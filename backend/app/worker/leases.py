"""Scope leases: one running task per (account, region, resource type) (R1-R3, scanner.md §4).

A task is on-demand when its priority is 0, scheduled otherwise. Same conventions as
`queue.py` (including its lock order: shard rows before task rows): one transaction per
function, `now` injectable.

Single-resource `resource` tasks never take a lease: R4's `observed_at` guard protects their
writes and the partial unique index on the task table deduplicates them.

Every lease is stamped with the `claim_token` of the task claim that took it (`lease_token`),
and acquiring, renewing and releasing all require a claim that is still current. A worker
that lost its task (reaped, claimed again) can therefore neither extend nor free the lease the
new claimant holds.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, NamedTuple

from sqlalchemy import text
from sqlmodel import Session

from app.models import ScanTask
from app.models.scan import utcnow
from app.worker import queue

LEASE_TTL = timedelta(minutes=5)
FRESHNESS_WINDOW = timedelta(minutes=30)


class Scope(NamedTuple):
    account_id: str
    region: str
    resource_type: str

    @classmethod
    def of(cls, task: ScanTask) -> Scope:
        if task.region is None or task.resource_type is None:
            raise ValueError(f"task {task.id} ({task.kind}) has no region/resource_type scope")
        return cls(task.account_id, task.region, task.resource_type)


class LeaseState(StrEnum):
    ACQUIRED = "acquired"
    BUSY = "busy"  # someone else holds the lease
    FRESH = "fresh"  # scheduled task, scope succeeded less than 30 minutes ago
    LOST = "lost"  # the caller no longer owns the task: stop, write nothing


def takes_lease(task: ScanTask) -> bool:
    return task.kind != "resource"


def is_on_demand(task: ScanTask) -> bool:
    return task.priority == 0


def try_acquire(
    session: Session, task: ScanTask, scope: Scope, now: datetime | None = None
) -> LeaseState:
    """Try to take the lease of `scope` for `task`, without touching the task's status.

    `config_group` calls this once per type it covers. Re-acquiring a lease the task already
    holds succeeds, so a retry of the same call is harmless, and a task claimed again after
    the reaper requeued it takes over its own stale lease. Returns `LOST` when `task`'s claim
    is no longer the current one.
    """
    if not takes_lease(task):
        raise ValueError(f"task {task.id} is a single-resource refresh: it takes no lease")
    now = now or utcnow()
    key = {"a": scope.account_id, "r": scope.region, "t": scope.resource_type}
    if not _owns_claim(session, task):
        return LeaseState.LOST
    if not is_on_demand(task):
        last_ok = (
            session.connection()
            .execute(
                text("""
            SELECT last_succeeded_at FROM t_inv_aws_scan_shard
             WHERE account_id = :a AND region = :r AND resource_type = :t
            """),
                key,
            )
            .scalar()
        )
        if last_ok is not None and last_ok > now - FRESHNESS_WINDOW:
            session.rollback()
            return LeaseState.FRESH
    session.connection().execute(
        text("""
        INSERT INTO t_inv_aws_scan_shard (account_id, region, resource_type)
        VALUES (:a, :r, :t) ON CONFLICT DO NOTHING
        """),
        key,
    )
    row = (
        session.connection()
        .execute(
            text("""
        UPDATE t_inv_aws_scan_shard
           SET lease_task_id = :task_id, lease_token = :token, lease_expires_at = :expires,
               last_started_at = :now,
               pending_task_id = CASE WHEN pending_task_id = :task_id THEN NULL
                                      ELSE pending_task_id END
         WHERE account_id = :a AND region = :r AND resource_type = :t
           AND (lease_task_id IS NULL OR lease_expires_at < :now OR lease_task_id = :task_id)
        RETURNING account_id
        """),
            {
                **key,
                "task_id": task.id,
                "token": task.claim_token,
                "now": now,
                "expires": now + LEASE_TTL,
            },
        )
        .first()
    )
    session.commit()
    return LeaseState.ACQUIRED if row else LeaseState.BUSY


def _owns_claim(session: Session, task: ScanTask) -> bool:
    owned = (
        session.connection()
        .execute(
            text("""
        SELECT 1 FROM t_inv_aws_scan_task
         WHERE id = :id AND status = 'running' AND claim_token = :token
        """),
            {"id": task.id, "token": task.claim_token or ""},
        )
        .first()
    )
    if owned is None:
        session.rollback()
    return owned is not None


def covers(pending: Any, new: ScanTask) -> bool:
    """Would running `new` produce everything `pending` was waiting to produce? Only tasks of
    the same kind, scope and source qualify, never single resources."""
    return takes_lease(new) and (
        (pending.kind, pending.account_id, pending.region, pending.resource_type, pending.source)
        == (new.kind, new.account_id, new.region, new.resource_type, new.source)
    )


def park_pending(
    session: Session, task: ScanTask, scope: Scope, now: datetime | None = None
) -> None:
    """Queue an on-demand task behind the lease holder as the scope's one trailing run (R2).

    The task goes back to `queued` without an attempt bump and becomes `pending_task_id`; an
    earlier pending task is coalesced into it, but only when the new task covers it (same kind
    and scope). A task that does not cover the pending one cannot replace it: it just waits
    until the lease would expire and tries again. Either way it stays unclaimable until the
    holder releases the lease (which sets `not_before = now` on the pending task).
    """
    now = now or utcnow()
    key = {"a": scope.account_id, "r": scope.region, "t": scope.resource_type}
    if not _owns_claim(session, task):
        return
    shard = (
        session.connection()
        .execute(
            text("""
        SELECT pending_task_id, lease_expires_at FROM t_inv_aws_scan_shard
         WHERE account_id = :a AND region = :r AND resource_type = :t FOR UPDATE
        """),
            key,
        )
        .one()
    )
    # Lock both tasks in id order before changing either (queue.py: shard, then tasks).
    held = {
        row.id: row
        for row in session.connection().execute(
            text("""
            SELECT id, kind, account_id, region, resource_type, source
              FROM t_inv_aws_scan_task WHERE id = ANY(:ids) ORDER BY id FOR UPDATE
            """),
            {"ids": [i for i in (task.id, shard.pending_task_id) if i is not None]},
        )
    }
    old = held.get(shard.pending_task_id) if shard.pending_task_id not in (None, task.id) else None
    replace = old is None or covers(old, task)
    if old is not None and replace:
        session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_task
               SET status = 'coalesced', coalesced_into = :new_id, finished_at = :now
             WHERE id = :old_id AND status = 'queued'
            """),
            {"new_id": task.id, "old_id": old.id, "now": now},
        )
    if replace:
        session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_shard SET pending_task_id = :task_id
             WHERE account_id = :a AND region = :r AND resource_type = :t
            """),
            {**key, "task_id": task.id},
        )
    session.connection().execute(
        text("""
        UPDATE t_inv_aws_scan_task
           SET status = 'queued', attempt = greatest(attempt - 1, 0), not_before = :not_before,
               worker_id = NULL, claim_token = NULL, heartbeat_at = NULL, started_at = NULL
         WHERE id = :id AND status = 'running' AND claim_token = :token
        """),
        {
            "id": task.id,
            "token": task.claim_token or "",
            "not_before": max(shard.lease_expires_at or now, now),
        },
    )
    session.commit()


def acquire_or_settle(session: Session, task: ScanTask, now: datetime | None = None) -> LeaseState:
    """Acquire the lease for a single-scope task, or settle the task when it can't run.

    - single-resource refresh: no lease is needed, `ACQUIRED`
    - fresh scope (scheduled task): `skipped`
    - busy scope, scheduled task: `skipped`, the running task already covers it
    - busy scope, on-demand task: parked as the trailing run, see `park_pending`
    - the task's claim is no longer current: `LOST`, nothing is touched

    Anything but `ACQUIRED` means the task is no longer running and the handler must return.
    """
    assert task.id is not None
    if not takes_lease(task):
        return LeaseState.ACQUIRED
    now = now or utcnow()
    scope = Scope.of(task)
    state = try_acquire(session, task, scope, now)
    if state is LeaseState.ACQUIRED or state is LeaseState.LOST:
        return state
    if state is LeaseState.BUSY and is_on_demand(task):
        park_pending(session, task, scope, now)
    else:
        queue.finish_task(
            session,
            task.id,
            "skipped",
            claim_token=task.claim_token or "",
            stats={"reason": state.value},
            now=now,
        )
    return state


def renew_leases(session: Session, claims: Mapping[int, str], now: datetime | None = None) -> None:
    """Push the expiry of every lease held under the given claims ({task id: claim token})
    out by the TTL. Leases of a task claimed again by someone else are left alone."""
    if not claims:
        return
    ids = sorted(claims)
    session.connection().execute(
        text("""
        WITH mine AS (
            SELECT s.account_id, s.region, s.resource_type FROM t_inv_aws_scan_shard s
              JOIN unnest(CAST(:ids AS bigint[]), CAST(:tokens AS text[])) AS c(id, token)
                ON c.id = s.lease_task_id AND c.token = s.lease_token
             ORDER BY s.account_id, s.region, s.resource_type
               FOR UPDATE OF s)
        UPDATE t_inv_aws_scan_shard s SET lease_expires_at = :expires
          FROM mine
         WHERE (s.account_id, s.region, s.resource_type)
             = (mine.account_id, mine.region, mine.resource_type)
        """),
        {
            "ids": ids,
            "tokens": [claims[i] for i in ids],
            "expires": (now or utcnow()) + LEASE_TTL,
        },
    )
    session.commit()


def release_lease(
    session: Session,
    task: ScanTask,
    scope: Scope,
    *,
    success: bool | None,
    error_code: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Release a lease the task's current claim holds and wake up the pending on-demand task,
    if any (§4.3).

    `success=None` releases without recording an outcome (stop, cancel).

    Returns False when the claim no longer held the lease (it expired and someone else took it,
    or the task was claimed again).
    """
    now = now or utcnow()
    row = (
        session.connection()
        .execute(
            text("""
        WITH held AS (
            SELECT account_id, region, resource_type, pending_task_id
              FROM t_inv_aws_scan_shard
             WHERE account_id = :a AND region = :r AND resource_type = :t
               AND lease_task_id = :task_id AND lease_token = :token
               FOR UPDATE)
        UPDATE t_inv_aws_scan_shard s
           SET lease_task_id = NULL, lease_token = NULL, lease_expires_at = NULL,
               last_task_id = :task_id,
               last_succeeded_at = CASE WHEN CAST(:success AS boolean) THEN :now
                                        ELSE s.last_succeeded_at END,
               consecutive_failures = CASE WHEN CAST(:success AS boolean) THEN 0
                                           WHEN CAST(:success AS boolean) IS FALSE
                                                THEN s.consecutive_failures + 1
                                           ELSE s.consecutive_failures END,
               last_error_code = CASE WHEN CAST(:success AS boolean) IS FALSE THEN :error_code
                                      ELSE s.last_error_code END,
               pending_task_id = NULL
          FROM held
         WHERE (s.account_id, s.region, s.resource_type)
             = (held.account_id, held.region, held.resource_type)
        RETURNING held.pending_task_id AS pending
        """),
            {
                "a": scope.account_id,
                "r": scope.region,
                "t": scope.resource_type,
                "task_id": task.id,
                "token": task.claim_token or "",
                "success": success,
                "error_code": error_code,
                "now": now,
            },
        )
        .first()
    )
    if row is None:
        session.rollback()
        return False
    if row.pending is not None:
        session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_task SET not_before = :now
             WHERE id = :id AND status = 'queued'
            """),
            {"id": row.pending, "now": now},
        )
    session.commit()
    return True


def release_task_leases(
    session: Session,
    task: ScanTask,
    *,
    success: bool | None,
    error_code: str | None = None,
    now: datetime | None = None,
) -> int:
    """Release every lease still held under a task's claim. The worker loop's safety net for
    handlers that ended without releasing (an exception, a stop, a cancel)."""
    rows = (
        session.connection()
        .execute(
            text(
                "SELECT account_id, region, resource_type FROM t_inv_aws_scan_shard"
                " WHERE lease_task_id = :id AND lease_token = :token"
            ),
            {"id": task.id, "token": task.claim_token or ""},
        )
        .all()
    )
    session.rollback()
    released = 0
    for account_id, region, resource_type in rows:
        scope = Scope(account_id, region, resource_type)
        if release_lease(session, task, scope, success=success, error_code=error_code, now=now):
            released += 1
    return released
