"""Task handlers on a real Postgres and the simulated AWS (fake-aws + moto), driven by a real
`Worker`: paging checkpoints (stop, cancel, deletion), stats that survive failures, and the
serialized account access status.

Resource data comes only from the simulated services. The one resource the scan will not see
(to be marked missing) is another account's queue, read from the simulation and filed under the
scanned account: the seed tool's own technique.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
import seed  # ty: ignore[import-not-found]
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from app.aws import cloudcontrol_source, config_source
from app.core.config import Settings
from app.models import Account, Resource, ScanRequest, ScanTask
from app.models.scan import utcnow
from app.types.registry import TypeRegistry
from app.worker import queue, requests
from app.worker.discovery import Discovery, Observation
from app.worker.handlers import Scanner
from app.worker.main import Worker
from app.worker.writer import ResourceWriter
from tests.conftest import AWS_REGIONS, AwsEnv

REGISTRY = TypeRegistry.from_directory()
REGION = AWS_REGIONS[0]
QUEUE = "AWS::SQS::Queue"
FINAL = ("succeeded", "failed", "cancelled", "skipped")


@pytest.fixture
def account_id(aws: AwsEnv, session: Session) -> str:
    account = aws.account_ids[0]
    entry = next(e for e in seed.build_registry(aws.account_ids) if e["account_id"] == account)
    session.add(Account(**entry, registry_fetched_at=utcnow()))
    session.commit()
    return account


@pytest.fixture
def scanner(
    aws: AwsEnv, engine: Engine, make_settings: Callable[..., Settings], account_id: str
) -> Scanner:
    settings = make_settings(REACHABLE_REGIONS=AWS_REGIONS, MISSING_GUARD_MIN=50)
    writer = ResourceWriter(engine, REGISTRY, settings)
    return Scanner(settings, REGISTRY, writer, aws.credential_service())


@pytest.fixture
def worker(engine: Engine, scanner: Scanner) -> Iterator[Worker]:
    settings = scanner.settings.model_copy(
        update={"WORKER_THREADS": 2, "WORKER_ONDEMAND_THREADS": 1}
    )
    worker = Worker(
        engine,
        settings,
        handlers=scanner.handlers(),
        after_settle=scanner.settled,
        poll_interval=0.05,
        heartbeat_interval=0.2,
    )
    yield worker
    worker.stop()
    worker.join(timeout=10)


@pytest.fixture(autouse=True)
def one_item_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """One resource per page: every listing of the fixtures spans several pages."""
    monkeypatch.setattr(cloudcontrol_source, "PAGE_SIZE", 1)
    monkeypatch.setattr(config_source, "PAGE_SIZE", 1)


def add_task(
    session: Session, account: str, kind: str, resource_type: str | None, source: str
) -> ScanTask:
    request = requests.create_sweep_request(
        session, [], trigger="api", priority=0, scope={"all": False}
    )
    assert request.id is not None
    (task,) = requests.add_tasks(
        session,
        request.id,
        [requests.NewTask(kind, account, REGION, resource_type, None, source)],
    )
    request.status, request.started_at, request.finished_at = "queued", None, None
    session.add(request)
    session.commit()
    return task


def wait_for(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def state(session: Session, task: ScanTask) -> ScanTask:
    session.expire_all()
    assert task.id is not None
    return session.get_one(ScanTask, task.id)


def run_until_settled(worker: Worker, session: Session, task: ScanTask) -> ScanTask:
    worker.start()
    wait_for(lambda: state(session, task).status in FINAL)
    return state(session, task)


def after_nth_write(
    scanner: Scanner, monkeypatch: pytest.MonkeyPatch, nth: int, action: Callable[[], object]
) -> None:
    """Run `action` right after the writer committed its `nth` write."""
    real = scanner.writer.write
    calls: list[int] = []

    def write(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == nth:
            action()
        return result

    monkeypatch.setattr(scanner.writer, "write", write)


def ghost_queue(aws: AwsEnv, account: str, engine: Engine, scanner: Scanner) -> str:
    """A queue of the scanned account that the next scan will not list: another account's
    queue from the simulation, filed under this account. Returns its resource id."""
    other = aws.account_ids[2]  # present in every dataset the other tests leave behind
    other_queue = Discovery(aws.session(other), other, REGION, REGISTRY).scan_live(QUEUE)
    obs: Observation = other_queue.observations[0]
    item = obs.item.model_copy(update={"account_id": account})
    scanner.writer.write([replace(obs, item=item, observed_at=utcnow() - timedelta(hours=1))])
    return item.resource_id


def resources(session: Session, account: str) -> dict[str, Resource]:
    session.expire_all()
    found = session.exec(
        select(Resource).where(Resource.account_id == account, Resource.resource_type == QUEUE)
    ).all()
    return {r.resource_id: r for r in found}


# --- paging checkpoints (item 3) -------------------------------------------------------------


def test_cancel_while_paging_stops_after_the_current_page_and_deletes_nothing(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ghost = ghost_queue(aws, account_id, engine, scanner)
    task = add_task(session, account_id, "shard", QUEUE, "cloudcontrol")
    after_nth_write(
        scanner, monkeypatch, 1, lambda: queue.cancel_request(session, task.scan_request_id)
    )

    done = run_until_settled(worker, session, task)

    assert done.status == "cancelled"
    stored = resources(session, account_id)
    assert len(stored) == 2  # the ghost and exactly the first page (one of two queues)
    assert stored[ghost].status == "active"
    assert done.stats == {"new": 1}  # what was written before the stop is accounted for


def test_cancel_during_the_last_page_skips_the_deletion_judgement(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ghost = ghost_queue(aws, account_id, engine, scanner)
    task = add_task(session, account_id, "shard", QUEUE, "cloudcontrol")
    after_nth_write(
        scanner, monkeypatch, 2, lambda: queue.cancel_request(session, task.scan_request_id)
    )

    done = run_until_settled(worker, session, task)

    assert done.status == "cancelled"
    stored = resources(session, account_id)
    assert len(stored) == 3 and stored[ghost].status == "active"  # nothing marked missing


def test_a_complete_scan_marks_the_unseen_resource_missing_and_counts_it(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
) -> None:
    ghost = ghost_queue(aws, account_id, engine, scanner)
    task = add_task(session, account_id, "shard", QUEUE, "cloudcontrol")

    done = run_until_settled(worker, session, task)

    assert done.status == "succeeded"
    assert resources(session, account_id)[ghost].status == "missing"
    assert (done.stats or {})["new"] == 2 and (done.stats or {})["missing"] == 1
    request = session.get_one(ScanRequest, task.scan_request_id)
    assert (request.counts or {})["resources"]["missing"] == 1


def test_a_graceful_stop_requeues_the_task_and_keeps_the_pages_already_written(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ghost = ghost_queue(aws, account_id, engine, scanner)
    task = add_task(session, account_id, "shard", QUEUE, "cloudcontrol")
    after_nth_write(scanner, monkeypatch, 1, worker.stop)  # SIGTERM arrives after page one

    worker.start()
    assert worker.join(timeout=20)

    stopped = state(session, task)
    assert (stopped.status, stopped.attempt, stopped.finished_at) == ("queued", 0, None)
    stored = resources(session, account_id)
    assert len(stored) == 2 and stored[ghost].status == "active"
    assert stopped.stats == {"new": 1}
    lease = (
        session.connection()
        .execute(
            text("SELECT lease_task_id FROM t_inv_aws_scan_shard WHERE resource_type = :t"),
            {"t": QUEUE},
        )
        .scalar()
    )
    assert lease is None  # released for whoever takes the task next


# --- stats survive failures and retries (item 4) --------------------------------------------


def test_increments_of_pages_written_before_a_failure_reach_the_request_counts(
    aws: AwsEnv,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = add_task(session, account_id, "shard", QUEUE, "cloudcontrol")
    after_nth_write(
        scanner,
        monkeypatch,
        1,
        lambda: aws.admin(
            "POST", "/errors", json={"error": "AccessDenied", "operation": "ListResources"}
        ),
    )

    done = run_until_settled(worker, session, task)

    assert (done.status, done.error_code) == ("failed", "access_denied")
    assert done.stats == {"new": 1}
    request = session.get_one(ScanRequest, task.scan_request_id)
    assert (request.counts or {})["resources"]["new"] == 1  # not lost with the exception


def test_config_group_keeps_its_increments_when_a_later_page_fails(
    aws: AwsEnv,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = add_task(session, account_id, "config_group", None, "config")
    after_nth_write(
        scanner,
        monkeypatch,
        2,
        lambda: aws.admin(
            "POST", "/errors", json={"error": "AccessDenied", "operation": "SelectResourceConfig"}
        ),
    )

    done = run_until_settled(worker, session, task)

    assert (done.status, done.error_code) == ("failed", "access_denied")
    assert (done.stats or {})["new"] == 2
    assert len(session.exec(select(Resource)).all()) == 2


def test_a_partial_confirm_missing_counts_the_resources_it_settled(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own = Discovery(aws.session(account_id), account_id, REGION, REGISTRY).scan_live(QUEUE)
    scanner.writer.write(
        [replace(o, observed_at=utcnow() - timedelta(hours=2)) for o in own.observations]
    )
    scanner.writer.finish_scope(
        account_id=account_id,
        region=REGION,
        resource_type=QUEUE,
        seen_ids=[],
        observed_at=utcnow() - timedelta(hours=1),
        complete=True,
    )  # both queues are missing, with their first piece of evidence
    task = add_task(session, account_id, "confirm_missing", QUEUE, "cloudcontrol")
    after_nth_write(  # the first one is found again, the second read is refused
        scanner,
        monkeypatch,
        1,
        lambda: aws.admin(
            "POST", "/errors", json={"error": "AccessDenied", "operation": "GetResource"}
        ),
    )

    done = run_until_settled(worker, session, task)

    assert (done.status, done.error_code) == ("failed", "access_denied")
    assert (done.stats or {})["restored"] == 1
    request = session.get_one(ScanRequest, task.scan_request_id)
    assert (request.counts or {})["tasks"] == {"failed": 1}


# --- account access status (item 7) ---------------------------------------------------------


def request_task(  # a shard task claimed and settled as the worker would
    session: Session, request_id: int, account: str, region: str, error_code: str | None
) -> ScanTask:
    (task,) = requests.add_tasks(
        session,
        request_id,
        [requests.NewTask("shard", account, region, QUEUE, None, "cloudcontrol")],
    )
    claimed = queue.claim_task(session, "w", per_account_concurrency=1000)
    assert claimed is not None and claimed.id == task.id and task.id is not None
    if error_code is None:
        assert queue.complete_task(session, task.id, claimed.claim_token or "", {})
    else:
        assert queue.fail_task(session, claimed, error_code, "boom", retryable=False) == "failed"
    return claimed


def test_two_tasks_of_one_account_settling_together_both_count_in_its_access_status(
    engine: Engine, session: Session, scanner: Scanner, account_id: str
) -> None:
    request = requests.create_sweep_request(
        session, [], trigger="api", priority=0, scope={"all": False}
    )
    assert request.id is not None
    denied = request_task(session, request.id, account_id, REGION, "access_denied")
    healthy = request_task(session, request.id, account_id, AWS_REGIONS[1], None)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def settle(task: ScanTask) -> None:
        try:
            barrier.wait(timeout=5)
            scanner.settled(task)
        except BaseException as exc:  # surfaced in the main thread below
            errors.append(exc)

    threads = [threading.Thread(target=settle, args=(t,)) for t in (denied, healthy)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    session.expire_all()
    account = session.get_one(Account, account_id)
    assert account.access_status == "partial"  # neither task's view hid the other
    assert account.last_scan_ok_at is None  # never advanced by a half-seen "ok"
    assert account.access_error == "boom"


def test_the_access_status_waits_for_a_concurrent_update_of_the_same_account(
    engine: Engine, session: Session, scanner: Scanner, account_id: str
) -> None:
    request = requests.create_sweep_request(
        session, [], trigger="api", priority=0, scope={"all": False}
    )
    assert request.id is not None
    task = request_task(session, request.id, account_id, REGION, None)
    finished = threading.Event()

    def refresh() -> None:
        scanner.settled(task)
        finished.set()

    with engine.begin() as holder:  # another settling task holds the account row
        holder.execute(
            text("SELECT 1 FROM t_inv_aws_account WHERE account_id = :a FOR UPDATE"),
            {"a": account_id},
        )
        thread = threading.Thread(target=refresh)
        thread.start()
        assert not finished.wait(0.5)  # serialized per account
    assert finished.wait(10)
    thread.join(timeout=5)
    session.expire_all()
    assert session.get_one(Account, account_id).access_status == "ok"
