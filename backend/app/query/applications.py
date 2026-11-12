"""Reading applications: active accounts grouped by EIM (DESIGN 8.3 `GET /applications`)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.query.paging import PagingParams
from app.query.search import like_pattern
from app.query.tables import ACCOUNT, RESOURCE, A, R
from app.schemas.applications import Application
from app.schemas.base import Page

ENVIRONMENTS = ("dev", "preprod", "prod")
ACCOUNT_COUNT = func.count(func.distinct(A.account_id))
RESOURCE_COUNT = func.count(func.distinct(R.id))
GROUPED_ENVIRONMENTS = func.array_agg(func.distinct(A.account_type))
SORTABLE: dict[str, ColumnElement[Any]] = {
    "eimApp": A.eim_app,
    "accountCount": ACCOUNT_COUNT,
    "resourceCount": RESOURCE_COUNT,
}


class ApplicationFilter(PagingParams):
    q: str | None = None
    account_type: list[str] = []
    sort: str = "eimApp"


def _conditions(params: ApplicationFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        A.active.is_(True),
        A.eim_id.is_not(None),
        A.eim_app.is_not(None),
    ]
    if params.account_type:
        conditions.append(A.account_type.in_(params.account_type))
    if params.q:
        pattern = like_pattern(params.q)
        conditions.append(
            or_(
                A.eim_app.ilike(pattern),
                A.eim_id.ilike(pattern),
                A.ba_id.ilike(pattern),
            )
        )
    return conditions


def _ordering(sort: str) -> list[ColumnElement[Any]]:
    ordering: list[ColumnElement[Any]] = []
    for token in sort.split(","):
        key = token.strip().removeprefix("-")
        expr = SORTABLE.get(key)
        if expr is None:
            raise ApiError(400, "invalid_sort", f"不能按 {key} 排序", {"sort": token})
        ordering.append(
            expr.desc().nulls_last() if token.strip().startswith("-") else expr.asc().nulls_last()
        )
    ordering.append(A.eim_id.asc())  # a total order keeps pages stable
    return ordering


def _application(row: Any) -> Application:
    found = set(row.environments or ())
    return Application(
        eim_id=row.eim_id,
        eim_app=row.eim_app,
        ba_id=row.ba_id,
        account_count=row.account_count,
        environments=[name for name in ENVIRONMENTS if name in found],
        resource_count=row.resource_count,
    )


def list_applications(session: Session, params: ApplicationFilter) -> Page[Application]:
    conditions = _conditions(params)
    base = (
        select(
            A.eim_id,
            A.eim_app,
            A.ba_id,
            ACCOUNT_COUNT.label("account_count"),
            GROUPED_ENVIRONMENTS.label("environments"),
            RESOURCE_COUNT.label("resource_count"),
        )
        .select_from(ACCOUNT)
        # Deleted resources are not counted; accounts without resources still appear.
        .join(
            RESOURCE,
            and_(R.account_id == A.account_id, R.status != "deleted"),
            isouter=True,
        )
        .where(*conditions)
        .group_by(A.eim_id, A.eim_app, A.ba_id)
    )
    rows = session.execute(
        base.order_by(*_ordering(params.sort))
        .limit(params.page_size)
        .offset((params.page - 1) * params.page_size)
    ).all()
    total = session.execute(
        select(func.count(func.distinct(A.eim_id))).select_from(ACCOUNT).where(*conditions)
    ).scalar_one()
    return Page(
        items=[_application(row) for row in rows],
        page=params.page,
        page_size=params.page_size,
        total=total,
    )
