"""The task queue: `t_inv_aws_scan_task` is the queue (scanner.md §2, §3).

Conventions:
- Every function takes a `Session`, does its work in one transaction and commits it.
- Time comes from the Python clock (`utcnow()`); every function that reads the clock accepts
  `now` so tests can move time without sleeping.

Execution fencing: every claim stamps the task with a fresh `claim_token`. A worker proves it
still owns the task by presenting that token (heartbeat, lease calls, checkpoint, finish);
once the reaper requeues the task and another worker claims it, the old token matches nothing
and the old worker's calls are refused.

Lock order (row locks, one global order, so no two transactions can wait on each other):

    1. scan_shard rows, by (account_id, region, resource_type)
    2. scan_task rows, by id ascending
    3. scan_request rows, by id ascending

A transaction takes the locks it needs in that order and never goes back to an earlier level:
`cancel_request` locks shards, then tasks, then the request; `claim_task` and `finish_task`
lock a task, then requests; `park_pending` and `release_lease` lock the shard before any task.
Functions that lock several rows of one kind lock them in the order above (`ORDER BY ... FOR
UPDATE`) before updating.
"""

import json
import random
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from app.models import ScanRequest, ScanTask
from app.models.scan import utcnow
from app.worker.errors import TaskLost

HEARTBEAT_TIMEOUT = timedelta(minutes=2)
BACKOFF_BASE_SECONDS = 30
BACKOFF_JITTER_SECONDS = 30

TERMINAL_REQUEST_STATUSES = ("succeeded", "partial", "failed", "cancelled")
RESOURCE_COUNTS = ("new", "updated", "missing", "deleted")

# A claimed task that `claim_task` returns is detached from the session, so the caller can
# keep using it after the transaction has ended.

CLAIM_SQL = text("""
UPDATE t_inv_aws_scan_task
   SET status = 'running', worker_id = :worker_id, claim_token = :claim_token,
       attempt = attempt + 1, started_at = :now, heartbeat_at = :now, finished_at = NULL
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

    The task comes back with a fresh `claim_token`. `max_priority=0` is for the threads
    reserved for on-demand work. The per-account cap is soft: two workers claiming at the same
    moment can overshoot it by one.
    """
    now = now or utcnow()
    statement = select(ScanTask).from_statement(
        CLAIM_SQL.bindparams(
            worker_id=worker_id,
            claim_token=uuid.uuid4().hex,
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


def assert_owned(session: Session, task: ScanTask) -> None:
    """Raise `TaskLost` unless `task` is still running under the claim the caller holds."""
    row = (
        session.connection()
        .execute(
            text("SELECT status, claim_token FROM t_inv_aws_scan_task WHERE id = :id"),
            {"id": task.id},
        )
        .first()
    )
    session.rollback()
    if (
        row is None
        or row.status != "running"
        or task.claim_token is None
        or row.claim_token != task.claim_token
    ):
        raise TaskLost


def heartbeat(session: Session, claims: Mapping[int, str], now: datetime | None = None) -> set[int]:
    """Refresh the heartbeat of the given claims ({task id: claim token}); return the ids
    whose claim is still current.

    A task missing from the result was reaped, claimed again or finished under the worker's feet.
    """
    if not claims:
        return set()
    ids = sorted(claims)
    rows = session.connection().execute(
        text("""
        WITH mine AS (
            SELECT t.id FROM t_inv_aws_scan_task t
              JOIN unnest(CAST(:ids AS bigint[]), CAST(:tokens AS text[])) AS c(id, token)
                ON c.id = t.id
             WHERE t.status = 'running' AND t.claim_token = c.token
             ORDER BY t.id
               FOR UPDATE OF t)
        UPDATE t_inv_aws_scan_task SET heartbeat_at = :now
         WHERE id IN (SELECT id FROM mine)
        RETURNING id
        """),
        {"ids": ids, "tokens": [claims[i] for i in ids], "now": now or utcnow()},
    )
    owned = {row.id for row in rows}
    session.commit()
    return owned


def finish_task(
    session: Session,
    task_id: int,
    status: str,
    *,
    claim_token: str,
    stats: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Move a *running* task to a final status and roll up the scan requests it affects.

    Returns False when the task is no longer running under `claim_token`, in which case
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
         WHERE id = :id AND status = 'running' AND claim_token = :claim_token
        RETURNING scan_request_id
        """),
            {
                "id": task_id,
                "status": status,
                "now": now,
                "stats": None if stats is None else _json(stats),
                "error_code": error_code,
                "error_message": (error_message or "")[:2000] or None,
                "claim_token": claim_token,
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
    claim_token: str,
    stats: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    return finish_task(session, task_id, "succeeded", claim_token=claim_token, stats=stats, now=now)


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
    claim_token = task.claim_token or ""
    if retryable and task.attempt < task.max_attempts:
        retry_at = now + timedelta(seconds=backoff_seconds(task.attempt, rng))
        updated = session.connection().execute(
            text("""
            UPDATE t_inv_aws_scan_task
               SET status = 'queued', not_before = :retry_at, worker_id = NULL,
                   claim_token = NULL, heartbeat_at = NULL,
                   error_code = :error_code, error_message = :message
             WHERE id = :id AND status = 'running' AND claim_token = :claim_token
            """),
            {
                "id": task.id,
                "retry_at": retry_at,
                "error_code": error_code,
                "message": error_message[:2000],
                "claim_token": claim_token,
            },
        )
        session.commit()
        return "queued" if updated.rowcount else None
    done = finish_task(
        session,
        task.id,
        "failed",
        claim_token=claim_token,
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
               worker_id = NULL, claim_token = NULL, heartbeat_at = NULL, started_at = NULL
         WHERE id = :id AND status = 'running' AND claim_token = :claim_token
        """),
        {"id": task.id, "claim_token": task.claim_token or "", "now": now or utcnow()},
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
    without any further bump. The claim token is cleared, which fences the old worker out the
    moment the task is claimed again. Scope leases are left alone: they expire by themselves
    five minutes after the last heartbeat, which is safer than freeing a lease whose worker
    might only be stalled; the next claimant of the same task takes it over.
    """
    now = now or utcnow()
    rows = (
        session.connection()
        .execute(
            text("""
        WITH lost AS (
            SELECT id FROM t_inv_aws_scan_task
             WHERE status = 'running' AND heartbeat_at < :cutoff
             ORDER BY id FOR UPDATE)
        UPDATE t_inv_aws_scan_task t
           SET status = CASE WHEN attempt >= max_attempts THEN 'failed' ELSE 'queued' END,
               finished_at = CASE WHEN attempt >= max_attempts THEN :now END,
               error_code = 'worker_lost', error_message = 'worker heartbeat timed out',
               not_before = :now, worker_id = NULL, claim_token = NULL, heartbeat_at = NULL
          FROM lost
         WHERE t.id = lost.id
        RETURNING t.id, t.status, t.scan_request_id
        """),
            {"now": now, "cutoff": now - timeout},
        )
        .all()
    )
    failed = sorted((row.id, row.scan_request_id) for row in rows if row.status == "failed")
    _roll_up_many(session, failed, now)
    session.commit()
    return ReapResult(
        requeued=[row.id for row in rows if row.status == "queued"],
        failed=[task_id for task_id, _request_id in failed],
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


_NEEDED_SQL = """
WITH RECURSIVE dep(id) AS (
    SELECT CAST(:task_id AS bigint)
  UNION
    SELECT t.id FROM t_inv_aws_scan_task t JOIN dep ON t.coalesced_into = dep.id
)
SELECT EXISTS (
    SELECT 1 FROM dep
      JOIN t_inv_aws_scan_task d ON d.id = dep.id
      JOIN t_inv_aws_scan_request r ON r.id = d.scan_request_id
     WHERE r.status <> 'cancelled')
"""


def is_needed(session: Session, task: ScanTask) -> bool:
    """Does any request that is not cancelled still depend on this task?

    That is the task's own request or the request of any task coalesced into it (directly or
    through a chain). A shared task keeps running while one of them is alive and stops only
    when all of them are cancelled (§3.3).
    """
    needed = session.connection().execute(text(_NEEDED_SQL), {"task_id": task.id}).scalar()
    session.rollback()
    return bool(needed)


_CANCEL_UNNEEDED_SQL = """
WITH RECURSIVE dep(root, id) AS (
    SELECT t.id, t.id FROM t_inv_aws_scan_task t WHERE t.id = ANY(:ids)
  UNION
    SELECT dep.root, c.id FROM dep JOIN t_inv_aws_scan_task c ON c.coalesced_into = dep.id
)
UPDATE t_inv_aws_scan_task t
   SET status = 'cancelled', finished_at = :now
 WHERE t.id = ANY(:ids) AND t.status = 'queued'
   AND NOT EXISTS (SELECT 1 FROM dep
                     JOIN t_inv_aws_scan_task d ON d.id = dep.id
                     JOIN t_inv_aws_scan_request r ON r.id = d.scan_request_id
                    WHERE dep.root = t.id AND r.status <> 'cancelled')
RETURNING t.id
"""


def cancel_request(
    session: Session, scan_request_id: int, now: datetime | None = None
) -> ScanRequest | None:
    """Cancel a scan request (§3.3). Returns None when the request does not exist.

    Queued tasks become `cancelled`; running tasks notice at their next page boundary through
    `is_needed`. A queued task that another live request's coalesced task is waiting on is left
    alone, so cancelling one request never strands another; it is cancelled later, when the
    last request depending on it is. Cancelling a finished request changes nothing.

    Locks in the module's order: shards, tasks, then the request.
    """
    now = now or utcnow()
    status = (
        session.connection()
        .execute(
            text("SELECT status FROM t_inv_aws_scan_request WHERE id = :id"),
            {"id": scan_request_id},
        )
        .scalar()
    )
    if status is None:
        session.rollback()
        return None
    # Candidates: this request's queued tasks, plus queued tasks of already cancelled requests
    # that were kept alive for this request and may now be orphaned.
    candidates = [
        row.id
        for row in session.connection().execute(
            text("""
            SELECT t.id FROM t_inv_aws_scan_task t
              JOIN t_inv_aws_scan_request r ON r.id = t.scan_request_id
             WHERE t.status = 'queued' AND (t.scan_request_id = :id OR r.status = 'cancelled')
            """),
            {"id": scan_request_id},
        )
    ]
    session.connection().execute(
        text("""
        SELECT 1 FROM t_inv_aws_scan_shard WHERE pending_task_id = ANY(:ids)
         ORDER BY account_id, region, resource_type FOR UPDATE
        """),
        {"ids": candidates},
    ).all()
    session.connection().execute(
        text("SELECT id FROM t_inv_aws_scan_task WHERE id = ANY(:ids) ORDER BY id FOR UPDATE"),
        {"ids": candidates},
    ).all()
    request = session.exec(
        select(ScanRequest).where(ScanRequest.id == scan_request_id).with_for_update()
    ).first()
    assert request is not None
    if request.status in TERMINAL_REQUEST_STATUSES:
        session.rollback()
        return request
    request.status = "cancelled"
    request.finished_at = now
    session.add(request)
    session.flush()
    cancelled_ids = [
        row.id
        for row in session.connection().execute(
            text(_CANCEL_UNNEEDED_SQL), {"ids": candidates, "now": now}
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

# Per status: how many of the request's own tasks, and the resource deltas their stats carry.
_COUNTS_SQL = """
SELECT status, count(*) AS n,
       count(*) FILTER (WHERE jsonb_exists_any(stats, CAST(:keys AS text[]))) AS with_stats,
       coalesce(sum(CASE WHEN jsonb_typeof(stats -> 'new') = 'number'
                         THEN (stats ->> 'new')::bigint END), 0) AS new,
       coalesce(sum(CASE WHEN jsonb_typeof(stats -> 'updated') = 'number'
                         THEN (stats ->> 'updated')::bigint END), 0) AS updated,
       coalesce(sum(CASE WHEN jsonb_typeof(stats -> 'missing') = 'number'
                         THEN (stats ->> 'missing')::bigint END), 0) AS missing,
       coalesce(sum(CASE WHEN jsonb_typeof(stats -> 'deleted') = 'number'
                         THEN (stats ->> 'deleted')::bigint END), 0) AS deleted
  FROM t_inv_aws_scan_task WHERE scan_request_id = :id GROUP BY status
"""


def roll_up_requests(
    session: Session, task_id: int, scan_request_id: int, now: datetime | None = None
) -> None:
    """Re-evaluate the request of a task that just ended, and the requests of every task that
    was coalesced into it."""
    _roll_up_many(session, [(task_id, scan_request_id)], now)


def _roll_up_many(
    session: Session, tasks: Collection[tuple[int, int]], now: datetime | None = None
) -> None:
    """Roll up the requests of several ended tasks, locking the requests in id order."""
    request_ids: set[int] = set()
    for task_id, scan_request_id in tasks:
        request_ids.add(scan_request_id)
        request_ids.update(
            row.scan_request_id
            for row in session.connection().execute(
                text(_DEPENDENT_REQUESTS_SQL), {"task_id": task_id}
            )
        )
    for request_id in sorted(request_ids):
        roll_up_request(session, request_id, now)


def roll_up_request(session: Session, scan_request_id: int, now: datetime | None = None) -> str:
    """Derive a request's status from its tasks (§2) and save its counts. Returns the status.

    All tasks done: no failure -> `succeeded`; failures and successes -> `partial`; only
    failures -> `failed`. `skipped` and `coalesced` count as success: the data they stand for
    was produced by another run. A cancelled request keeps `cancelled`.

    `counts.tasks` (tasks by status) and `counts.resources` (new / updated / missing / deleted,
    summed from the tasks' `stats` when any task reports them) are written on every call; other
    keys of `counts` (`registryError`, `accounts`, ...) are kept.
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
    if current is None:
        return "missing"
    _save_counts(session, scan_request_id)
    if current == "cancelled":
        return current
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


def _save_counts(session: Session, scan_request_id: int) -> None:
    rows = (
        session.connection()
        .execute(text(_COUNTS_SQL), {"id": scan_request_id, "keys": list(RESOURCE_COUNTS)})
        .all()
    )
    patch: dict[str, Any] = {"tasks": {row.status: row.n for row in rows}}
    if any(row.with_stats for row in rows):
        patch["resources"] = {
            name: int(sum(getattr(row, name) for row in rows)) for name in RESOURCE_COUNTS
        }
    session.connection().execute(
        text("""
        UPDATE t_inv_aws_scan_request
           SET counts = coalesce(counts, '{}'::jsonb) || CAST(:patch AS jsonb)
         WHERE id = :id
        """),
        {"id": scan_request_id, "patch": _json(patch)},
    )


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)
