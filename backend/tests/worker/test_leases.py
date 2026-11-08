from datetime import timedelta

from sqlmodel import Session

from app.aws.account_registry import RegistryAccount
from app.models import ScanRequest, ScanTask
from app.worker import leases, queue
from app.worker.leases import LeaseState
from app.worker.scheduler import Scheduler
from tests.worker.conftest import (
    NOW,
    WORKER,
    claim_one,
    lease_scope,
    refresh,
    scheduled_shard,
    shard_state,
    task_state,
)


def request_status(session: Session, task: ScanTask) -> str:
    session.expire_all()
    return session.get_one(ScanRequest, task.scan_request_id).status


def hold_lease(session: Session) -> ScanTask:
    """A scheduled shard task that has claimed and holds the lease of the test scope."""
    scheduled_shard(session)
    holder = claim_one(session)
    assert holder.kind == "shard"
    assert leases.acquire_or_settle(session, holder, NOW) is LeaseState.ACQUIRED
    return holder


def test_busy_scope_skips_scheduled_tasks_and_keeps_the_lease(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder = hold_lease(session)
    # Another sweep reaches the same scope while the first still scans it.
    other = scheduled_shard(session)
    assert claim_one(session).id == other.id

    state = leases.acquire_or_settle(session, other, NOW)

    assert state is LeaseState.BUSY
    skipped = task_state(session, other)
    assert (skipped.status, skipped.stats) == ("skipped", {"reason": "busy"})
    assert shard_state(session).lease_task_id == holder.id


def test_on_demand_tasks_on_a_busy_scope_leave_one_trailing_run(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder = hold_lease(session)
    first = refresh(session, "queue-1")
    second = refresh(session, "queue-2", now=NOW + timedelta(seconds=1))
    now = NOW + timedelta(minutes=1)

    for _ in range(2):
        parked = claim_one(session, now)
        assert leases.acquire_or_settle(session, parked, now) is LeaseState.BUSY

    # R2: the earlier waiting task is merged into the newer one, which waits unclaimed.
    merged = task_state(session, first)
    assert (merged.status, merged.coalesced_into) == ("coalesced", second.id)
    waiting = task_state(session, second)
    assert (waiting.status, waiting.attempt) == ("queued", 0)
    assert shard_state(session).pending_task_id == second.id
    assert queue.claim_task(session, WORKER, per_account_concurrency=9, now=now) is None

    # Releasing the lease wakes the trailing run, which then holds the lease itself.
    assert leases.release_lease(session, holder.id or 0, lease_scope(), success=True, now=now)
    trailing = claim_one(session, now)
    assert trailing.id == second.id
    assert leases.acquire_or_settle(session, trailing, now) is LeaseState.ACQUIRED
    assert shard_state(session).pending_task_id is None
    # The merged request only finishes once the run that stands in for it has.
    assert request_status(session, first) == "running"
    queue.complete_task(session, trailing.id or 0, WORKER, now=now)
    assert request_status(session, first) == request_status(session, second) == "succeeded"


def test_scheduled_tasks_skip_a_fresh_scope_but_on_demand_ones_do_not(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder = hold_lease(session)
    assert leases.release_lease(session, holder.id or 0, lease_scope(), success=True, now=NOW)

    soon = NOW + timedelta(minutes=10)
    scheduled = scheduled_shard(session, soon)
    assert claim_one(session, soon).id == scheduled.id
    assert leases.acquire_or_settle(session, scheduled, soon) is LeaseState.FRESH
    assert task_state(session, scheduled).stats == {"reason": "fresh"}

    on_demand = refresh(session, "queue-1", now=soon)
    assert claim_one(session, soon).id == on_demand.id
    assert leases.acquire_or_settle(session, on_demand, soon) is LeaseState.ACQUIRED
    leases.release_lease(session, on_demand.id or 0, lease_scope(), success=True, now=soon)

    later = soon + timedelta(minutes=31)
    again = scheduled_shard(session, later)
    assert claim_one(session, later).id == again.id
    assert leases.acquire_or_settle(session, again, later) is LeaseState.ACQUIRED


def test_lease_of_a_dead_worker_expires_and_failures_are_recorded(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    # Scenario F: the holder never releases and never renews.
    holder = hold_lease(session)
    newcomer = refresh(session, "queue-1")
    soon = NOW + timedelta(minutes=4)
    assert leases.acquire_or_settle(session, claim_one(session, soon), soon) is LeaseState.BUSY
    assert task_state(session, newcomer).status == "queued"

    # A heartbeat of the holder pushes the expiry out, so the lease is still busy at 6 minutes.
    leases.renew_leases(session, [holder.id or 0], soon)
    late = NOW + timedelta(minutes=6)
    assert leases.try_acquire(session, newcomer, lease_scope(), late) is LeaseState.BUSY

    expired = NOW + timedelta(minutes=10)
    assert leases.try_acquire(session, newcomer, lease_scope(), expired) is LeaseState.ACQUIRED
    assert shard_state(session).lease_task_id == newcomer.id
    assert shard_state(session).pending_task_id is None

    # The holder's late release is ignored; the newcomer's failure is recorded and does not
    # make the scope fresh.
    assert not leases.release_lease(session, holder.id or 0, lease_scope(), success=True)
    assert leases.release_lease(
        session, newcomer.id or 0, lease_scope(), success=False, error_code="throttled", now=expired
    )
    state = shard_state(session)
    assert (state.lease_task_id, state.consecutive_failures, state.last_error_code) == (
        None,
        1,
        "throttled",
    )
    assert state.last_succeeded_at is None
