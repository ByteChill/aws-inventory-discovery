import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.aws.account_registry import RegistryAccount
from app.core.config import Settings
from app.models import ScanTask
from app.worker.handlers import HANDLERS, TaskContext
from app.worker.main import Worker
from app.worker.scheduler import Scheduler
from tests.worker.conftest import NOW, refresh, task_state


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


def test_unimplemented_task_kinds_fail_without_retries_and_scheduled_work_waits_for_other_threads(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    scheduler: Scheduler,
    accounts: list[RegistryAccount],
) -> None:
    sweep = scheduler.start_sweep(session, NOW - timedelta(days=1))
    task = refresh(session, "queue-1", now=NOW - timedelta(days=1))
    # One thread, and it is an on-demand-only thread: it must leave the sweep alone.
    worker = make_worker(engine, make_settings, HANDLERS, threads=1, on_demand=1)
    worker.start()

    wait_for(lambda: task_state(session, task).status == "failed")
    worker.stop()
    assert worker.join(timeout=5)

    failed = task_state(session, task)
    assert (failed.error_code, failed.attempt) == ("internal", 1)
    assert "M4" in (failed.error_message or "")
    session.expire_all()
    assert {
        t.status for t in session.exec(select(ScanTask).where(ScanTask.scan_request_id == sweep.id))
    } == {"queued"}
