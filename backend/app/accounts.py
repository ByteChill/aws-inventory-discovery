"""Account master data and scan-policy operations.

The account table is the control-plane source of truth. The registry (service 1) can be
imported once, but an import only inserts unknown accounts: it never overwrites local fields,
never changes a local policy, and never deactivates an account that is absent from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as SASession
from sqlmodel import Session, col, select

from app.aws.account_registry import RegistryAccount
from app.aws.regions import ENABLED
from app.models import Account, AccountRegion, AccountScanRegion
from app.models.account import utcnow
from app.query.search import like_pattern
from app.schemas.accounts import (
    AccountBatchUpdate,
    AccountCreate,
    AccountSelector,
    AccountUpdate,
    RegionScanState,
    ScanRegionMode,
)
from app.worker import cleanup


class AccountExists(Exception):
    def __init__(self, account_id: str) -> None:
        super().__init__(f"account {account_id} already exists")
        self.account_id = account_id


class AccountNotFound(Exception):
    def __init__(self, account_id: str) -> None:
        super().__init__(f"account {account_id} does not exist")
        self.account_id = account_id


class EmptyRegistry(Exception):
    """An empty registry response must not be treated as a complete account list."""


@dataclass(frozen=True)
class ImportResult:
    fetched: int
    imported: int
    skipped: int


@dataclass(frozen=True)
class ScanPolicy:
    active: bool
    scan_enabled: bool
    mode: ScanRegionMode
    selected: frozenset[str]


INSERT_CHUNK_ROWS = 1000


def _insert_rows(session: Session, table: Any, rows: Sequence[dict[str, Any]]) -> None:
    """Insert in bounded chunks: one multi-row statement can exceed PostgreSQL's parameter limit."""
    for start in range(0, len(rows), INSERT_CHUNK_ROWS):
        session.connection().execute(insert(table).values(rows[start : start + INSERT_CHUNK_ROWS]))


def _lock_accounts(session: Session, account_ids: Iterable[str]) -> None:
    """Serialise account writes with the cleanup purge using the existing per-account locks."""
    ids = sorted(set(account_ids))
    if not ids:
        return
    schema = cleanup.schema_of(session)
    keys = sorted({cleanup.account_sync_key(schema, account_id) for account_id in ids})
    session.connection().execute(
        text(
            "SELECT pg_advisory_xact_lock(k) FROM"
            " (SELECT unnest(CAST(:keys AS bigint[])) AS k ORDER BY 1) AS ordered"
        ),
        {"keys": keys},
    )


def _replace_selected_regions(
    session: Session, account_ids: Sequence[str], regions: Sequence[str], now: datetime
) -> None:
    if not account_ids:
        return
    session.connection().execute(
        delete(AccountScanRegion.__table__).where(  # ty: ignore[unresolved-attribute]
            col(AccountScanRegion.account_id).in_(account_ids)
        )
    )
    rows = [
        {"account_id": account_id, "region": region}
        for account_id in account_ids
        for region in sorted(set(regions))
    ]
    _insert_rows(session, AccountScanRegion.__table__, rows)  # ty: ignore[unresolved-attribute]
    session.connection().execute(
        update(Account.__table__)  # ty: ignore[unresolved-attribute]
        .where(col(Account.account_id).in_(account_ids))
        .values(updated_at=now)
    )


def _add_selected_regions(session: Session, account_id: str, regions: Sequence[str]) -> None:
    rows = [{"account_id": account_id, "region": region} for region in sorted(set(regions))]
    _insert_rows(session, AccountScanRegion.__table__, rows)  # ty: ignore[unresolved-attribute]


def create_account(session: Session, body: AccountCreate, now: datetime | None = None) -> None:
    now = now or utcnow()
    _lock_accounts(session, [body.account_id])
    if session.get(Account, body.account_id) is not None:
        raise AccountExists(body.account_id)
    session.add(
        Account(
            account_id=body.account_id,
            account_name=body.account_name,
            account_type=body.account_type,
            eim_id=body.eim_id,
            eim_app=body.eim_app,
            ba_id=body.ba_id,
            active=True,
            inactive_since=None,
            source="manual",
            scan_enabled=body.scan_enabled,
            scan_region_mode=body.scan_region_mode,
            updated_at=now,
        )
    )
    session.flush()
    _add_selected_regions(session, body.account_id, body.selected_regions)
    session.commit()


def update_account(
    session: Session,
    account_id: str,
    body: AccountUpdate,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    _lock_accounts(session, [account_id])
    account = session.exec(
        select(Account).where(Account.account_id == account_id).with_for_update()
    ).first()
    if account is None:
        raise AccountNotFound(account_id)
    for field in (
        "account_name",
        "account_type",
        "eim_id",
        "eim_app",
        "ba_id",
        "scan_enabled",
        "scan_region_mode",
    ):
        if field in body.model_fields_set:
            setattr(account, field, getattr(body, field))
    account.updated_at = now
    session.add(account)
    if "selected_regions" in body.model_fields_set and body.selected_regions is not None:
        session.connection().execute(
            delete(AccountScanRegion.__table__).where(  # ty: ignore[unresolved-attribute]
                col(AccountScanRegion.account_id) == account_id
            )
        )
        _add_selected_regions(session, account_id, body.selected_regions)
    session.commit()


def soft_delete_account(session: Session, account_id: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    _lock_accounts(session, [account_id])
    account = session.exec(
        select(Account).where(Account.account_id == account_id).with_for_update()
    ).first()
    if account is None:
        raise AccountNotFound(account_id)
    if account.active:
        account.active = False
        account.inactive_since = now
    account.updated_at = now
    session.add(account)
    session.commit()


def restore_account(session: Session, account_id: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    _lock_accounts(session, [account_id])
    account = session.exec(
        select(Account).where(Account.account_id == account_id).with_for_update()
    ).first()
    if account is None:
        raise AccountNotFound(account_id)
    account.active = True
    account.inactive_since = None
    account.updated_at = now
    session.add(account)
    session.commit()


def selector_conditions(selector: AccountSelector) -> list[Any]:
    """The SQL conditions of a batch selector. Batch operations always target active accounts."""
    conditions = [col(Account.active)]
    for values, column in (
        (selector.account_ids, col(Account.account_id)),
        (selector.eim_ids, col(Account.eim_id)),
        (selector.eim_apps, col(Account.eim_app)),
        (selector.ba_ids, col(Account.ba_id)),
        (selector.account_types, col(Account.account_type)),
        (selector.access_statuses, col(Account.access_status)),
    ):
        if values:
            conditions.append(column.in_(values))
    if selector.q:
        pattern = like_pattern(selector.q)
        conditions.append(
            or_(
                col(Account.account_name).ilike(pattern),
                col(Account.account_id).ilike(pattern),
            )
        )
    if selector.scan_enabled is not None:
        conditions.append(col(Account.scan_enabled) == selector.scan_enabled)
    return conditions


def _selected_account_ids(session: Session, selector: AccountSelector) -> list[str]:
    return list(
        session.exec(
            select(Account.account_id)
            .where(*selector_conditions(selector))
            .order_by(col(Account.account_id))
        )
    )


def batch_update(
    session: Session,
    body: AccountBatchUpdate,
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    account_ids = _selected_account_ids(session, body.selector)
    if not account_ids:
        session.commit()
        return 0
    _lock_accounts(session, account_ids)
    # Lock every target row before changing any, in account-id order.
    locked = list(
        session.exec(
            select(Account.account_id)
            .where(col(Account.account_id).in_(account_ids), col(Account.active))
            .order_by(col(Account.account_id))
            .with_for_update()
        )
    )
    if not locked:
        session.commit()
        return 0
    values: dict[str, object] = {"updated_at": now}
    if body.scan_enabled is not None:
        values["scan_enabled"] = body.scan_enabled
    if body.scan_region_mode is not None:
        values["scan_region_mode"] = body.scan_region_mode
    if len(values) > 1:
        session.connection().execute(
            update(Account.__table__)  # ty: ignore[unresolved-attribute]
            .where(col(Account.account_id).in_(locked))
            .values(**values)
        )
    if body.selected_regions is not None:
        _replace_selected_regions(session, locked, body.selected_regions, now)
    session.commit()
    return len(locked)


def import_accounts(
    session: Session,
    accounts: Sequence[RegistryAccount],
    now: datetime | None = None,
) -> ImportResult:
    """Insert accounts that do not exist locally; never update or deactivate existing rows."""
    if not accounts:
        raise EmptyRegistry
    now = now or utcnow()
    account_ids = [account.account_id for account in accounts]
    _lock_accounts(session, account_ids)
    existing = set(
        session.exec(select(Account.account_id).where(col(Account.account_id).in_(account_ids)))
    )
    fresh = [account for account in accounts if account.account_id not in existing]
    if fresh:
        _insert_rows(
            session,
            Account.__table__,  # ty: ignore[unresolved-attribute]
            [
                {
                    "account_id": account.account_id,
                    "account_name": account.account_name,
                    "account_type": account.account_type,
                    "eim_id": account.eim_id,
                    "eim_app": account.eim_app,
                    "ba_id": account.ba_id,
                    "active": True,
                    "inactive_since": None,
                    "registry_fetched_at": now,
                    "source": "imported",
                    "scan_enabled": True,
                    "scan_region_mode": "all",
                    "updated_at": now,
                }
                for account in fresh
            ],
        )
    session.commit()
    return ImportResult(
        fetched=len(accounts), imported=len(fresh), skipped=len(accounts) - len(fresh)
    )


def load_scan_policy(session: Session, account_id: str) -> ScanPolicy:
    account = session.get(Account, account_id)
    if account is None:
        return ScanPolicy(False, False, "all", frozenset())
    selected = frozenset(
        session.exec(
            select(AccountScanRegion.region).where(AccountScanRegion.account_id == account_id)
        )
    )
    return ScanPolicy(
        account.active,
        account.scan_enabled,
        account.scan_region_mode,  # ty: ignore[invalid-argument-type]
        selected,
    )


def effective_region(
    region: str,
    opt_in_status: str,
    reachable: bool,
    mode: ScanRegionMode,
    selected: frozenset[str] | set[str],
) -> bool:
    return reachable and opt_in_status in ENABLED and (mode == "all" or region in selected)


def region_state(
    region: str,
    opt_in_status: str,
    reachable: bool,
    mode: ScanRegionMode,
    selected: frozenset[str] | set[str],
) -> tuple[bool, bool, RegionScanState]:
    """(selected by policy, effective, state) for one region row."""
    chosen = mode == "all" or region in selected
    if not chosen:
        return False, False, "not_selected"
    if not reachable:
        return True, False, "unreachable"
    if opt_in_status == "unknown":
        return True, False, "unknown"
    if opt_in_status not in ENABLED:
        return True, False, "account_disabled"
    return True, True, "selected"


def _region_rows(session: Session, account_ids: Sequence[str]) -> list[AccountRegion]:
    if not account_ids:
        return []
    return list(
        session.exec(
            select(AccountRegion)
            .where(col(AccountRegion.account_id).in_(account_ids))
            .order_by(AccountRegion.account_id, AccountRegion.region)
        )
    )


def _selected_rows(session: Session, account_ids: Sequence[str]) -> list[AccountScanRegion]:
    if not account_ids:
        return []
    return list(
        session.exec(
            select(AccountScanRegion).where(col(AccountScanRegion.account_id).in_(account_ids))
        )
    )


def scan_preview(
    session: Session,
    selector: AccountSelector,
    *,
    resource_type_count: int,
    reachable_regions: set[str],
    override_scan_policy: bool = False,
    scan_enabled: bool | None = None,
    scan_region_mode: ScanRegionMode | None = None,
    selected_regions: Sequence[str] | None = None,
) -> dict[str, int]:
    accounts = list(
        session.exec(
            select(Account).where(*selector_conditions(selector)).order_by(col(Account.account_id))
        )
    )
    account_ids = [account.account_id for account in accounts]
    selected_by_account: dict[str, set[str]] = {}
    for row in _selected_rows(session, account_ids):
        selected_by_account.setdefault(row.account_id, set()).add(row.region)
    regions_by_account: dict[str, list[AccountRegion]] = {}
    for row in _region_rows(session, account_ids):
        regions_by_account.setdefault(row.account_id, []).append(row)

    region_count = 0
    without_region_data = 0
    skipped_disabled = 0
    proposed_selected = set(selected_regions) if selected_regions is not None else None
    for account in accounts:
        if override_scan_policy:
            enabled = True
        elif scan_enabled is not None:
            enabled = scan_enabled
        else:
            enabled = account.scan_enabled
        if not enabled:
            skipped_disabled += 1
            continue
        observed = regions_by_account.get(account.account_id, [])
        if not observed:
            without_region_data += 1
        selected = frozenset(
            proposed_selected
            if proposed_selected is not None
            else selected_by_account.get(account.account_id, set())
        )
        if override_scan_policy:
            mode: ScanRegionMode = "all"
        elif scan_region_mode is not None:
            mode = scan_region_mode
        else:
            mode = "selected" if account.scan_region_mode == "selected" else "all"
        region_count += sum(
            1
            for region in observed
            if effective_region(
                region.region,
                region.opt_in_status,
                region.reachable and region.region in reachable_regions,
                mode,
                selected,
            )
        )
    account_count = len(accounts) - skipped_disabled
    return {
        "accounts": account_count,
        "regions": region_count,
        "estimated_tasks": account_count + region_count * resource_type_count,
        "accounts_without_region_data": without_region_data,
        "skipped_disabled_accounts": skipped_disabled,
    }


def selected_regions(session: SASession, account_id: str) -> set[str]:
    return set(
        session.execute(
            select(AccountScanRegion.region).where(col(AccountScanRegion.account_id) == account_id)
        ).scalars()
    )
