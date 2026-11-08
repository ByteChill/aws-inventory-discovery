import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlmodel import Session

from app.aws.account_registry import AccountRegistryClient, RegistryAccount
from app.core.config import Settings
from app.core.db import make_engine
from app.models import ScanShard, ScanTask
from app.worker import leases, queue, requests
from app.worker.scheduler import Scheduler, sync_accounts
from tests.conftest import TEST_DATABASE_URL

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "registry"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
WORKER = "worker-1"
# The account ids of tests/fixtures/registry/accounts.json
PAYMENTS_PROD, IMAGES, PAYMENTS_DEV = "111122223333", "444455556666", "777788889999"
REGION, TYPE = "ap-east-1", "AWS::SQS::Queue"


class RegistryServer:
    """A real HTTP endpoint serving a registry fixture file, with scriptable failures."""

    def __init__(self) -> None:
        self.file = "accounts.json"
        self.failures: list[int] = []
        self.always_fail: int | None = None
        self.request_count = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.request_count += 1
                status = owner.always_fail or (owner.failures.pop(0) if owner.failures else 200)
                body = (FIXTURES / owner.file).read_bytes() if status == 200 else b"{}"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/accounts"

    def client(self) -> AccountRegistryClient:
        return AccountRegistryClient(self.url, sleep=lambda _seconds: None)


@pytest.fixture(scope="session")
def engine(schema_name: str) -> Iterator[Engine]:
    engine = make_engine(TEST_DATABASE_URL, schema_name)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE t_inv_aws_scan_task, t_inv_aws_scan_request, t_inv_aws_scan_shard,"
                " t_inv_aws_resource, t_inv_aws_account_region, t_inv_aws_account"
                " RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def registry() -> Iterator[RegistryServer]:
    server = RegistryServer()
    server.thread.start()
    yield server
    server.server.shutdown()
    server.server.server_close()


@pytest.fixture
def make_settings(schema_name: str, registry: RegistryServer) -> Callable[..., Settings]:
    def build(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "DATABASE_URL": TEST_DATABASE_URL,
            "DATABASE_SCHEMA": schema_name,
            "ACCOUNT_REGISTRY_URL": registry.url,
            "PER_ACCOUNT_CONCURRENCY": 1000,
            **overrides,
        }
        return Settings(_env_file=None, **values)  # ty: ignore[invalid-argument-type]

    return build


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
def scheduler(engine: Engine, settings: Settings, registry: RegistryServer) -> Iterator[Scheduler]:
    scheduler = Scheduler(engine, settings, registry.client())
    yield scheduler
    scheduler.lock.release()


@pytest.fixture
def accounts(session: Session, registry: RegistryServer) -> list[RegistryAccount]:
    """The accounts of the registry fixture file, fetched over HTTP and synced like a sweep."""
    fetched = registry.client().fetch_accounts()
    sync_accounts(session, fetched, NOW - timedelta(days=1))
    return fetched


# -- helpers: everything goes through the public service functions ---------------------------


def refresh(
    session: Session, resource_id: str, account_id: str = PAYMENTS_DEV, now: datetime = NOW
) -> ScanTask:
    key = requests.ResourceKey(account_id, REGION, TYPE, resource_id)
    return requests.request_resource_refresh(session, key, now=now).task


def claim(session: Session, now: datetime = NOW, **kwargs: int) -> ScanTask | None:
    return queue.claim_task(session, WORKER, per_account_concurrency=1000, now=now, **kwargs)


def claim_one(session: Session, now: datetime = NOW, **kwargs: int) -> ScanTask:
    task = claim(session, now, **kwargs)
    assert task is not None, "expected a claimable task"
    return task


def scheduled_shard(session: Session, now: datetime = NOW) -> ScanTask:
    """A scheduled (priority 1) shard task on the test scope, added to a fresh sweep request."""
    # A scheduled request without sweep_account tasks of its own, so only the shard is claimable.
    request = requests.create_sweep_request(
        session, [], trigger="schedule", priority=1, scope={"all": True}, now=now
    )
    assert request.id is not None
    (task,) = requests.add_tasks(
        session,
        request.id,
        [requests.NewTask("shard", PAYMENTS_DEV, REGION, TYPE, source="cloudcontrol")],
        now=now,
    )
    return task


def shard_state(session: Session) -> ScanShard:
    session.expire_all()
    return session.get_one(ScanShard, (PAYMENTS_DEV, REGION, TYPE))


def task_state(session: Session, task: ScanTask) -> ScanTask:
    session.expire_all()
    assert task.id is not None
    return session.get_one(ScanTask, task.id)


def lease_scope() -> leases.Scope:
    return leases.Scope(PAYMENTS_DEV, REGION, TYPE)
