"""Daily retention cleanup (scanner.md §12).

Deletes happen in batches of at most `BATCH_SIZE` rows per statement, one transaction each,
so a large backlog never holds a long transaction.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.core.config import Settings
from app.models.scan import utcnow

logger = logging.getLogger(__name__)

BATCH_SIZE = 10_000


@dataclass(frozen=True)
class CleanupResult:
    tasks: int
    requests: int
    resources: int
    accounts: int


def run_cleanup(session: Session, settings: Settings, now: datetime | None = None) -> CleanupResult:
    now = now or utcnow()
    result = CleanupResult(
        tasks=_purge_tasks(session, now - timedelta(days=settings.TASK_RETENTION_DAYS)),
        requests=_purge_requests(session, now - timedelta(days=settings.REQUEST_RETENTION_DAYS)),
        resources=_purge_deleted_resources(
            session, now - timedelta(days=settings.DELETED_PURGE_DAYS)
        ),
        accounts=_purge_inactive_accounts(
            session, now - timedelta(days=settings.INACTIVE_ACCOUNT_PURGE_DAYS)
        ),
    )
    logger.info("cleanup finished", extra={"deleted": result.__dict__})
    return result


def _delete_in_batches(session: Session, statement: str, params: dict[str, Any]) -> int:
    """Run a statement that deletes at most :batch rows until it deletes fewer."""
    total = 0
    while True:
        deleted = (
            session.connection().execute(text(statement), {**params, "batch": BATCH_SIZE}).rowcount
        )
        session.commit()
        total += deleted
        if deleted < BATCH_SIZE:
            return total


def _purge_tasks(session: Session, cutoff: datetime) -> int:
    total = 0
    while True:
        ids = [
            row.id
            for row in session.connection().execute(
                text(
                    "SELECT id FROM t_inv_aws_scan_task WHERE finished_at < :cutoff"
                    " AND status NOT IN ('queued', 'running') ORDER BY id LIMIT :batch"
                ),
                {"cutoff": cutoff, "batch": BATCH_SIZE},
            )
        ]
        if not ids:
            session.rollback()
            return total
        # Retry may have changed a candidate since the first SELECT. Lock both candidates
        # and their dependants in task-id order, then recheck retention under those locks.
        locked = (
            session.connection()
            .execute(
                text("""
            SELECT id, status, finished_at FROM t_inv_aws_scan_task
             WHERE id = ANY(:ids) OR coalesced_into = ANY(:ids)
             ORDER BY id FOR UPDATE
            """),
                {"ids": ids},
            )
            .all()
        )
        candidates = set(ids)
        ids = [
            row.id
            for row in locked
            if row.id in candidates
            and row.status not in ("queued", "running")
            and row.finished_at is not None
            and row.finished_at < cutoff
        ]
        if not ids:
            session.commit()
            continue
        # Tasks coalesced into a deleted task keep living for a while; detach them first.
        session.connection().execute(
            text(
                "UPDATE t_inv_aws_scan_task SET coalesced_into = NULL"
                " WHERE coalesced_into = ANY(:ids)"
            ),
            {"ids": ids},
        )
        session.connection().execute(
            text("DELETE FROM t_inv_aws_scan_task WHERE id = ANY(:ids)"), {"ids": ids}
        )
        session.commit()
        total += len(ids)


def _purge_requests(session: Session, cutoff: datetime) -> int:
    return _delete_in_batches(
        session,
        """
        DELETE FROM t_inv_aws_scan_request WHERE id IN (
            SELECT r.id FROM t_inv_aws_scan_request r
             WHERE r.finished_at < :cutoff
               AND NOT EXISTS (SELECT 1 FROM t_inv_aws_scan_task t WHERE t.scan_request_id = r.id)
             LIMIT :batch)
        """,
        {"cutoff": cutoff},
    )


def _purge_deleted_resources(session: Session, cutoff: datetime) -> int:
    return _delete_in_batches(
        session,
        """
        DELETE FROM t_inv_aws_resource WHERE id IN (
            SELECT id FROM t_inv_aws_resource
             WHERE status = 'deleted' AND deleted_at < :cutoff LIMIT :batch)
        """,
        {"cutoff": cutoff},
    )


def _purge_inactive_accounts(session: Session, cutoff: datetime) -> int:
    expired = [
        row.account_id
        for row in session.connection().execute(
            text(
                "SELECT account_id FROM t_inv_aws_account"
                " WHERE NOT active AND inactive_since < :cutoff"
            ),
            {"cutoff": cutoff},
        )
    ]
    session.rollback()
    for account_id in expired:
        _delete_in_batches(
            session,
            """
            DELETE FROM t_inv_aws_resource WHERE id IN (
                SELECT id FROM t_inv_aws_resource WHERE account_id = :account_id LIMIT :batch)
            """,
            {"account_id": account_id},
        )
        for table in ("t_inv_aws_account_region", "t_inv_aws_scan_shard", "t_inv_aws_account"):
            session.connection().execute(
                text(f"DELETE FROM {table} WHERE account_id = :account_id"),  # noqa: S608
                {"account_id": account_id},
            )
        session.commit()
    return len(expired)
