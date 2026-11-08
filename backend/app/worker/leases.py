"""Scope leases: one running task per (account, region, resource type) (R1-R3, scanner.md §4).

A task is on-demand when its priority is 0, scheduled otherwise. Same conventions as
`queue.py`: one transaction per function, `now` injectable.
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NamedTuple

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


def is_on_demand(task: ScanTask) -> bool:
    return task.priority == 0


def try_acquire(
    session: Session, task: ScanTask, scope: Scope, now: datetime | None = None
) -> LeaseState:
    """Try to take the lease of `scope` for `task`, without touching the task's status.

    `config_group` calls this once per type it covers. Re-acquiring a lease the task already
    holds succeeds, so a retry of the same call is harmless.
    """
    now = now or utcnow()
    key = {"a": scope.account_id, "r": scope.region, "t": scope.resource_type}
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
           SET lease_task_id = :task_id, lease_expires_at = :expires, last_started_at = :now,
               pending_task_id = CASE WHEN pending_task_id = :task_id THEN NULL
                                      ELSE pending_task_id END
         WHERE account_id = :a AND region = :r AND resource_type = :t
           AND (lease_task_id IS NULL OR lease_expires_at < :now OR lease_task_id = :task_id)
        RETURNING account_id
        """),
            {**key, "task_id": task.id, "now": now, "expires": now + LEASE_TTL},
        )
        .first()
    )
    session.commit()
    return LeaseState.ACQUIRED if row else LeaseState.BUSY


def park_pending(
    session: Session, task: ScanTask, scope: Scope, now: datetime | None = None
) -> None:
    """Queue an on-demand task behind the lease holder as the scope's one trailing run (R2).

    The task goes back to `queued` without an attempt bump and becomes `pending_task_id`; an
    earlier pending task is coalesced into it. It stays unclaimable until the holder releases
    the lease (which sets `not_before = now`), or at the latest until the lease would expire.
    """
    now = now or utcnow()
    key = {"a": scope.account_id, "r": scope.region, "t": scope.resource_type}
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
    if shard.pending_task_id not in (None, task.id):
        session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_task
               SET status = 'coalesced', coalesced_into = :new_id, finished_at = :now
             WHERE id = :old_id AND status = 'queued'
            """),
            {"new_id": task.id, "old_id": shard.pending_task_id, "now": now},
        )
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
               worker_id = NULL, heartbeat_at = NULL, started_at = NULL
         WHERE id = :id AND status = 'running'
        """),
        {"id": task.id, "not_before": max(shard.lease_expires_at or now, now)},
    )
    session.commit()


def acquire_or_settle(session: Session, task: ScanTask, now: datetime | None = None) -> LeaseState:
    """Acquire the lease for a single-scope task, or settle the task when it can't run.

    - fresh scope (scheduled task): `skipped`
    - busy scope, scheduled task: `skipped`, the running task already covers it
    - busy scope, on-demand task: parked as the trailing run, see `park_pending`

    Anything but `ACQUIRED` means the task is no longer running and the handler must return.
    """
    assert task.id is not None
    now = now or utcnow()
    scope = Scope.of(task)
    state = try_acquire(session, task, scope, now)
    if state is LeaseState.ACQUIRED:
        return state
    if state is LeaseState.BUSY and is_on_demand(task):
        park_pending(session, task, scope, now)
    else:
        queue.finish_task(
            session,
            task.id,
            "skipped",
            worker_id=task.worker_id,
            stats={"reason": state.value},
            now=now,
        )
    return state


def renew_leases(session: Session, task_ids: Collection[int], now: datetime | None = None) -> None:
    """Push the expiry of every lease held by the given tasks out by the TTL."""
    if not task_ids:
        return
    session.connection().execute(
        text("""
        UPDATE t_inv_aws_scan_shard SET lease_expires_at = :expires
         WHERE lease_task_id = ANY(:ids)
        """),
        {"ids": list(task_ids), "expires": (now or utcnow()) + LEASE_TTL},
    )
    session.commit()


def release_lease(
    session: Session,
    task_id: int,
    scope: Scope,
    *,
    success: bool | None,
    error_code: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Release a lease the task holds and wake up the pending on-demand task, if any (§4.3).

    `success=None` releases without recording an outcome (stop, cancel).

    Returns False when the task no longer held the lease (it expired and someone else took it).
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
               AND lease_task_id = :task_id
               FOR UPDATE)
        UPDATE t_inv_aws_scan_shard s
           SET lease_task_id = NULL, lease_expires_at = NULL, last_task_id = :task_id,
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
                "task_id": task_id,
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
    task_id: int,
    *,
    success: bool | None,
    error_code: str | None = None,
    now: datetime | None = None,
) -> int:
    """Release every lease still held by a task. The worker loop's safety net for handlers
    that ended without releasing (an exception, a stop, a cancel)."""
    rows = (
        session.connection()
        .execute(
            text(
                "SELECT account_id, region, resource_type FROM t_inv_aws_scan_shard"
                " WHERE lease_task_id = :id"
            ),
            {"id": task_id},
        )
        .all()
    )
    session.rollback()
    released = 0
    for account_id, region, resource_type in rows:
        scope = Scope(account_id, region, resource_type)
        if release_lease(session, task_id, scope, success=success, error_code=error_code, now=now):
            released += 1
    return released
