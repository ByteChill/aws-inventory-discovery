"""Task handlers on a real Postgres and the simulated AWS (fake-aws + moto), driven by a real
`Worker`: paging checkpoints (stop, cancel, deletion), stats that survive failures, and the
serialized account access status.

Resource data comes only from the simulated services. The one resource the scan will not see
(to be marked missing) is another account's queue, read from the simulation and filed under the
scanned account: the seed tool's own technique.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import seed  # ty: ignore[import-not-found]
from sqlalchemy import Engine, text
from sqlmodel import Session, col, select

from app.accounts import soft_delete_account
from app.aws import cloudcontrol_source, config_source
from app.aws.findings import FindingsService
from app.core.config import Settings
from app.core.regions import GLOBAL_HOME, GLOBAL_REGION
from app.models import (
    Account,
    AccountScanRegion,
    FindingSummary,
    Resource,
    ScanRequest,
    ScanTask,
)
from app.models.resource import ResourceRelation
from app.models.scan import utcnow
from app.types.registry import TypeRegistry
from app.types.spec import CloudControlSource, Sources
from app.worker import leases, queue, requests
from app.worker.discovery import Discovery, Observation
from app.worker.errors import TaskLost
from app.worker.handlers import Scanner, TaskContext
from app.worker.main import Worker
from app.worker.writer import ResourceWriter
from tests.conftest import AWS_REGIONS, AwsEnv

REGISTRY = TypeRegistry.from_directory()
REGION = AWS_REGIONS[0]
QUEUE = "AWS::SQS::Queue"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aws"
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
    session: Session,
    account: str,
    kind: str,
    resource_type: str | None,
    source: str,
    resource_id: str | None = None,
) -> ScanTask:
    request = requests.create_sweep_request(
        session, [], trigger="api", priority=0, scope={"all": False}
    )
    assert request.id is not None
    (task,) = requests.add_tasks(
        session,
        request.id,
        [requests.NewTask(kind, account, REGION, resource_type, resource_id, source)],
    )
    request.status, request.started_at, request.finished_at = "queued", None, None
    session.add(request)
    session.commit()
    return task


def cancel_in_own_session(engine: Engine, scan_request_id: int) -> None:
    """Cancel from the worker thread without sharing the test thread's Session."""
    with Session(engine, expire_on_commit=False) as session:
        queue.cancel_request(session, scan_request_id)


def claimed_task(
    session: Session,
    account: str,
    kind: str,
    resource_type: str | None,
    source: str,
    resource_id: str | None = None,
) -> ScanTask:
    task = add_task(session, account, kind, resource_type, source, resource_id)
    claimed = queue.claim_task(session, "worker-old", per_account_concurrency=1000)
    assert claimed is not None and claimed.id == task.id
    return claimed


def reclaim_while_paused(session: Session, task: ScanTask) -> ScanTask:
    """Reap and re-claim `task` while its old handler is blocked before a write."""
    later = utcnow() + queue.HEARTBEAT_TIMEOUT + timedelta(seconds=1)
    assert queue.reap_lost_tasks(session, later).requeued == [task.id]
    fresh = queue.claim_task(session, "worker-new", per_account_concurrency=1000, now=later)
    assert fresh is not None and fresh.id == task.id
    return fresh


def pause_handler_across_reclaim(
    engine: Engine,
    scanner: Scanner,
    session: Session,
    task: ScanTask,
    handler: Callable[[ScanTask, TaskContext], dict[str, Any] | None],
    monkeypatch: pytest.MonkeyPatch,
    writer_method: str,
) -> tuple[ScanTask, BaseException | None]:
    """Run `handler` in a thread, block it just before `writer_method`, then reap and re-claim
    the task. The handler resumes after the new claim exists and can only be refused."""
    paused, resume = threading.Event(), threading.Event()
    real = getattr(scanner.writer, writer_method)

    def pause(*args: Any, **kwargs: Any) -> Any:
        paused.set()
        assert resume.wait(20)
        return real(*args, **kwargs)

    monkeypatch.setattr(scanner.writer, writer_method, pause)
    errors: list[BaseException] = []
    context = TaskContext(
        lambda: Session(engine, expire_on_commit=False), "worker-old", threading.Event()
    )

    def run() -> None:
        try:
            handler(task, context)
        except BaseException as exc:  # surfaced in the test thread below
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert paused.wait(20), f"handler never reached writer.{writer_method}"
    fresh = reclaim_while_paused(session, task)
    resume.set()
    thread.join(timeout=20)
    assert not thread.is_alive()
    return fresh, errors[0] if errors else None


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


def queue_config_item() -> dict[str, Any]:
    return json.loads((FIXTURES / "aws.sqs.queue" / "config_item.json").read_text())


def config_only(registry: TypeRegistry) -> TypeRegistry:
    """The same registry with SQS read through Config only, as some future types will be."""
    specs = {spec.type_name: spec for spec in registry}
    specs[QUEUE] = specs[QUEUE].model_copy(
        update={
            "sources": Sources(
                config=True, cloud_control=CloudControlSource(read=False, list=False)
            )
        }
    )
    return TypeRegistry(specs)


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
        scanner,
        monkeypatch,
        1,
        lambda: cancel_in_own_session(engine, task.scan_request_id),
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
        scanner,
        monkeypatch,
        2,
        lambda: cancel_in_own_session(engine, task.scan_request_id),
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ghost = ghost_queue(aws, account_id, engine, scanner)
    task = add_task(session, account_id, "shard", QUEUE, "cloudcontrol")
    # The scan queues a confirm_missing task, which this running worker would pick up at once
    # and use to delete the ghost; skip it so what is checked is what the scan itself left.
    # The whole chain is in tests/worker/test_end_to_end.py.
    monkeypatch.setitem(worker.handlers, "confirm_missing", lambda _task, _ctx: None)

    done = run_until_settled(worker, session, task)

    assert done.status == "succeeded"
    assert resources(session, account_id)[ghost].status == "missing"
    assert (done.stats or {})["new"] == 2 and (done.stats or {})["missing"] == 1
    request = session.get_one(ScanRequest, task.scan_request_id)
    assert (request.counts or {})["resources"]["missing"] == 1
    confirmation = session.exec(
        select(ScanTask).where(
            ScanTask.scan_request_id == task.scan_request_id, ScanTask.kind == "confirm_missing"
        )
    ).one()
    assert (confirmation.account_id, confirmation.resource_type) == (account_id, QUEUE)


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


# --- writes are fenced by the current claim, not just checked between pages -------------------


def test_a_reclaimed_shard_cannot_write_a_page(
    engine: Engine,
    session: Session,
    scanner: Scanner,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = claimed_task(session, account_id, "shard", QUEUE, "cloudcontrol")

    fresh, error = pause_handler_across_reclaim(
        engine, scanner, session, task, scanner.shard, monkeypatch, "write"
    )

    assert isinstance(error, TaskLost)
    assert resources(session, account_id) == {}
    assert fresh.claim_token != task.claim_token
    assert session.get_one(ScanTask, task.id).stats is None


def test_a_reclaimed_shard_cannot_mark_anything_missing(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ghost = ghost_queue(aws, account_id, engine, scanner)
    task = claimed_task(session, account_id, "shard", QUEUE, "cloudcontrol")

    fresh, error = pause_handler_across_reclaim(
        engine, scanner, session, task, scanner.shard, monkeypatch, "finish_scope"
    )

    assert isinstance(error, TaskLost)
    stored = resources(session, account_id)
    assert stored[ghost].status == "active"  # the deletion step never ran
    assert session.get_one(ScanTask, task.id).stats == {"new": 2}
    assert fresh.id == task.id


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


def test_a_reclaimed_resource_read_cannot_record_gone(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obs = (
        Discovery(aws.session(account_id), account_id, REGION, REGISTRY)
        .scan_live(QUEUE)
        .observations[0]
    )
    observed_at = utcnow() - timedelta(days=1)
    scanner.writer.write([replace(obs, observed_at=observed_at)])
    aws.admin(
        "DELETE",
        "/resources",
        params={"account_id": account_id, "region": REGION, "type_name": QUEUE,
                "identifier": obs.item.resource_id},
    )  # fmt: skip
    task = claimed_task(
        session, account_id, "resource", QUEUE, "cloudcontrol", obs.item.resource_id
    )

    fresh, error = pause_handler_across_reclaim(
        engine, scanner, session, task, scanner.resource, monkeypatch, "record_gone"
    )

    assert isinstance(error, TaskLost)
    row = resources(session, account_id)[obs.item.resource_id]
    assert (row.status, row.observed_at, row.last_task_id) == ("active", observed_at, None)
    assert fresh.id == task.id
    assert session.get_one(ScanTask, task.id).stats is None


def test_a_reclaimed_confirm_missing_cannot_count_a_failed_check(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obs = (
        Discovery(aws.session(account_id), account_id, REGION, REGISTRY)
        .scan_live(QUEUE)
        .observations[0]
    )
    written_at = utcnow() - timedelta(days=2)
    scanner.writer.write([replace(obs, observed_at=written_at)])
    scanner.writer.finish_scope(
        account_id=account_id,
        region=REGION,
        resource_type=QUEUE,
        seen_ids=[],
        observed_at=utcnow() - timedelta(days=1),
        complete=True,
    )
    aws.admin("POST", "/errors", json={"error": "AccessDenied", "operation": "GetResource"})
    task = claimed_task(session, account_id, "confirm_missing", QUEUE, "cloudcontrol")

    fresh, error = pause_handler_across_reclaim(
        engine, scanner, session, task, scanner.confirm_missing, monkeypatch, "record_check_failed"
    )

    assert isinstance(error, TaskLost)
    row = resources(session, account_id)[obs.item.resource_id]
    assert (row.status, row.missing_checks) == ("missing", 0)
    assert fresh.id == task.id
    assert session.get_one(ScanTask, task.id).stats is None


@pytest.mark.parametrize("ambiguous", [False, True])
def test_reclaimed_confirmation_repairs_relationships_after_committed_deletion(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    worker: Worker,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
    ambiguous: bool,
) -> None:
    function_type = "AWS::Lambda::Function"
    secret_type = "AWS::SecretsManager::Secret"
    sweep = Discovery(aws.session(account_id), account_id, REGION, REGISTRY).sweep_config(
        [function_type, secret_type]
    )
    (function_obs,) = sweep.by_type[function_type]
    (secret_obs,) = sweep.by_type[secret_type]
    written_at = utcnow() - timedelta(days=2)
    scanner.writer.write(
        [
            replace(function_obs, observed_at=written_at),
            replace(secret_obs, relationships=[], observed_at=written_at),
        ]
    )
    seen_ids = []
    surviving_id = None
    if ambiguous:
        duplicate = replace(
            function_obs,
            item=function_obs.item.model_copy(
                update={"resource_id": function_obs.item.resource_id + "-duplicate"}
            ),
            observed_at=written_at,
        )
        scanner.writer.write([duplicate])
        seen_ids.append(duplicate.item.resource_id)
        surviving_id = session.exec(
            select(Resource.id).where(
                Resource.resource_type == function_type,
                Resource.resource_id == duplicate.item.resource_id,
            )
        ).one()
    secret = session.exec(select(Resource).where(Resource.resource_type == secret_type)).one()
    secret_id = secret.id
    source_state = (secret.observed_at, secret.last_changed_at, secret.content_hash)

    def edge_target() -> int | None:
        session.expire_all()
        return session.exec(
            select(ResourceRelation.to_resource_id).where(
                ResourceRelation.from_resource_id == secret_id
            )
        ).one()

    before_target = edge_target()
    assert (before_target is None) == ambiguous
    scanner.writer.finish_scope(
        account_id=account_id,
        region=REGION,
        resource_type=function_type,
        seen_ids=seen_ids,
        observed_at=utcnow() - timedelta(days=1),
        complete=True,
    )
    aws.admin(
        "DELETE",
        "/resources",
        params={
            "account_id": account_id,
            "region": REGION,
            "type_name": function_type,
            "identifier": function_obs.item.resource_id,
        },
    )
    task = claimed_task(session, account_id, "confirm_missing", function_type, "cloudcontrol")
    resolve = scanner.writer._resolve_dangling_relations
    fresh, error = pause_handler_across_reclaim(
        engine,
        scanner,
        session,
        task,
        scanner.confirm_missing,
        monkeypatch,
        "_resolve_dangling_relations",
    )
    assert isinstance(error, TaskLost)
    deleted = session.exec(
        select(Resource).where(
            Resource.resource_type == function_type,
            Resource.resource_id == function_obs.item.resource_id,
        )
    ).one()
    assert deleted.status == "deleted"
    assert deleted.last_task_id == task.id
    assert edge_target() == before_target
    assert state(session, fresh).stats == {"deleted": 1}
    monkeypatch.setattr(scanner.writer, "_resolve_dangling_relations", resolve)

    def unexpected_read(*args: Any, **kwargs: Any) -> None:
        pytest.fail("reconciliation retry must not fetch the deleted target from AWS")

    monkeypatch.setattr(Discovery, "fetch", unexpected_read)
    worker._execute(fresh)

    assert state(session, fresh).status == "succeeded"
    assert edge_target() == surviving_id
    stats = state(session, fresh).stats or {}
    assert (stats["deleted"], stats["checked"]) == (1, 0)
    session.refresh(secret)
    assert (secret.observed_at, secret.last_changed_at, secret.content_hash) == source_state


def test_config_confirm_missing_uses_the_item_capture_time_as_its_observation(
    aws: AwsEnv,
    engine: Engine,
    session: Session,
    scanner: Scanner,
    account_id: str,
) -> None:
    captured = datetime(2026, 11, 6, 9, 15, 22, 401000, tzinfo=UTC)
    obs = (
        Discovery(aws.session(account_id), account_id, REGION, REGISTRY)
        .scan_live(QUEUE)
        .observations[0]
    )
    scanner.writer.write([replace(obs, observed_at=captured - timedelta(days=1))])
    scanner.writer.finish_scope(
        account_id=account_id,
        region=REGION,
        resource_type=QUEUE,
        seen_ids=[],
        observed_at=captured - timedelta(hours=1),
        complete=True,
    )
    item = queue_config_item()
    item["resourceId"] = obs.item.resource_id  # the fixture's account id, remapped like seed
    aws.admin(
        "PUT",
        "/resources",
        json={
            "account_id": account_id,
            "region": REGION,
            "type_name": QUEUE,
            "config_item": {
                **item,
                "configurationItemStatus": "ResourceDeleted",
                "configurationItemCaptureTime": captured.isoformat(),
            },
        },
    )
    registry = config_only(REGISTRY)
    config_scanner = Scanner(
        scanner.settings,
        registry,
        ResourceWriter(engine, registry, scanner.settings),
        aws.credential_service(),
    )
    task = claimed_task(session, account_id, "confirm_missing", QUEUE, "config")
    context = TaskContext(
        lambda: Session(engine, expire_on_commit=False), "worker-1", threading.Event()
    )

    stats = config_scanner.confirm_missing(task, context)

    row = resources(session, account_id)[obs.item.resource_id]
    assert (row.status, row.deleted_at, row.observed_at) == ("deleted", captured, captured)
    assert (stats or {})["deleted"] == 1


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


# --- enqueue-time dedupe of scheduled children (scanner.md §5.1) -----------------------------


def run_sweep_account(
    engine: Engine, session: Session, scanner: Scanner, now: datetime
) -> ScanTask:
    """Claim the next sweep_account (the children of earlier sweeps are not due yet) and run
    its handler to completion, the way the worker would."""
    task = queue.claim_task(session, "worker-1", per_account_concurrency=1000, now=now)
    assert task is not None and task.kind == "sweep_account"
    context = TaskContext(
        lambda: Session(engine, expire_on_commit=False), "worker-1", threading.Event()
    )
    stats = scanner.sweep_account(task, context)
    assert task.id is not None
    assert queue.complete_task(session, task.id, task.claim_token or "", stats=stats, now=now)
    return state(session, task)


def sweep_request(session: Session, account: str, priority: int, now: datetime) -> ScanRequest:
    request = requests.create_sweep_request(
        session,
        [account],
        trigger="schedule" if priority else "api",
        priority=priority,
        scope={"all": bool(priority)},
        now=now,
    )
    session.commit()
    return request


def queued_children(session: Session, priority: int) -> list[ScanTask]:
    session.expire_all()
    return list(
        session.exec(
            select(ScanTask).where(
                col(ScanTask.kind).in_(("config_group", "shard")),
                ScanTask.status == "queued",
                ScanTask.priority == priority,
            )
        )
    )


def test_a_second_scheduled_sweep_does_not_duplicate_children_still_queued_but_on_demand_does(
    engine: Engine, session: Session, scanner: Scanner, account_id: str
) -> None:
    long_ago = utcnow() - timedelta(days=2)  # before the children's own not_before
    first = sweep_request(session, account_id, 1, long_ago)
    second = sweep_request(session, account_id, 1, long_ago + timedelta(minutes=1))

    # Nobody runs the children: they pile up as a backlog.
    planned = run_sweep_account(engine, session, scanner, long_ago + timedelta(hours=1))
    backlog = queued_children(session, 1)
    assert planned.scan_request_id == first.id and (planned.stats or {})["tasks"] == len(backlog)
    assert len(backlog) > 0

    replanned = run_sweep_account(engine, session, scanner, long_ago + timedelta(hours=2))

    assert replanned.scan_request_id == second.id
    assert (replanned.stats or {})["tasks"] == 0
    assert (replanned.stats or {})["skipped"] == len(backlog)
    scopes = [(t.kind, t.region, t.resource_type, t.source) for t in queued_children(session, 1)]
    assert len(scopes) == len(backlog) == len(set(scopes))

    on_demand = sweep_request(session, account_id, 0, long_ago + timedelta(hours=2, minutes=30))
    fresh = run_sweep_account(engine, session, scanner, long_ago + timedelta(hours=3))

    assert fresh.scan_request_id == on_demand.id
    assert (fresh.stats or {})["tasks"] > 0
    assert len(queued_children(session, 0)) == (fresh.stats or {})["tasks"]
    assert len(queued_children(session, 1)) == len(backlog)  # the scheduled ones are untouched


def test_sweep_account_obeys_the_selected_region_policy_and_an_explicit_override(
    engine: Engine, session: Session, scanner: Scanner, account_id: str
) -> None:
    other_region = AWS_REGIONS[1]
    account = session.get_one(Account, account_id)
    account.scan_region_mode = "selected"
    session.add(account)
    session.add(AccountScanRegion(account_id=account_id, region=REGION))
    session.commit()

    now = utcnow()
    request = requests.create_sweep_request(
        session,
        [account_id],
        trigger="api",
        priority=0,
        scope={"all": False, "awsRegions": [other_region]},
        now=now,
    )
    session.commit()
    task = run_sweep_account(engine, session, scanner, now)
    assert task.scan_request_id == request.id
    assert (task.stats or {})["regions"] == 0
    assert [
        child for child in queued_children(session, 0) if child.scan_request_id == request.id
    ] == []

    overridden = requests.create_sweep_request(
        session,
        [account_id],
        trigger="api",
        priority=0,
        scope={
            "all": False,
            "awsRegions": [other_region],
            "overrideScanPolicy": True,
        },
        now=now + timedelta(minutes=1),
    )
    session.commit()
    task = run_sweep_account(engine, session, scanner, now + timedelta(minutes=1))
    assert task.scan_request_id == overridden.id
    assert (task.stats or {})["regions"] == 1
    assert {
        child.region
        for child in queued_children(session, 0)
        if child.scan_request_id == overridden.id
    } == {other_region}


def test_selected_policy_scans_global_types_only_when_the_home_region_is_selected(
    engine: Engine, session: Session, scanner: Scanner, account_id: str
) -> None:
    other_region = next(region for region in AWS_REGIONS if region != GLOBAL_HOME)
    account = session.get_one(Account, account_id)
    account.scan_region_mode = "selected"
    session.add(account)
    session.add(AccountScanRegion(account_id=account_id, region=other_region))
    now = utcnow()
    request = requests.create_sweep_request(
        session,
        [account_id],
        trigger="api",
        priority=1,
        scope={"all": False},
        now=now,
    )
    session.commit()

    task = run_sweep_account(engine, session, scanner, now)

    assert task.scan_request_id == request.id
    assert (task.stats or {})["regions"] == 1
    children = [
        child for child in queued_children(session, 1) if child.scan_request_id == request.id
    ]
    assert children
    assert GLOBAL_REGION not in {child.region for child in children}


def test_a_stopped_account_queued_before_the_change_creates_no_region_tasks(
    engine: Engine,
    session: Session,
    scanner: Scanner,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_credentials(_task: ScanTask) -> None:
        pytest.fail("a stopped account must not ask the credential service")

    monkeypatch.setattr(scanner, "_aws", unexpected_credentials)
    account = session.get_one(Account, account_id)
    account.scan_enabled = False
    session.add(account)
    now = utcnow()
    request = requests.create_sweep_request(
        session,
        [account_id],
        trigger="api",
        priority=0,
        scope={"all": False},
        now=now,
    )
    session.commit()

    task = run_sweep_account(engine, session, scanner, now)

    assert task.scan_request_id == request.id
    assert (task.stats or {})["regions"] == 0
    assert [
        child for child in queued_children(session, 0) if child.scan_request_id == request.id
    ] == []


def test_a_deleted_account_is_not_scanned_even_when_the_task_overrides_policy(
    engine: Engine,
    session: Session,
    scanner: Scanner,
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_credentials(_task: ScanTask) -> None:
        pytest.fail("a deleted account must not ask the credential service")

    monkeypatch.setattr(scanner, "_aws", unexpected_credentials)
    soft_delete_account(session, account_id)
    now = utcnow()
    request = requests.create_sweep_request(
        session,
        [account_id],
        trigger="api",
        priority=0,
        scope={"all": False, "overrideScanPolicy": True},
        now=now,
    )
    session.commit()

    task = run_sweep_account(engine, session, scanner, now)

    assert task.scan_request_id == request.id
    assert (task.stats or {})["regions"] == 0
    assert [
        child for child in queued_children(session, 0) if child.scan_request_id == request.id
    ] == []


def test_a_scan_stores_the_findings_summary_of_the_images_it_wrote(
    aws: AwsEnv, engine: Engine, make_settings: Callable[..., Settings], account_id: str
) -> None:
    levels = json.loads((FIXTURES.parent / "findings" / "ami_findings.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        image_id = request.url.path.split("/")[2]
        if image_id == "ami-high":
            return httpx.Response(200, json=levels)
        return httpx.Response(
            200,
            json={"counts": {"critical": 0, "high": 0, "medium": 0, "low": 0}, "maxScore": None},
        )

    settings = make_settings(REACHABLE_REGIONS=AWS_REGIONS)
    findings = FindingsService(
        "http://findings",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    scanner = Scanner(
        settings,
        REGISTRY,
        ResourceWriter(engine, REGISTRY, settings),
        aws.credential_service(),
        findings=findings,
    )
    with Session(engine, expire_on_commit=False) as session:
        task = claimed_task(session, account_id, "shard", "AWS::EC2::Image", "cloudcontrol")
        state = leases.try_acquire(session, task, leases.Scope.of(task))
        assert state is leases.LeaseState.ACQUIRED

    scanner._sync_findings(task, ["ami-high", "ami-clean"])

    with Session(engine) as session:
        rows = {
            row.resource_id: (row.high_count, row.max_score, row.critical_count)
            for row in session.exec(
                select(FindingSummary).where(FindingSummary.account_id == account_id)
            )
        }
    assert rows == {
        "ami-high": (levels["counts"]["high"], levels["maxScore"], levels["counts"]["critical"]),
        "ami-clean": (0, None, 0),
    }


def test_findings_written_by_a_worker_that_lost_its_claim_are_refused(
    aws: AwsEnv, engine: Engine, make_settings: Callable[..., Settings], account_id: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"counts": {"critical": 0, "high": 1, "medium": 0, "low": 0}, "maxScore": 7.2}
        )

    settings = make_settings(REACHABLE_REGIONS=AWS_REGIONS)
    findings = FindingsService(
        "http://findings",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    scanner = Scanner(
        settings,
        REGISTRY,
        ResourceWriter(engine, REGISTRY, settings),
        aws.credential_service(),
        findings=findings,
    )
    with Session(engine, expire_on_commit=False) as session:
        task = claimed_task(session, account_id, "shard", "AWS::EC2::Image", "cloudcontrol")
        state = leases.try_acquire(session, task, leases.Scope.of(task))
        assert state is leases.LeaseState.ACQUIRED
        fresh = reclaim_while_paused(session, task)
        assert fresh.claim_token != task.claim_token

    with pytest.raises(TaskLost):
        scanner._sync_findings(task, ["ami-reclaimed"])

    with Session(engine) as session:
        assert (
            session.get(FindingSummary, (account_id, REGION, "AWS::EC2::Image", "ami-reclaimed"))
            is None
        )
