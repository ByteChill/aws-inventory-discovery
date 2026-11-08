import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql

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
