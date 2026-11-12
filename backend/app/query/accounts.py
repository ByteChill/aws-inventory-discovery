"""Reading accounts and their scan coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Row, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.query.columns import STALE_AFTER
from app.query.paging import PagingParams
from app.query.search import like_pattern
from app.query.tables import ACCOUNT, ACCOUNT_REGION, A, R, S
from app.schemas.accounts import AccountDetail, AccountRegionInfo, AccountSummary, CoverageCell
from app.schemas.base import Page

SORTABLE: dict[str, ColumnElement[Any]] = {
    "accountId": A.account_id,
    "accountName": A.account_name,
    "accountType": A.account_type,
    "eimId": A.eim_id,
    "baId": A.ba_id,
    "accessStatus": A.access_status,
    "lastScanOkAt": A.last_scan_ok_at,
    "active": A.active,
}
RESOURCE_COUNT = (
    select(func.count())
    .where(R.account_id == A.account_id, R.status != "deleted")
    .correlate(ACCOUNT)
    .scalar_subquery()
)
ACCESS_DENIED_MARKS = ("accessdenied", "access_denied", "unauthorized")


class AccountFilter(PagingParams):
    account_type: list[str] = []
    eim_id: list[str] = []
    ba_id: list[str] = []
    access_status: list[str] = []
    q: str | None = None
    active: bool = True
    sort: str = "accountName"


def _summary(row: Row[Any], purge_days: int) -> dict[str, Any]:
    m = row._mapping
    purge_at = (
        m["inactive_since"] + timedelta(days=purge_days)
        if not m["active"] and m["inactive_since"] is not None
        else None
    )
    return {
        "account_id": m["account_id"],
        "account_name": m["account_name"],
        "account_type": m["account_type"],
        "eim_id": m["eim_id"],
        "eim_app": m["eim_app"],
        "ba_id": m["ba_id"],
        "active": m["active"],
        "access_status": m["access_status"],
        "access_error": m["access_error"],
        "resource_count": m["resource_count"],
        "last_scan_ok_at": m["last_scan_ok_at"],
        "purge_at": purge_at,
    }


def _account_select() -> Any:
    return select(
        A.account_id,
        A.account_name,
        A.account_type,
        A.eim_id,
        A.eim_app,
        A.ba_id,
        A.active,
        A.inactive_since,
        A.access_status,
        A.access_error,
        A.last_scan_ok_at,
        RESOURCE_COUNT.label("resource_count"),
    )


def list_accounts(session: Session, params: AccountFilter, purge_days: int) -> Page[AccountSummary]:
    conditions: list[ColumnElement[bool]] = [A.active == params.active]
    for values, column in (
        (params.account_type, A.account_type),
        (params.eim_id, A.eim_id),
        (params.ba_id, A.ba_id),
        (params.access_status, A.access_status),
    ):
        if values:
            conditions.append(column.in_(values))
    if params.q:
        pattern = like_pattern(params.q)
        conditions.append(or_(A.account_name.ilike(pattern), A.account_id.ilike(pattern)))
    ordering: list[ColumnElement[Any]] = []
    for token in params.sort.split(","):
        key = token.strip().removeprefix("-")
        expr = RESOURCE_COUNT if key == "resourceCount" else SORTABLE.get(key)
        if expr is None:
            raise ApiError(400, "invalid_sort", f"不能按 {key} 排序", {"sort": token})
        ordering.append(
            expr.desc().nulls_last() if token.strip().startswith("-") else expr.asc().nulls_last()
        )
    ordering.append(A.account_id.asc())
    rows = session.execute(
        _account_select()
        .where(*conditions)
        .order_by(*ordering)
        .limit(params.page_size)
        .offset((params.page - 1) * params.page_size)
    ).all()
    total = session.execute(
        select(func.count()).select_from(ACCOUNT).where(*conditions)
    ).scalar_one()
    return Page(
        items=[AccountSummary(**_summary(row, purge_days)) for row in rows],
        page=params.page,
        page_size=params.page_size,
        total=total,
    )


def _require(session: Session, account_id: str) -> Row[Any]:
    row = session.execute(_account_select().where(A.account_id == account_id)).first()
    if row is None:
        raise ApiError(404, "account_not_found", "账号不存在", {"accountId": account_id})
    return row


def get_account(session: Session, account_id: str, purge_days: int) -> AccountDetail:
    row = _require(session, account_id)
    regions = session.execute(
        select(ACCOUNT_REGION)
        .where(ACCOUNT_REGION.c.account_id == account_id)
        .order_by(ACCOUNT_REGION.c.region)
    ).all()
    return AccountDetail(
        **_summary(row, purge_days),
        regions=[
            AccountRegionInfo(
                region=region.region,
                opt_in_status=region.opt_in_status,
                reachable=region.reachable,
                config_recording=(region.config_recorder or {}).get("recording"),
            )
            for region in regions
        ],
    )


def _state(row: Row[Any], now: datetime) -> str:
    if row.consecutive_failures > 0:
        code = (row.last_error_code or "").lower()
        return "access_denied" if any(mark in code for mark in ACCESS_DENIED_MARKS) else "failed"
    if row.last_succeeded_at is None:
        return "never"
    return "stale" if row.last_succeeded_at < now - STALE_AFTER else "ok"


def coverage(session: Session, account_id: str) -> list[CoverageCell]:
    _require(session, account_id)
    now = datetime.now(UTC)
    rows = session.execute(
        select(
            S.region,
            S.resource_type,
            S.last_succeeded_at,
            S.last_error_code,
            S.consecutive_failures,
        )
        .where(S.account_id == account_id)
        .order_by(S.region, S.resource_type)
    )
    return [
        CoverageCell(
            region=row.region,
            resource_type=row.resource_type,
            state=_state(row, now),  # ty: ignore[invalid-argument-type]
            last_succeeded_at=row.last_succeeded_at,
            last_error_code=row.last_error_code,
        )
        for row in rows
    ]
