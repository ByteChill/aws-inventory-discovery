"""Dashboard overview numbers (api.md `GET /stats/overview`)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import Cache
from app.query.columns import Language, label_of, value_label
from app.query.tables import ACCOUNT, RESOURCE, A, R
from app.schemas.resources import AccountTypeCount, StatsOverview, TypeCount
from app.types.registry import TypeRegistry

UNREACHABLE = ("access_denied", "credential_error")


def compute(session: Session, registry: TypeRegistry, language: Language) -> StatsOverview:
    live = R.status != "deleted"
    by_type = session.execute(
        select(R.resource_type, func.count().label("total"))
        .where(live)
        .group_by(R.resource_type)
        .order_by(func.count().desc(), R.resource_type)
    ).all()
    by_account_type = session.execute(
        select(A.account_type, func.count().label("total"))
        .select_from(RESOURCE.join(ACCOUNT, R.account_id == A.account_id))
        .where(live)
        .group_by(A.account_type)
        .order_by(func.count().desc(), A.account_type)
    ).all()
    accounts = session.execute(
        select(
            func.count().label("total"),
            func.count().filter(A.access_status.in_(UNREACHABLE)).label("unreachable"),
        ).where(A.active)
    ).one()
    return StatsOverview(
        resource_total=sum(row.total for row in by_type),
        account_total=accounts.total,
        account_unreachable=accounts.unreachable,
        by_resource_type=[
            TypeCount(
                resource_type=row.resource_type,
                label=(
                    label_of(registry.get(row.resource_type).label, language)
                    if row.resource_type in registry
                    else row.resource_type
                ),
                count=row.total,
            )
            for row in by_type
        ],
        by_account_type=[
            AccountTypeCount(
                account_type=row.account_type,
                label=value_label("accountType", row.account_type, language),
                count=row.total,
            )
            for row in by_account_type
        ],
        generated_at=datetime.now(UTC),
    )


def overview(
    session: Session, registry: TypeRegistry, cache: Cache, language: Language
) -> StatsOverview:
    key = cache.key("stats", "overview", language)
    cached = cache.get(key)
    if cached is not None:
        return StatsOverview.model_validate(cached)
    result = compute(session, registry, language)
    cache.set(key, result.model_dump(mode="json", by_alias=True))
    return result
