import os
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app import cli
from app.aws.account_registry import RegistryAccount
from app.core.config import Settings
from app.models import ScanTask
from app.models.scan import utcnow
from app.worker import leases, queue
from app.worker.errors import TaskError
from app.worker.handlers import TaskContext
from app.worker.main import Worker
from app.worker.scheduler import Scheduler
from tests.conftest import clear_caches
from tests.worker.conftest import NOW, refresh, scheduled_shard, shard_state, task_state


def wait_for(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the worker"
        time.sleep(0.05)


def make_worker(
    engine: Engine,
    make_settings: Callable[..., Settings],
    handlers: dict[str, Any],
    threads: int = 2,
    on_demand: int = 1,
) -> Worker:
    settings = make_settings(WORKER_THREADS=threads, WORKER_ONDEMAND_THREADS=on_demand)
    return Worker(engine, settings, handlers=handlers, poll_interval=0.05, heartbeat_interval=0.2)


def test_stopping_the_worker_requeues_the_running_task_without_an_attempt_bump(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    accounts: list[RegistryAccount],
) -> None:
    task = refresh(session, "queue-1")
    started = []

    def slow_scan(task: ScanTask, ctx: TaskContext) -> None:
        started.append(task.id)
        while True:  # a long scan that checks in after every page
            time.sleep(0.02)
            ctx.checkpoint(task)

    worker = make_worker(engine, make_settings, {"resource": slow_scan})
    worker.start()
    wait_for(lambda: bool(started))

    worker.stop()  # what SIGTERM does
    assert worker.join(timeout=5)

    stopped = task_state(session, task)
    assert (stopped.status, stopped.attempt, stopped.worker_id) == ("queued", 0, None)
    assert stopped.finished_at is None


def test_non_retryable_errors_fail_without_retries_and_scheduled_work_waits_for_other_threads(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    scheduler: Scheduler,
    accounts: list[RegistryAccount],
) -> None:
    sweep = scheduler.start_sweep(session, NOW - timedelta(days=1))
    task = refresh(session, "queue-1", now=NOW - timedelta(days=1))

    # One thread, and it is an on-demand-only thread: it must leave the sweep alone.
    def denied(task: ScanTask, ctx: TaskContext) -> None:
        raise TaskError("access_denied", "no read access")

    worker = make_worker(
        engine, make_settings, {"resource": denied, "sweep_account": denied}, threads=1, on_demand=1
    )
    worker.start()

    wait_for(lambda: task_state(session, task).status == "failed")
    worker.stop()
    assert worker.join(timeout=5)

    failed = task_state(session, task)
    assert (failed.error_code, failed.attempt) == ("access_denied", 1)
    session.expire_all()
    assert {
        t.status for t in session.exec(select(ScanTask).where(ScanTask.scan_request_id == sweep.id))
    } == {"queued"}


def test_a_worker_that_lost_its_task_stops_and_writes_nothing_more(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    accounts: list[RegistryAccount],
) -> None:
    task = scheduled_shard(session, utcnow() - timedelta(minutes=1))
    outcome: list[str] = []

    def stalled_scan(task: ScanTask, ctx: TaskContext) -> None:
        with ctx.new_session() as own:
            leases.acquire_or_settle(own, task)
        try:
            while True:
                time.sleep(0.02)
                ctx.checkpoint(task)
        except BaseException as exc:
            outcome.append(type(exc).__name__)
            raise

    worker = make_worker(engine, make_settings, {"shard": stalled_scan})
    worker.start()
    wait_for(lambda: task_state(session, task).status == "running")
    wait_for(lambda: shard_state(session).lease_task_id == task.id)

    # The reaper decides the worker is gone and a second worker claims the task.
    future = utcnow() + timedelta(minutes=10)
    assert queue.reap_lost_tasks(session, future).requeued == [task.id]
    second = queue.claim_task(session, "worker-2", per_account_concurrency=9, now=future)
    assert second is not None and second.id == task.id
    stale_lease = shard_state(session)

    wait_for(lambda: bool(outcome))
    worker.stop()
    assert worker.join(timeout=5)

    assert outcome == ["TaskLost"]
    now_state = task_state(session, task)
    assert (now_state.status, now_state.claim_token, now_state.worker_id) == (
        "running",
        second.claim_token,
        "worker-2",
    )
    after = shard_state(session)
    assert (after.lease_task_id, after.lease_token, after.lease_expires_at) == (
        stale_lease.lease_task_id,
        stale_lease.lease_token,
        stale_lease.lease_expires_at,
    )


def test_healthcheck_passes_only_while_the_worker_heartbeat_file_is_fresh(
    engine: Engine,
    make_settings: Callable[..., Settings],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat = tmp_path / "worker.heartbeat"
    settings = make_settings(WORKER_HEARTBEAT_FILE=heartbeat)
    monkeypatch.setenv("DATABASE_URL", settings.DATABASE_URL)
    monkeypatch.setenv("DATABASE_SCHEMA", settings.DATABASE_SCHEMA)
    monkeypatch.setenv("WORKER_HEARTBEAT_FILE", str(heartbeat))
    clear_caches()
    assert cli.main(["healthcheck"]) == 1  # no worker has run yet

    worker = Worker(engine, settings, handlers={}, poll_interval=0.05, heartbeat_interval=0.2)
    worker.start()
    try:
        wait_for(heartbeat.exists)
        assert cli.main(["healthcheck"]) == 0
    finally:
        worker.stop()
        worker.join(timeout=10)
    stale = time.time() - 3600  # the worker stopped beating an hour ago
    os.utime(heartbeat, (stale, stale))
    assert cli.main(["healthcheck"]) == 1
    clear_caches()
