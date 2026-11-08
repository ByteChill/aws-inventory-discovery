"""The task queue: `t_inv_aws_scan_task` is the queue (scanner.md §2, §3).

Conventions:
- Every function takes a `Session`, does its work in one transaction and commits it.
- Time comes from the Python clock (`utcnow()`); every function that reads the clock accepts
  `now` so tests can move time without sleeping.
"""

import json
import random
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from app.models import ScanRequest, ScanTask
from app.models.scan import utcnow

HEARTBEAT_TIMEOUT = timedelta(minutes=2)
BACKOFF_BASE_SECONDS = 30
BACKOFF_JITTER_SECONDS = 30

TERMINAL_REQUEST_STATUSES = ("succeeded", "partial", "failed", "cancelled")

# A claimed task that `claim_task` returns is detached from the session, so the caller can
# keep using it after the transaction has ended.

CLAIM_SQL = text("""
UPDATE t_inv_aws_scan_task
   SET status = 'running', worker_id = :worker_id, attempt = attempt + 1,
       started_at = :now, heartbeat_at = :now, finished_at = NULL
 WHERE id = (
   SELECT t.id FROM t_inv_aws_scan_task t
    WHERE t.status = 'queued'
      AND t.not_before <= :now
      AND t.priority <= :max_priority
      AND (SELECT count(*) FROM t_inv_aws_scan_task r
            WHERE r.account_id = t.account_id AND r.status = 'running') < :per_account_concurrency
    ORDER BY t.priority, t.not_before
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING *
""")


def backoff_seconds(attempt: int, rng: random.Random | None = None) -> float:
    """30 s * 2^(attempt-1), plus 0-30 s of jitter, so retries don't arrive in a herd."""
    jitter = (rng or random).uniform(0, BACKOFF_JITTER_SECONDS)
    return BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) + jitter


def claim_task(
    session: Session,
    worker_id: str,
    *,
    per_account_concurrency: int,
    max_priority: int = 1,
    now: datetime | None = None,
) -> ScanTask | None:
    """Claim the next runnable task, or return None when there is nothing to do.

    `max_priority=0` is for the threads reserved for on-demand work. The per-account cap is
    soft: two workers claiming at the same moment can overshoot it by one.
    """
    now = now or utcnow()
    statement = select(ScanTask).from_statement(
        CLAIM_SQL.bindparams(
            worker_id=worker_id,
            now=now,
            max_priority=max_priority,
            per_account_concurrency=per_account_concurrency,
        )
    )
    # populate_existing: a task this session already knows must show its claimed state.
    task = session.scalars(statement, execution_options={"populate_existing": True}).first()
    if task is None:
        session.rollback()
        return None
    session.connection().execute(
        text("""
        UPDATE t_inv_aws_scan_request
           SET status = 'running', started_at = coalesce(started_at, :now)
         WHERE id = :id AND status = 'queued'
        """),
        {"id": task.scan_request_id, "now": now},
    )
    session.expunge(task)
    session.commit()
    return task


def heartbeat(
    session: Session, worker_id: str, task_ids: Collection[int], now: datetime | None = None
) -> set[int]:
    """Refresh the heartbeat of the given tasks; return those this worker still owns.

    A task missing from the result was reaped or cancelled under the worker's feet.
    """
    if not task_ids:
        return set()
    rows = session.connection().execute(
        text("""
        UPDATE t_inv_aws_scan_task SET heartbeat_at = :now
         WHERE id = ANY(:ids) AND status = 'running' AND worker_id = :worker_id
        RETURNING id
        """),
        {"ids": list(task_ids), "worker_id": worker_id, "now": now or utcnow()},
    )
    owned = {row.id for row in rows}
    session.commit()
    return owned


def finish_task(
    session: Session,
    task_id: int,
    status: str,
    *,
    worker_id: str | None = None,
    stats: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Move a *running* task to a final status and roll up the scan requests it affects.

    Returns False when the task is no longer running (or no longer ours), in which case
    nothing changes.
    """
    now = now or utcnow()
    row = (
        session.connection()
        .execute(
            text("""
        UPDATE t_inv_aws_scan_task
           SET status = :status, finished_at = :now,
               stats = coalesce(CAST(:stats AS jsonb), stats),
               error_code = :error_code, error_message = :error_message
         WHERE id = :id AND status = 'running'
           AND (CAST(:worker_id AS text) IS NULL OR worker_id = :worker_id)
        RETURNING scan_request_id
        """),
            {
                "id": task_id,
                "status": status,
                "now": now,
                "stats": None if stats is None else _json(stats),
                "error_code": error_code,
                "error_message": (error_message or "")[:2000] or None,
                "worker_id": worker_id,
            },
        )
        .first()
    )
    if row is None:
        session.rollback()
        return False
    roll_up_requests(session, task_id, row.scan_request_id, now)
    session.commit()
    return True


def complete_task(
    session: Session,
    task_id: int,
    worker_id: str,
    stats: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    return finish_task(session, task_id, "succeeded", worker_id=worker_id, stats=stats, now=now)


def fail_task(
    session: Session,
    task: ScanTask,
    error_code: str,
    error_message: str,
    *,
    retryable: bool,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> str | None:
    """Fail a running task: back to `queued` with backoff while attempts remain, else `failed`.

    Returns the new status, or None when the task is no longer ours.
    """
    now = now or utcnow()
    assert task.id is not None
    if retryable and task.attempt < task.max_attempts:
        retry_at = now + timedelta(seconds=backoff_seconds(task.attempt, rng))
        updated = session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_task
               SET status = 'queued', not_before = :retry_at, worker_id = NULL,
                   heartbeat_at = NULL, error_code = :error_code, error_message = :message
             WHERE id = :id AND status = 'running' AND worker_id = :worker_id
            """),
            {
                "id": task.id,
                "retry_at": retry_at,
                "error_code": error_code,
                "message": error_message[:2000],
                "worker_id": task.worker_id,
            },
        )
        session.commit()
        return "queued" if updated.rowcount else None
    done = finish_task(
        session,
        task.id,
        "failed",
        worker_id=task.worker_id,
        error_code=error_code,
        error_message=error_message,
        now=now,
    )
    return "failed" if done else None


def requeue_task(session: Session, task: ScanTask, now: datetime | None = None) -> bool:
    """Put a running task back without counting the interrupted run (graceful stop, §11)."""
    updated = session.connection().execute(
        text("""
        UPDATE t_inv_aws_scan_task
           SET status = 'queued', attempt = greatest(attempt - 1, 0), not_before = :now,
               worker_id = NULL, heartbeat_at = NULL, started_at = NULL
         WHERE id = :id AND status = 'running' AND worker_id = :worker_id
        """),
        {"id": task.id, "worker_id": task.worker_id, "now": now or utcnow()},
    )
    session.commit()
    return bool(updated.rowcount)


@dataclass(frozen=True)
class ReapResult:
    requeued: list[int]
    failed: list[int]


def reap_lost_tasks(
    session: Session, now: datetime | None = None, timeout: timedelta = HEARTBEAT_TIMEOUT
) -> ReapResult:
    """Recover tasks whose worker stopped heartbeating (R9).

    `attempt` was already counted when the task was claimed, so a lost run costs one attempt
    without any further bump. Scope leases are left alone: they expire by themselves five
    minutes after the last heartbeat, which is safer than freeing a lease whose worker might
    only be stalled.
    """
    now = now or utcnow()
    rows = (
        session.connection()
        .execute(
            text("""
        UPDATE t_inv_aws_scan_task
           SET status = CASE WHEN attempt >= max_attempts THEN 'failed' ELSE 'queued' END,
               finished_at = CASE WHEN attempt >= max_attempts THEN :now END,
               error_code = 'worker_lost', error_message = 'worker heartbeat timed out',
               not_before = :now, worker_id = NULL, heartbeat_at = NULL
         WHERE status = 'running' AND heartbeat_at < :cutoff
        RETURNING id, status, scan_request_id
        """),
            {"now": now, "cutoff": now - timeout},
        )
        .all()
    )
    for row in rows:
        if row.status == "failed":
            roll_up_requests(session, row.id, row.scan_request_id, now)
    session.commit()
    return ReapResult(
        requeued=[row.id for row in rows if row.status == "queued"],
        failed=[row.id for row in rows if row.status == "failed"],
    )


def is_cancelled(session: Session, scan_request_id: int) -> bool:
    status = (
        session.connection()
        .execute(
            text("SELECT status FROM t_inv_aws_scan_request WHERE id = :id"),
            {"id": scan_request_id},
        )
        .scalar()
    )
    session.rollback()
    return status == "cancelled"


def cancel_request(
    session: Session, scan_request_id: int, now: datetime | None = None
) -> ScanRequest | None:
    """Cancel a scan request (§3.3). Returns None when the request does not exist.

    Queued tasks become `cancelled`; running tasks notice at their next page boundary through
    `is_cancelled`. A queued task that another request's coalesced task is waiting on is left
    alone, so cancelling one request never strands another. Cancelling a finished request
    changes nothing.
    """
    now = now or utcnow()
    request = session.exec(
        select(ScanRequest).where(ScanRequest.id == scan_request_id).with_for_update()
    ).first()
    if request is None:
        session.rollback()
        return None
    if request.status in TERMINAL_REQUEST_STATUSES:
        session.rollback()
        return request
    cancelled_ids = [
        row.id
        for row in session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_task t
               SET status = 'cancelled', finished_at = :now
             WHERE t.scan_request_id = :id AND t.status = 'queued'
               AND NOT EXISTS (SELECT 1 FROM t_inv_aws_scan_task c
                                WHERE c.coalesced_into = t.id
                                  AND c.scan_request_id <> t.scan_request_id)
            RETURNING t.id
            """),
            {"id": scan_request_id, "now": now},
        )
    ]
    if cancelled_ids:
        session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_shard SET pending_task_id = NULL
             WHERE pending_task_id = ANY(:ids)
            """),
            {"ids": cancelled_ids},
        )
    request.status = "cancelled"
    request.finished_at = now
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


# The final status of a task, following `coalesced_into` to the task that actually runs.
_EFFECTIVE_STATUS_SQL = """
WITH RECURSIVE chain(task_id, tip_id) AS (
    SELECT id, id FROM t_inv_aws_scan_task WHERE scan_request_id = :request_id
  UNION ALL
    SELECT c.task_id, t.coalesced_into
      FROM chain c JOIN t_inv_aws_scan_task t ON t.id = c.tip_id
     WHERE t.status = 'coalesced' AND t.coalesced_into IS NOT NULL
)
SELECT tip.status AS status, count(*) AS n
  FROM chain c JOIN t_inv_aws_scan_task tip ON tip.id = c.tip_id
 WHERE NOT (tip.status = 'coalesced' AND tip.coalesced_into IS NOT NULL)
 GROUP BY tip.status
"""

_DEPENDENT_REQUESTS_SQL = """
WITH RECURSIVE dep(id) AS (
    SELECT id FROM t_inv_aws_scan_task WHERE coalesced_into = :task_id
  UNION
    SELECT t.id FROM t_inv_aws_scan_task t JOIN dep ON t.coalesced_into = dep.id
)
SELECT DISTINCT scan_request_id FROM t_inv_aws_scan_task WHERE id IN (SELECT id FROM dep)
"""


def roll_up_requests(
    session: Session, task_id: int, scan_request_id: int, now: datetime | None = None
) -> None:
    """Re-evaluate the request of a task that just ended, and the requests of every task that
    was coalesced into it."""
    request_ids = {scan_request_id}
    request_ids.update(
        row.scan_request_id
        for row in session.connection().execute(text(_DEPENDENT_REQUESTS_SQL), {"task_id": task_id})
    )
    for request_id in sorted(request_ids):
        roll_up_request(session, request_id, now)


def roll_up_request(session: Session, scan_request_id: int, now: datetime | None = None) -> str:
    """Derive a request's status from its tasks (§2). Returns the request's status.

    All tasks done: no failure -> `succeeded`; failures and successes -> `partial`; only
    failures -> `failed`. `skipped` and `coalesced` count as success: the data they stand for
    was produced by another run. A cancelled request keeps `cancelled`.
    """
    now = now or utcnow()
    current = (
        session.connection()
        .execute(
            text("SELECT status FROM t_inv_aws_scan_request WHERE id = :id FOR UPDATE"),
            {"id": scan_request_id},
        )
        .scalar()
    )
    if current is None or current == "cancelled":
        return current or "missing"
    counts = {
        row.status: row.n
        for row in session.connection().execute(
            text(_EFFECTIVE_STATUS_SQL), {"request_id": scan_request_id}
        )
    }
    if counts.get("queued") or counts.get("running"):
        return current
    bad = counts.get("failed", 0) + counts.get("cancelled", 0)
    good = sum(counts.values()) - bad
    status = "succeeded" if not bad else ("failed" if not good else "partial")
    session.connection().execute(
        text("""
        UPDATE t_inv_aws_scan_request
           SET status = :status, finished_at = :now, started_at = coalesce(started_at, :now)
         WHERE id = :id
        """),
        {"id": scan_request_id, "status": status, "now": now},
    )
    return status


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)
