import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import httpx
import psycopg
import pytest
import seed  # ty: ignore[import-not-found]
import uvicorn
from alembic import command
from alembic.config import Config
from fake_aws.app import create_app  # ty: ignore[import-not-found]
from fastapi.testclient import TestClient
from moto.server import ThreadedMotoServer
from psycopg import sql

from app.aws.credentials import CredentialService
from app.aws.session import make_session
from app.core.config import get_settings
from app.core.db import get_engine, get_read_engine, normalise_url

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://inventory:inventory@localhost:55432/inventory",
)
BACKEND_DIR = Path(__file__).resolve().parents[1]
# A port nothing listens on, for "the dependency is down" cases.
NOWHERE = "localhost:1"


def clear_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_read_engine.cache_clear()


def libpq_url(url: str) -> str:
    return normalise_url(url).replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def schema_name() -> Iterator[str]:
    """A unique throwaway schema with the migrations applied, dropped afterwards."""
    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(libpq_url(TEST_DATABASE_URL), autocommit=True)
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", TEST_DATABASE_URL)
        mp.setenv("DATABASE_SCHEMA", schema)
        clear_caches()
        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
        try:
            yield schema
        finally:
            clear_caches()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            admin.close()


@pytest.fixture
def db(schema_name: str) -> Iterator[psycopg.Connection]:
    """A plain connection to the migrated test schema, for inspecting the catalog."""
    with psycopg.connect(
        libpq_url(TEST_DATABASE_URL),
        options=f"-csearch_path={schema_name}",
        autocommit=True,
    ) as connection:
        yield connection


@pytest.fixture
def make_client(
    schema_name: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., TestClient]]:
    """Build an API client against the test schema with the given env overrides."""

    def build(**env: str) -> TestClient:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        monkeypatch.setenv("DATABASE_SCHEMA", schema_name)
        monkeypatch.setenv("REDIS_URL", f"redis://{NOWHERE}/0")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        clear_caches()
        from app.main import app

        return TestClient(app)

    yield build
    clear_caches()


# --- in-process AWS simulation (moto + devtools fake_aws) ---------------------------------

AWS_REGIONS = ["us-east-1", "eu-west-2"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class AwsEnv:
    fake_url: str
    moto_url: str
    account_ids: list[str]
    dataset: dict[str, Any]

    def admin(self, method: str, path: str, **kwargs: Any) -> Any:
        response = httpx.request(method, f"{self.fake_url}/_admin{path}", **kwargs)
        response.raise_for_status()
        return response.json()

    def reload(self) -> None:
        self.admin("PUT", "/dataset", params={"reset": True}, json=self.dataset)

    def credential_service(self) -> CredentialService:
        return CredentialService(f"{self.fake_url}/credentials", backoff=0.01)

    def session(self, account_id: str) -> boto3.Session:
        return make_session(self.credential_service().credentials_for(account_id))


@pytest.fixture(scope="session")
def aws_servers() -> Iterator[AwsEnv]:
    moto_port, fake_port = _free_port(), _free_port()
    # moto ignores ExecutableUsers=["self"] and returns its ~100 built-in public AMIs;
    # without them a listing shows only what the test accounts registered.
    os.environ["MOTO_EC2_LOAD_DEFAULT_AMIS"] = "false"
    moto = ThreadedMotoServer(port=moto_port, verbose=False)
    moto.start()
    moto_url = f"http://127.0.0.1:{moto_port}"
    fake_url = f"http://127.0.0.1:{fake_port}"
    server = uvicorn.Server(
        uvicorn.Config(create_app(moto_url), port=fake_port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    ids = seed.account_ids(7)  # [2] has Config off in eu-west-2; [6] records only 3 types
    env = AwsEnv(fake_url, moto_url, ids, seed.build_dataset(ids, AWS_REGIONS))
    env.reload()
    seed.create_amis(fake_url, moto_url, ids, AWS_REGIONS)
    with pytest.MonkeyPatch.context() as mp:
        # boto3's own endpoint variables: the only switch between real AWS and the simulation
        mp.setenv("AWS_ENDPOINT_URL", moto_url)
        mp.setenv("AWS_ENDPOINT_URL_CLOUDCONTROL", fake_url)
        mp.setenv("AWS_ENDPOINT_URL_CONFIG_SERVICE", fake_url)
        try:
            yield env
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            moto.stop()


@pytest.fixture
def aws(aws_servers: AwsEnv) -> AwsEnv:
    """The simulation reset to the seed dataset with no injected errors."""
    aws_servers.reload()
    return aws_servers
