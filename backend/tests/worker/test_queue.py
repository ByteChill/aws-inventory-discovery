import random
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.aws.account_registry import RegistryAccount
from app.core.config import Settings
from app.models import ScanRequest, ScanTask
from app.worker import leases, queue, requests
from app.worker.errors import TaskCancelled
from app.worker.handlers import TaskContext
from app.worker.scheduler import Scheduler
from tests.worker.conftest import (
    IMAGES,
    NOW,
    PAYMENTS_DEV,
    PAYMENTS_PROD,
    WORKER,
    RegistryServer,
    claim,
    claim_one,
    complete,
    lease_scope,
    on_demand_shard,
    refresh,
    scheduled_shard,
    shard_state,
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
    complete(session, done)
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
            complete(session, task)
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

    assert queue.heartbeat(session, {task.id or 0: task.claim_token or ""}, later) == {task.id}
    assert queue.reap_lost_tasks(session, later).requeued == []
    assert queue.heartbeat(session, {task.id or 0: "stale"}, later) == set()


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
    assert queue.finish_task(
        session, running.id or 0, "cancelled", claim_token=running.claim_token or "", now=NOW
    )
    assert request_of(session, running).status == "cancelled"
    statuses = sorted(t.status for t in session.exec(select(ScanTask)))
    assert statuses == ["cancelled", "cancelled", "cancelled"]


def park_two_on_demand_runs(session: Session) -> tuple[ScanTask, ScanTask, ScanTask, datetime]:
    """A scope held by a scheduled holder, with request A's task coalesced into request B's."""
    scheduled_shard(session)
    holder = claim_one(session)
    assert leases.acquire_or_settle(session, holder, NOW) is leases.LeaseState.ACQUIRED
    first = on_demand_shard(session)
    second = on_demand_shard(session, NOW + timedelta(seconds=1))
    later = NOW + timedelta(minutes=1)
    for _ in range(2):
        leases.acquire_or_settle(session, claim_one(session, later), later)
    return holder, first, second, later


def test_cancelling_a_request_keeps_a_shared_task_running_for_the_other_request(
    engine: Engine, session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder, first, second, later = park_two_on_demand_runs(session)

    queue.cancel_request(session, second.scan_request_id, later)

    assert task_state(session, second).status == "queued"  # request A still needs it
    leases.release_lease(session, holder, lease_scope(), success=True, now=later)
    trailing = claim_one(session, later)
    assert trailing.id == second.id
    assert leases.acquire_or_settle(session, trailing, later) is leases.LeaseState.ACQUIRED
    context = TaskContext(
        lambda: Session(engine, expire_on_commit=False), WORKER, threading.Event()
    )
    context.checkpoint(trailing)  # B is cancelled, A is not: keep going
    assert complete(session, trailing, later)
    assert request_of(session, first).status == "succeeded"
    assert request_of(session, second).status == "cancelled"


def test_a_shared_task_stops_once_every_request_that_needs_it_is_cancelled(
    engine: Engine, session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    _holder, first, second, later = park_two_on_demand_runs(session)
    queue.cancel_request(session, second.scan_request_id, later)

    queue.cancel_request(session, first.scan_request_id, later)

    assert task_state(session, second).status == "cancelled"  # nobody needs it any more
    assert task_state(session, first).status == "coalesced"
    assert shard_state(session).pending_task_id is None


def test_a_running_shared_task_checkpoints_until_all_dependents_are_cancelled(
    engine: Engine, session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder, first, second, later = park_two_on_demand_runs(session)
    leases.release_lease(session, holder, lease_scope(), success=True, now=later)
    trailing = claim_one(session, later)
    assert leases.acquire_or_settle(session, trailing, later) is leases.LeaseState.ACQUIRED
    context = TaskContext(
        lambda: Session(engine, expire_on_commit=False), WORKER, threading.Event()
    )

    queue.cancel_request(session, second.scan_request_id, later)
    context.checkpoint(trailing)
    queue.cancel_request(session, first.scan_request_id, later)

    with pytest.raises(TaskCancelled):
        context.checkpoint(trailing)


def test_cancel_does_not_deadlock_with_claim_and_finish(
    engine: Engine, settings: Settings, accounts: list[RegistryAccount]
) -> None:
    # One global lock order (queue.py docstring): cancel, claim and finish race on the same rows.
    errors: list[BaseException] = []

    def run(action: Callable[[Session], object], barrier: threading.Barrier) -> None:
        try:
            with Session(engine, expire_on_commit=False) as own:
                barrier.wait()
                action(own)
        except BaseException as exc:  # noqa: BLE001 - the test reports whatever went wrong
            errors.append(exc)

    for _ in range(25):
        with Session(engine, expire_on_commit=False) as setup:
            created = requests.create_scan(
                setup,
                requests.ScanSpec(account_ids=[PAYMENTS_DEV, PAYMENTS_PROD, IMAGES]),
                settings,
                now=NOW,
            )
            running = claim_one(setup)
        barrier = threading.Barrier(3)
        actions = race_actions(created.request.id or 0, running)
        threads = [threading.Thread(target=run, args=(a, barrier)) for a in actions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors, errors
        with Session(engine) as cleanup:
            queue.cancel_request(cleanup, created.request.id or 0, NOW)


def race_actions(request_id: int, running: ScanTask) -> list[Callable[[Session], object]]:
    return [
        lambda s: queue.cancel_request(s, request_id, NOW),
        lambda s: claim(s),
        lambda s: queue.complete_task(s, running.id or 0, running.claim_token or "", now=NOW),
    ]


def test_request_counts_persist_task_totals_and_resource_deltas(
    session: Session,
    scheduler: Scheduler,
    registry: RegistryServer,
    accounts: list[RegistryAccount],
) -> None:
    registry.always_fail = 500  # the sweep runs on the stored snapshot and records why
    sweep = scheduler.start_sweep(session, NOW)
    assert "registryError" in (sweep.counts or {})
    total = (sweep.counts or {})["accounts"]
    later = NOW + timedelta(hours=6)  # every account's stagger offset has passed
    first, second = claim_one(session, later), claim_one(session, later)

    assert complete(session, first, NOW, stats={"new": 3, "updated": 1})
    mid = request_of(session, first).counts or {}
    assert mid["tasks"] == {"succeeded": 1, "running": 1, "queued": total - 2}
    assert mid["resources"] == {"new": 3, "updated": 1, "missing": 0, "deleted": 0}

    assert complete(session, second, NOW, stats={"new": 2, "missing": 4, "deleted": 1})
    counts = request_of(session, first).counts or {}
    assert counts["resources"] == {"new": 5, "updated": 1, "missing": 4, "deleted": 1}
    assert counts["tasks"] == {"succeeded": 2, "queued": total - 2}
    assert counts["accounts"] == total
    assert "registryError" in counts
