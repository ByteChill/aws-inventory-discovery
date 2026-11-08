"""Creating and managing scan requests: the service functions behind `POST /scans`,
`POST /resources/refresh` and the scheduler (DESIGN 7.2 R7, scanner.md §5).

Pure database logic. Each function runs in one transaction and commits. `cancel_request`
lives in `queue.py` next to the rest of the task lifecycle.
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, NamedTuple, Self

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.config import Settings
from app.models import Account, ScanRequest, ScanTask
from app.models.scan import utcnow
from app.worker.locks import lock_full_sweeps

SWEEP_SAFETY_MARGIN = timedelta(minutes=30)
ACCOUNT_ID_PATTERN = r"^\d{12}$"


class ScopeTooLarge(Exception):
    def __init__(self, limit: int, requested: int) -> None:
        super().__init__(f"a scan may cover at most {limit} accounts, {requested} requested")
        self.limit = limit
        self.requested = requested


class IdempotencyConflict(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"Idempotency-Key {key!r} was already used with a different request body")
        self.key = key


class UnknownAccounts(Exception):
    def __init__(self, account_ids: list[str]) -> None:
        super().__init__(f"unknown or inactive accounts: {', '.join(account_ids)}")
        self.account_ids = account_ids


class InvalidRequestState(Exception):
    """The operation does not apply to the request in its current status."""


class ScanSpec(BaseModel):
    """The body of `POST /scans`: either a list of accounts or `all_accounts`."""

    account_ids: list[str] = Field(default_factory=list)
    aws_regions: list[str] = Field(default_factory=list)
    resource_types: list[str] = Field(default_factory=list)
    all_accounts: bool = False

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        if self.all_accounts == bool(self.account_ids):
            raise ValueError("give either account_ids or all_accounts=true")
        if self.all_accounts and (self.aws_regions or self.resource_types):
            raise ValueError("all_accounts cannot be narrowed by region or resource type")
        for account_id in self.account_ids:
            if len(account_id) != 12 or not account_id.isdigit():
                raise ValueError(f"invalid account id {account_id!r}")
        return self

    def canonical(self) -> dict[str, Any]:
        """The normalised form stored on the request; two equal bodies give equal dicts."""
        return {
            "all": self.all_accounts,
            "accountIds": sorted(set(self.account_ids)),
            "awsRegions": sorted(set(self.aws_regions)),
            "resourceTypes": sorted(set(self.resource_types)),
        }


class CreatedScan(NamedTuple):
    request: ScanRequest
    created: bool  # False: an existing request was returned instead


class ResourceKey(NamedTuple):
    account_id: str
    aws_region: str
    resource_type: str
    resource_id: str


class CreatedRefresh(NamedTuple):
    task: ScanTask
    created: bool  # False: an in-flight refresh of the same resource is shared


class NewTask(NamedTuple):
    """A child task to add to an existing request (planned by `sweep_account`)."""

    kind: str
    account_id: str
    region: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    source: str | None = None
    not_before: datetime | None = None


def stagger_window_seconds(settings: Settings) -> int:
    """Sweep interval minus 30 minutes, so the last account can finish before the next sweep."""
    interval = timedelta(hours=settings.SWEEP_INTERVAL_HOURS)
    return max(int((interval - SWEEP_SAFETY_MARGIN).total_seconds()), 1)


def stagger_offset(account_id: str, window_seconds: int) -> timedelta:
    """A stable position of the account inside the window. Python's `hash()` is randomised per
    process, so the digest of the id is used instead."""
    digest = hashlib.sha256(account_id.encode()).digest()
    return timedelta(seconds=int.from_bytes(digest[:8], "big") % window_seconds)


def create_sweep_request(
    session: Session,
    account_ids: Sequence[str],
    *,
    trigger: str,
    priority: int,
    scope: dict[str, Any],
    caller_id: str | None = None,
    idempotency_key: str | None = None,
    stagger_window: int | None = None,
    skip_in_flight: bool = False,
    counts: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ScanRequest:
    """Create a request with one `sweep_account` task per account (not yet committed).

    With `stagger_window` (seconds) each task starts at its account's stable offset inside the
    window; otherwise at once. With `skip_in_flight` accounts whose previous `sweep_account`
    is still queued or running are left out. A request that ends up with no tasks is
    `succeeded` immediately, so it never sits in `queued` forever.
    """
    now = now or utcnow()
    skipped: list[str] = []
    accounts = list(dict.fromkeys(account_ids))
    if skip_in_flight and accounts:
        busy = set(
            session.exec(
                select(ScanTask.account_id).where(
                    ScanTask.kind == "sweep_account",
                    col(ScanTask.status).in_(("queued", "running")),
                    col(ScanTask.account_id).in_(accounts),
                )
            )
        )
        skipped = [a for a in accounts if a in busy]
        accounts = [a for a in accounts if a not in busy]
    request = ScanRequest(
        trigger=trigger,
        caller_id=caller_id,
        scope=scope,
        priority=priority,
        status="queued" if accounts else "succeeded",
        counts={**(counts or {}), "accounts": len(accounts), "skippedAccounts": len(skipped)},
        idempotency_key=idempotency_key,
        created_at=now,
        started_at=None if accounts else now,
        finished_at=None if accounts else now,
    )
    session.add(request)
    session.flush()
    assert request.id is not None
    for account_id in accounts:
        offset = stagger_offset(account_id, stagger_window) if stagger_window else timedelta()
        session.add(
            ScanTask(
                scan_request_id=request.id,
                kind="sweep_account",
                account_id=account_id,
                priority=priority,
                status="queued",
                not_before=now + offset,
                created_at=now,
            )
        )
    session.flush()
    return request


def create_scan(
    session: Session,
    spec: ScanSpec,
    settings: Settings,
    *,
    caller_id: str | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> CreatedScan:
    """Handle `POST /scans`.

    - Same `Idempotency-Key` and same body: the original request; different body:
      `IdempotencyConflict` (R7).
    - `all_accounts`: an already queued or running sweep of all accounts is returned as is
      (§5.2); otherwise a priority 0 sweep starts at once.
    - A list of accounts: more than `MAX_SCAN_ACCOUNTS` raises `ScopeTooLarge`, unknown or
      inactive accounts raise `UnknownAccounts`. Each account gets an on-demand
      `sweep_account` (priority 0, so every later stage reads live sources only); the regions
      and resource types to cover are kept in the request's `scope`.
    """
    now = now or utcnow()
    scope = spec.canonical()
    if idempotency_key is not None:
        existing = _by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return CreatedScan(_same_body_or_conflict(existing, scope, idempotency_key), False)
    if spec.all_accounts:
        # Check-then-insert must not interleave with another full sweep being created (the
        # lock is released by the commit below or by the rollback of an early return).
        lock_full_sweeps(session)
        running = (
            session.connection()
            .execute(
                text("""
            SELECT id FROM t_inv_aws_scan_request
             WHERE status IN ('queued', 'running') AND scope ->> 'all' = 'true'
             ORDER BY id LIMIT 1
            """)
            )
            .scalar()
        )
        if running is not None:
            existing_sweep = session.get_one(ScanRequest, running)
            session.commit()
            session.refresh(existing_sweep)
            return CreatedScan(existing_sweep, False)
        account_ids = list(session.exec(select(Account.account_id).where(col(Account.active))))
        sweep: dict[str, Any] = {"skip_in_flight": True}
    else:
        account_ids = sorted(set(spec.account_ids))
        if len(account_ids) > settings.MAX_SCAN_ACCOUNTS:
            raise ScopeTooLarge(settings.MAX_SCAN_ACCOUNTS, len(account_ids))
        known = set(
            session.exec(
                select(Account.account_id).where(
                    col(Account.account_id).in_(account_ids), col(Account.active)
                )
            )
        )
        if unknown := [a for a in account_ids if a not in known]:
            raise UnknownAccounts(unknown)
        sweep = {}
    try:
        with session.begin_nested():
            request = create_sweep_request(
                session,
                account_ids,
                trigger="api",
                priority=0,
                scope=scope,
                caller_id=caller_id,
                idempotency_key=idempotency_key,
                now=now,
                **sweep,
            )
    except IntegrityError:
        # A concurrent call with the same key won the race.
        existing = _by_idempotency_key(session, idempotency_key) if idempotency_key else None
        if existing is None or idempotency_key is None:
            raise
        return CreatedScan(_same_body_or_conflict(existing, scope, idempotency_key), False)
    session.commit()
    session.refresh(request)
    return CreatedScan(request, True)


def _by_idempotency_key(session: Session, key: str) -> ScanRequest | None:
    return session.exec(select(ScanRequest).where(ScanRequest.idempotency_key == key)).first()


def _same_body_or_conflict(existing: ScanRequest, scope: dict[str, Any], key: str) -> ScanRequest:
    if existing.scope != scope:
        raise IdempotencyConflict(key)
    return existing


def request_resource_refresh(
    session: Session,
    key: ResourceKey,
    *,
    caller_id: str | None = None,
    now: datetime | None = None,
) -> CreatedRefresh:
    """Create a single-resource refresh (kind `resource`, priority 0), or return the one that
    is already queued or running for that resource. The partial unique index
    `uq_t_inv_aws_scan_task_resource_inflight` is the arbiter, so two simultaneous callers
    end up sharing one task and one AWS call (R7)."""
    now = now or utcnow()
    existing = _inflight_refresh(session, key)
    if existing is not None:
        return CreatedRefresh(existing, False)
    try:
        with session.begin_nested():
            request = ScanRequest(
                trigger="api",
                caller_id=caller_id,
                target=key._asdict(),
                priority=0,
                status="queued",
                created_at=now,
            )
            session.add(request)
            session.flush()
            assert request.id is not None
            task = ScanTask(
                scan_request_id=request.id,
                kind="resource",
                account_id=key.account_id,
                region=key.aws_region,
                resource_type=key.resource_type,
                resource_id=key.resource_id,
                priority=0,
                status="queued",
                not_before=now,
                created_at=now,
            )
            session.add(task)
            session.flush()
    except IntegrityError:
        existing = _inflight_refresh(session, key)
        if existing is None:
            raise
        return CreatedRefresh(existing, False)
    session.commit()
    session.refresh(task)
    return CreatedRefresh(task, True)


def _inflight_refresh(session: Session, key: ResourceKey) -> ScanTask | None:
    return session.exec(
        select(ScanTask).where(
            ScanTask.kind == "resource",
            ScanTask.account_id == key.account_id,
            ScanTask.region == key.aws_region,
            ScanTask.resource_type == key.resource_type,
            ScanTask.resource_id == key.resource_id,
            col(ScanTask.status).in_(("queued", "running")),
        )
    ).first()


def retry_failed(session: Session, scan_request_id: int, now: datetime | None = None) -> int:
    """Re-queue the failed tasks of a request (`POST /scans/{id}/retry-failed`).

    Returns how many tasks were re-queued. A failed refresh whose resource is being refreshed
    by a newer task stays failed. A cancelled request can't be retried.
    """
    now = now or utcnow()
    request = session.exec(
        select(ScanRequest).where(ScanRequest.id == scan_request_id).with_for_update()
    ).first()
    if request is None or request.status == "cancelled":
        session.rollback()
        raise InvalidRequestState(
            "scan request not found" if request is None else "a cancelled scan cannot be retried"
        )
    failed = session.exec(
        select(ScanTask).where(
            ScanTask.scan_request_id == scan_request_id, ScanTask.status == "failed"
        )
    ).all()
    requeued = 0
    for task in failed:
        try:
            with session.begin_nested():
                task.status = "queued"
                task.attempt = 0
                task.not_before = now
                task.error_code = task.error_message = None
                task.worker_id = task.heartbeat_at = task.started_at = task.finished_at = None
                session.add(task)
                session.flush()
        except IntegrityError:
            session.refresh(task)
            continue
        requeued += 1
    if requeued:
        request.status = "queued" if request.started_at is None else "running"
        request.finished_at = None
        session.add(request)
    session.commit()
    return requeued


def add_tasks(
    session: Session,
    scan_request_id: int,
    tasks: Sequence[NewTask],
    now: datetime | None = None,
) -> list[ScanTask]:
    """Add queued child tasks to a request; they inherit its priority (so on-demand requests
    only ever produce on-demand tasks)."""
    now = now or utcnow()
    request = session.get_one(ScanRequest, scan_request_id)
    created = [
        ScanTask(
            scan_request_id=scan_request_id,
            kind=new.kind,
            source=new.source,
            account_id=new.account_id,
            region=new.region,
            resource_type=new.resource_type,
            resource_id=new.resource_id,
            priority=request.priority,
            status="queued",
            not_before=new.not_before or now,
            created_at=now,
        )
        for new in tasks
    ]
    session.add_all(created)
    session.commit()
    for task in created:
        session.refresh(task)
    return created
