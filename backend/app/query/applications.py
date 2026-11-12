"""Reading applications: active accounts grouped by EIM (DESIGN 8.3 `GET /applications`)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, FromClause, Subquery, any_, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.query.paging import PagingParams
from app.query.search import like_pattern
from app.query.tables import A, R
from app.schemas.applications import Application
from app.schemas.base import Page

ENVIRONMENTS = ("dev", "preprod", "prod")


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


def _applications(params: ApplicationFilter) -> Subquery:
    """One row per application with its accounts counted and listed; no resources yet."""
    return (
        select(
            A.eim_id,
            A.eim_app,
            A.ba_id,
            func.count().label("account_count"),
            func.array_agg(func.distinct(A.account_type)).label("environments"),
            func.array_agg(A.account_id).label("account_ids"),
        )
        .where(*_conditions(params))
        .group_by(A.eim_id, A.eim_app, A.ba_id)
        .subquery("application")
    )


def _resource_count(applications: FromClause) -> ColumnElement[int]:
    """The resources of an application's accounts; deleted resources are not counted."""
    return (
        select(func.count())
        .where(R.account_id == any_(applications.c.account_ids), R.status != "deleted")
        .correlate(applications)
        .scalar_subquery()
    )


def _ordering(
    sort: str, applications: FromClause, resource_count: ColumnElement[int]
) -> list[ColumnElement[Any]]:
    sortable: dict[str, ColumnElement[Any]] = {
        "eimApp": applications.c.eim_app,
        "accountCount": applications.c.account_count,
        "resourceCount": resource_count,
    }
    ordering: list[ColumnElement[Any]] = []
    for token in sort.split(","):
        key = token.strip().removeprefix("-")
        expr = sortable.get(key)
        if expr is None:
            raise ApiError(400, "invalid_sort", f"不能按 {key} 排序", {"sort": token})
        ordering.append(
            expr.desc().nulls_last() if token.strip().startswith("-") else expr.asc().nulls_last()
        )
    # The grouping key as the tie-break: a total order keeps pages stable.
    ordering.extend(
        (applications.c.eim_id.asc(), applications.c.eim_app.asc(), applications.c.ba_id.asc())
    )
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
    applications = _applications(params)
    # Page first, then count resources for that page only. Counting them for every
    # application is needed only to sort by the count.
    page = (
        select(applications)
        .order_by(*_ordering(params.sort, applications, _resource_count(applications)))
        .limit(params.page_size)
        .offset(params.offset)
        .subquery("page")
    )
    resource_count = _resource_count(page).label("resource_count")
    rows = session.execute(
        select(page, resource_count).order_by(*_ordering(params.sort, page, resource_count))
    ).all()
    total = session.execute(select(func.count()).select_from(applications)).scalar_one()
    return Page(
        items=[_application(row) for row in rows],
        page=params.page,
        page_size=params.page_size,
        total=total,
    )
