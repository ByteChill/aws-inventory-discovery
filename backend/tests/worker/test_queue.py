import random
import threading
from datetime import timedelta

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.aws.account_registry import RegistryAccount
from app.core.config import Settings
from app.models import ScanRequest, ScanTask
from app.worker import leases, queue, requests
from app.worker.scheduler import Scheduler
from tests.worker.conftest import (
    IMAGES,
    NOW,
    PAYMENTS_DEV,
    PAYMENTS_PROD,
    WORKER,
    claim,
    claim_one,
    lease_scope,
    refresh,
    scheduled_shard,
    task_state,
)


def request_of(session: Session, task: ScanTask) -> ScanRequest:
    session.expire_all()
    return session.get_one(ScanRequest, task.scan_request_id)


def test_claim_takes_on_demand_first_then_oldest_due_task(
    session: Session,
    scheduler: Scheduler,
    settings: Settings,
    accounts: list[RegistryAccount],
) -> None:
    sweep_start = NOW - timedelta(days=1)
    scheduler.start_sweep(session, sweep_start)
    requests.create_scan(
        session, requests.ScanSpec(account_ids=[PAYMENTS_DEV]), settings, now=sweep_start
    )

    # Mid-window only the scheduled tasks whose stagger offset has passed are due.
    midway = sweep_start + timedelta(hours=2)
    claimed = []
    while task := claim(session, midway):
        claimed.append(task)
    assert claimed[0].priority == 0
    assert all(t.not_before <= midway for t in claimed)
    scheduled = [t.not_before for t in claimed[1:]]
    assert scheduled == sorted(scheduled)

    # Later the rest become due, still oldest first.
    rest = []
    while task := claim(session, NOW):
        rest.append(task.not_before)
    assert rest == sorted(rest)
    assert len(claimed) + len(rest) == 4


def test_on_demand_only_threads_never_claim_scheduled_work(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    scheduler.start_sweep(session, NOW - timedelta(days=1))

    assert claim(session, NOW, max_priority=0) is None
    assert claim(session, NOW, max_priority=1) is not None

    refresh(session, "queue-1")
    task = claim_one(session, NOW, max_priority=0)
    assert task.kind == "resource"


def test_per_account_concurrency_caps_running_tasks(
    session: Session, accounts: list[RegistryAccount]
) -> None:
    for n in range(3):
        refresh(session, f"queue-{n}", PAYMENTS_DEV)
    refresh(session, "queue-x", IMAGES)

    def claim_capped() -> ScanTask | None:
        return queue.claim_task(session, WORKER, per_account_concurrency=2, now=NOW)

    first = [claim_capped() for _ in range(4)]

    assert sorted(t.account_id for t in first if t) == sorted([PAYMENTS_DEV, PAYMENTS_DEV, IMAGES])
    assert claim_capped() is None  # third payments task has to wait
    done = next(t for t in first if t and t.account_id == PAYMENTS_DEV)
    queue.complete_task(session, done.id or 0, WORKER, now=NOW)
    assert claim_capped() is not None


def test_concurrent_claims_never_hand_out_a_task_twice(
    engine: Engine, session: Session, accounts: list[RegistryAccount]
) -> None:
    # Scenario A: many workers racing for the same rows with SKIP LOCKED.
    total = 40
    for n in range(total):
        refresh(session, f"queue-{n}")
    start = threading.Barrier(2)
    claimed: dict[str, list[int]] = {"w1": [], "w2": []}

    def worker(name: str) -> None:
        with Session(engine, expire_on_commit=False) as own:
            start.wait()
            while True:
                task = queue.claim_task(own, name, per_account_concurrency=1000, now=NOW)
                if task is None:
                    return
                claimed[name].append(task.id or 0)

    threads = [threading.Thread(target=worker, args=(name,)) for name in claimed]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # A racing claim may come back empty while a row is momentarily locked; drain the rest.
    while task := claim(session):
        claimed["w1"].append(task.id or 0)

    everything = claimed["w1"] + claimed["w2"]
    assert len(everything) == len(set(everything)) == total


def test_failed_task_is_retried_with_backoff_until_attempts_run_out(
    session: Session, accounts: list[RegistryAccount]
) -> None:
    refresh(session, "queue-1")
    rng = random.Random(7)
    now = NOW
    for attempt, (low, high) in enumerate([(30, 60), (60, 90)], start=1):
        task = claim_one(session, now)
        assert task.attempt == attempt
        status = queue.fail_task(
            session, task, "throttled", "slow down", retryable=True, now=now, rng=rng
        )
        after = task_state(session, task)
        assert status == "queued"
        wait = (after.not_before - now).total_seconds()
        assert low <= wait <= high
        assert claim(session, now) is None  # not before the backoff has passed
        now = after.not_before

    last = claim_one(session, now)
    assert last.attempt == 3
    assert queue.fail_task(session, last, "throttled", "again", retryable=True, now=now) == "failed"
    failed = task_state(session, last)
    assert (failed.status, failed.error_code, failed.finished_at) == ("failed", "throttled", now)
    assert request_of(session, last).status == "failed"


def test_non_retryable_failure_fails_at_once(
    session: Session, accounts: list[RegistryAccount]
) -> None:
    refresh(session, "queue-1")
    task = claim_one(session)

    status = queue.fail_task(
        session, task, "access_denied", "no read access", retryable=False, now=NOW
    )

    assert status == "failed"
    assert task_state(session, task).attempt == 1


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (["ok", "ok"], "succeeded"),
        (["ok", "bad"], "partial"),
        (["bad", "bad"], "failed"),
    ],
)
def test_request_status_rolls_up_from_its_tasks(
    session: Session,
    settings: Settings,
    accounts: list[RegistryAccount],
    outcomes: list[str],
    expected: str,
) -> None:
    created = requests.create_scan(
        session,
        requests.ScanSpec(account_ids=[PAYMENTS_DEV, PAYMENTS_PROD]),
        settings,
        now=NOW,
    )
    assert created.request.status == "queued"
    for index, outcome in enumerate(outcomes):
        task = claim_one(session)
        if index == 0:
            session.refresh(created.request)
            assert created.request.status == "running"
        if outcome == "ok":
            queue.complete_task(session, task.id or 0, WORKER, now=NOW)
        else:
            queue.fail_task(session, task, "access_denied", "x", retryable=False, now=NOW)
        session.refresh(created.request)
        assert created.request.status == ("running" if index < len(outcomes) - 1 else expected)

    assert created.request.finished_at == NOW


def test_reaper_requeues_lost_tasks_then_fails_them_as_worker_lost(
    session: Session, accounts: list[RegistryAccount]
) -> None:
    # Scenario F: the worker dies after claiming.
    refresh(session, "queue-1")
    now = NOW
    for attempt in (1, 2):
        task = claim_one(session, now)
        assert queue.reap_lost_tasks(session, now + timedelta(minutes=1)).requeued == []
        lost = queue.reap_lost_tasks(session, now + timedelta(minutes=3))
        assert lost.requeued == [task.id]
        requeued = task_state(session, task)
        assert (requeued.status, requeued.attempt, requeued.worker_id) == ("queued", attempt, None)
        now += timedelta(minutes=3)

    task = claim_one(session, now)
    result = queue.reap_lost_tasks(session, now + timedelta(minutes=3))

    assert result.failed == [task.id]
    failed = task_state(session, task)
    assert (failed.status, failed.error_code, failed.attempt) == ("failed", "worker_lost", 3)
    assert request_of(session, task).status == "failed"


def test_heartbeat_keeps_a_task_alive_and_reports_lost_ones(
    session: Session, accounts: list[RegistryAccount]
) -> None:
    refresh(session, "queue-1")
    task = claim_one(session)
    later = NOW + timedelta(minutes=3)

    assert queue.heartbeat(session, WORKER, [task.id or 0], later) == {task.id}
    assert queue.reap_lost_tasks(session, later).requeued == []
    assert queue.heartbeat(session, "someone-else", [task.id or 0], later) == set()


def test_requeue_does_not_count_the_interrupted_attempt(
    session: Session, accounts: list[RegistryAccount]
) -> None:
    refresh(session, "queue-1")
    task = claim_one(session)

    assert queue.requeue_task(session, task, NOW)

    requeued = task_state(session, task)
    assert (requeued.status, requeued.attempt, requeued.worker_id) == ("queued", 0, None)
    assert claim_one(session, NOW).attempt == 1


def test_cancel_stops_queued_tasks_and_leaves_running_ones_to_notice(
    session: Session, settings: Settings, accounts: list[RegistryAccount]
) -> None:
    created = requests.create_scan(
        session,
        requests.ScanSpec(account_ids=[PAYMENTS_DEV, PAYMENTS_PROD, IMAGES]),
        settings,
        now=NOW,
    )
    running = claim_one(session)

    cancelled = queue.cancel_request(session, created.request.id or 0, NOW)

    assert cancelled is not None
    assert (cancelled.status, cancelled.finished_at) == ("cancelled", NOW)
    assert claim(session) is None
    assert queue.is_cancelled(session, created.request.id or 0)
    assert task_state(session, running).status == "running"
    # The running task finishes its page, sees the cancel and stops; the request stays cancelled.
    assert queue.finish_task(session, running.id or 0, "cancelled", worker_id=WORKER, now=NOW)
    assert request_of(session, running).status == "cancelled"
    statuses = sorted(t.status for t in session.exec(select(ScanTask)))
    assert statuses == ["cancelled", "cancelled", "cancelled"]


def test_cancel_spares_a_task_that_another_request_is_waiting_on(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder = scheduled_shard(session)
    claimed_holder = claim_one(session)
    assert claimed_holder.id == holder.id
    assert leases.acquire_or_settle(session, claimed_holder, NOW) is leases.LeaseState.ACQUIRED
    # Two on-demand refreshes of the same scope: the first is coalesced into the second (R2).
    first = refresh(session, "queue-1")
    second = refresh(session, "queue-2", now=NOW + timedelta(seconds=1))
    later = NOW + timedelta(minutes=1)
    for _ in range(2):
        leases.acquire_or_settle(session, claim_one(session, later), later)

    queue.cancel_request(session, second.scan_request_id, later)

    assert task_state(session, second).status == "queued"  # the first request still needs it
    leases.release_lease(session, holder.id or 0, lease_scope(), success=True, now=later)
    trailing = claim_one(session, later)
    assert trailing.id == second.id
    queue.complete_task(session, trailing.id or 0, WORKER, now=later)
    assert request_of(session, first).status == "succeeded"
    assert request_of(session, second).status == "cancelled"
