from datetime import timedelta

import pytest
from sqlmodel import Session

from app.aws.account_registry import RegistryAccount
from app.models import ScanRequest, ScanTask
from app.worker import leases, queue
from app.worker.errors import TaskLost
from app.worker.leases import LeaseState
from app.worker.scheduler import Scheduler
from tests.worker.conftest import (
    NOW,
    WORKER,
    claim_one,
    complete,
    lease_scope,
    on_demand_shard,
    refresh,
    scheduled_shard,
    shard_state,
    task_state,
)

LEASE_STEP = timedelta(minutes=1)


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
    first = on_demand_shard(session)
    second = on_demand_shard(session, NOW + timedelta(seconds=1))
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
    assert leases.release_lease(session, holder, lease_scope(), success=True, now=now)
    trailing = claim_one(session, now)
    assert trailing.id == second.id
    assert leases.acquire_or_settle(session, trailing, now) is LeaseState.ACQUIRED
    assert shard_state(session).pending_task_id is None
    # The merged request only finishes once the run that stands in for it has.
    assert request_status(session, first) == "running"
    complete(session, trailing, now)
    assert request_status(session, first) == request_status(session, second) == "succeeded"


def test_single_resource_refreshes_bypass_the_scope_lease_and_are_never_coalesced(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    # The scope is busy, yet refreshes of two different queues both run: R4's observed_at
    # guard protects them, and the partial unique index is their only deduplication.
    holder = hold_lease(session)
    first = refresh(session, "queue-1")
    second = refresh(session, "queue-2", now=NOW + timedelta(seconds=1))
    now = NOW + timedelta(minutes=1)

    for task in (claim_one(session, now), claim_one(session, now)):
        assert leases.acquire_or_settle(session, task, now) is LeaseState.ACQUIRED

    assert {task_state(session, t).status for t in (first, second)} == {"running"}
    state = shard_state(session)
    assert (state.lease_task_id, state.pending_task_id) == (holder.id, None)
    for task in (first, second):
        assert complete(session, task, now)
        assert request_status(session, task) == "succeeded"


def test_a_pending_run_only_absorbs_tasks_it_covers(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    hold_lease(session)
    shard = on_demand_shard(session)
    other_kind = on_demand_shard(session, NOW + timedelta(seconds=1), kind="confirm_missing")
    now = NOW + timedelta(minutes=1)

    for _ in range(2):
        assert leases.acquire_or_settle(session, claim_one(session, now), now) is LeaseState.BUSY

    # A confirm_missing run is no substitute for the pending shard task: both survive.
    assert task_state(session, shard).status == "queued"
    assert task_state(session, other_kind).status == "queued"
    assert shard_state(session).pending_task_id == shard.id


def test_scheduled_tasks_skip_a_fresh_scope_but_on_demand_ones_do_not(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder = hold_lease(session)
    assert leases.release_lease(session, holder, lease_scope(), success=True, now=NOW)

    soon = NOW + timedelta(minutes=10)
    scheduled = scheduled_shard(session, soon)
    assert claim_one(session, soon).id == scheduled.id
    assert leases.acquire_or_settle(session, scheduled, soon) is LeaseState.FRESH
    assert task_state(session, scheduled).stats == {"reason": "fresh"}

    on_demand = refresh(session, "queue-1", now=soon)
    assert claim_one(session, soon).id == on_demand.id
    assert leases.acquire_or_settle(session, on_demand, soon) is LeaseState.ACQUIRED
    leases.release_lease(session, on_demand, lease_scope(), success=True, now=soon)

    later = soon + timedelta(minutes=31)
    again = scheduled_shard(session, later)
    assert claim_one(session, later).id == again.id
    assert leases.acquire_or_settle(session, again, later) is LeaseState.ACQUIRED


def test_lease_of_a_dead_worker_expires_and_failures_are_recorded(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    # Scenario F: the holder never releases and never renews.
    holder = hold_lease(session)
    newcomer = on_demand_shard(session)
    soon = NOW + timedelta(minutes=4)
    assert leases.acquire_or_settle(session, claim_one(session, soon), soon) is LeaseState.BUSY
    assert task_state(session, newcomer).status == "queued"
    assert shard_state(session).pending_task_id == newcomer.id

    # A heartbeat of the holder pushes the expiry out, so the lease is still busy at 6 minutes.
    leases.renew_leases(session, {holder.id or 0: holder.claim_token or ""}, soon)
    late = NOW + timedelta(minutes=6)
    woken = claim_one(session, late)  # the parked task wakes at the old expiry, still busy
    assert leases.acquire_or_settle(session, woken, late) is LeaseState.BUSY
    assert shard_state(session).pending_task_id == newcomer.id

    expired = NOW + timedelta(minutes=10)
    newcomer = claim_one(session, expired)
    assert leases.try_acquire(session, newcomer, lease_scope(), expired) is LeaseState.ACQUIRED
    assert shard_state(session).lease_task_id == newcomer.id
    assert shard_state(session).pending_task_id is None

    # The holder's late release is ignored; the newcomer's failure is recorded and does not
    # make the scope fresh.
    assert not leases.release_lease(session, holder, lease_scope(), success=True)
    assert leases.release_lease(
        session, newcomer, lease_scope(), success=False, error_code="throttled", now=expired
    )
    state = shard_state(session)
    assert (state.lease_task_id, state.consecutive_failures, state.last_error_code) == (
        None,
        1,
        "throttled",
    )
    assert state.last_succeeded_at is None


def test_a_reaped_claim_cannot_take_or_extend_a_lease_before_the_next_claimant(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    holder = hold_lease(session)
    expires = shard_state(session).lease_expires_at
    later = NOW + timedelta(minutes=3)
    assert queue.reap_lost_tasks(session, later).requeued == [holder.id]

    # The task is queued for whoever claims it next; the old handler must not slip in first.
    assert not leases.release_lease(session, holder, lease_scope(), success=True, now=later)
    leases.renew_leases(session, {holder.id or 0: holder.claim_token or ""}, later)
    assert leases.try_acquire(session, holder, lease_scope(), later) is LeaseState.LOST
    state = shard_state(session)
    assert (state.lease_task_id, state.lease_token, state.lease_expires_at) == (
        holder.id,
        holder.claim_token,
        expires,
    )

    fresh = claim_one(session, later)
    assert leases.acquire_or_settle(session, fresh, later) is LeaseState.ACQUIRED
    assert shard_state(session).lease_task_id == fresh.id


def test_a_requeued_task_fences_out_the_stalled_worker(
    session: Session, scheduler: Scheduler, accounts: list[RegistryAccount]
) -> None:
    scheduled_shard(session)
    stale = claim_one(session)
    assert leases.acquire_or_settle(session, stale, NOW) is LeaseState.ACQUIRED
    later = NOW + timedelta(minutes=3)
    assert queue.reap_lost_tasks(session, later).requeued == [stale.id]
    fresh = claim_one(session, later)  # another worker claims the same task
    assert fresh.id == stale.id
    assert fresh.claim_token and fresh.claim_token != stale.claim_token
    assert leases.acquire_or_settle(session, fresh, later) is LeaseState.ACQUIRED
    expires = shard_state(session).lease_expires_at

    # Everything the stalled worker does with its old token is refused.
    assert leases.acquire_or_settle(session, stale, later) is LeaseState.LOST
    assert queue.heartbeat(session, {stale.id or 0: stale.claim_token or ""}, later) == set()
    leases.renew_leases(session, {stale.id or 0: stale.claim_token or ""}, later + LEASE_STEP)
    assert not leases.release_lease(session, stale, lease_scope(), success=True, now=later)
    assert leases.release_task_leases(session, stale, success=True, now=later) == 0
    assert not complete(session, stale, later)
    assert queue.fail_task(session, stale, "internal", "x", retryable=True, now=later) is None
    assert not queue.requeue_task(session, stale, later)
    with pytest.raises(TaskLost):
        queue.assert_owned(session, stale)

    state = shard_state(session)
    assert (state.lease_task_id, state.lease_token, state.lease_expires_at) == (
        fresh.id,
        fresh.claim_token,
        expires,
    )
    running = task_state(session, fresh)
    assert (running.status, running.claim_token, running.attempt) == (
        "running",
        fresh.claim_token,
        2,
    )
    assert queue.heartbeat(session, {fresh.id or 0: fresh.claim_token or ""}, later) == {fresh.id}
