"""Reading resources: list, detail, lookup and relationships."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    distinct,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Join

from app.core.cache import Cache
from app.core.errors import ApiError
from app.query.cache_keys import params_digest
from app.query.columns import STALE_AFTER, attr_expr, attr_value
from app.query.filters import Paging, ResourceFilter, order_by, single_spec, where
from app.query.tables import ACCOUNT, RELATION, RESOURCE, SHARD, A, R, S
from app.schemas.accounts import AccountRef
from app.schemas.base import Page
from app.schemas.resources import (
    LiveData,
    RelationshipEntry,
    RelationshipRef,
    ResourceDetail,
    ResourceItem,
    ResourceSummary,
    Tag,
)
from app.types.columns import column_paths, column_values
from app.types.registry import TypeRegistry

STALE = and_(
    R.status != "deleted",
    or_(S.last_succeeded_at.is_(None), S.last_succeeded_at < func.now() - STALE_AFTER),
)

ITEM_COLUMNS: list[ColumnElement[Any]] = [
    R.id,
    R.arn,
    R.account_id,
    R.aws_region,
    R.resource_type,
    R.resource_id,
    R.resource_name,
    R.tags,
    R.status,
    R.config_capture_time,
    R.first_seen_at,
    R.last_changed_at,
    R.missing_since,
    R.deleted_at,
    A.account_name,
    A.account_type,
    A.eim_id,
    A.eim_app,
    A.ba_id,
    STALE.label("stale"),
    func.jsonb_array_length(R.relationships).label("relationship_count"),
]
DETAIL_COLUMNS: list[ColumnElement[Any]] = [
    R.configuration,
    R.relationships,
    R.observed_at,
    R.observed_source,
    R.live_observed_at,
]


def resource_join() -> Join:
    """resource -> account (always there) -> the scope's shard (for `stale`)."""
    return RESOURCE.join(ACCOUNT, R.account_id == A.account_id).outerjoin(
        SHARD,
        and_(
            S.account_id == R.account_id,
            S.region == R.aws_region,
            S.resource_type == R.resource_type,
        ),
    )


def item_select(
    extra: Sequence[ColumnElement[Any]] | None = None,
) -> Select[Any]:
    return select(*ITEM_COLUMNS, *(extra or [])).select_from(resource_join())


# --------------------------------------------------------------------------- rows -> models


def _item_fields(row: Mapping[str, Any], attrs: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "arn": row["arn"],
        "account_id": row["account_id"],
        "aws_region": row["aws_region"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "resource_name": row["resource_name"],
        "tags": [Tag.model_validate(tag) for tag in row["tags"]],
        "account": AccountRef(
            account_id=row["account_id"],
            account_name=row["account_name"],
            account_type=row["account_type"],
            eim_id=row["eim_id"],
            eim_app=row["eim_app"],
            ba_id=row["ba_id"],
        ),
        "attrs": attrs,
        "relationship_count": row["relationship_count"],
        "status": row["status"],
        "stale": bool(row["stale"]),
        "configuration_item_capture_time": row["config_capture_time"],
        "first_seen_at": row["first_seen_at"],
        "last_changed_at": row["last_changed_at"],
        "missing_since": row["missing_since"],
        "deleted_at": row["deleted_at"],
    }


def to_item(row: Row[Any], attr_keys: list[str]) -> ResourceItem:
    """A list row; `attr_keys` are the labels of its type-column expressions (`attr_<i>`)."""
    mapping = row._mapping
    attrs = {key: attr_value(mapping[f"attr_{index}"]) for index, key in enumerate(attr_keys)}
    return ResourceItem(**_item_fields(mapping, attrs))


def relationship_refs(relationships: list[Any]) -> list[RelationshipRef]:
    return [
        RelationshipRef(
            resource_type=item["resourceType"],
            resource_id=item["resourceId"],
            name=item.get("relationshipName", ""),
        )
        for item in relationships
    ]


def to_detail(row: Row[Any], registry: TypeRegistry, *, live: bool) -> ResourceDetail:
    """A full record. `live` rows also carry `live_raw` (selected as `live_raw`)."""
    mapping = row._mapping
    configuration = mapping["configuration"]
    resource_type = mapping["resource_type"]
    attrs = (
        column_values(registry.get(resource_type), configuration or {})
        if resource_type in registry
        else {}
    )
    live_raw = mapping["live_raw"] if live else None
    return ResourceDetail(
        **_item_fields(mapping, attrs),
        configuration=configuration,
        relationships=relationship_refs(mapping["relationships"]),
        live=(
            LiveData(properties=live_raw, observed_at=mapping["live_observed_at"])
            if live_raw is not None
            else None
        ),
        observed_at=mapping["observed_at"],
        observed_source=mapping["observed_source"],
    )


def detail_select(*, live: bool) -> Select[Any]:
    extra = [*DETAIL_COLUMNS, *([R.live_raw] if live else [])]
    return item_select(extra)


def detail_rows(
    session: Session, registry: TypeRegistry, condition: Any, *, live: bool = True
) -> list[ResourceDetail]:
    statement = detail_select(live=live).where(condition)
    return [to_detail(row, registry, live=live) for row in session.execute(statement)]


# --------------------------------------------------------------------------- list


def list_resources(
    session: Session,
    registry: TypeRegistry,
    cache: Cache,
    params: Paging,
    *,
    task_retention: timedelta,
) -> Page[ResourceItem]:
    condition = where(params, registry, task_retention=task_retention)
    spec = single_spec(params, registry)
    attr_keys = [column.key for column in spec.columns] if spec else []
    paths = column_paths(spec) if spec else {}
    attr_columns = [
        attr_expr(paths[key], raw_datetime=True).label(f"attr_{index}")
        for index, key in enumerate(attr_keys)
    ]
    statement = (
        item_select(attr_columns)
        .where(condition)
        .order_by(*order_by(params.sort, params, registry))
        .limit(params.page_size)
        .offset(params.offset)
    )
    items = [to_item(row, attr_keys) for row in session.execute(statement)]
    return Page(
        items=items,
        page=params.page,
        page_size=params.page_size,
        total=count(session, params, registry, task_retention=task_retention, cache=cache),
    )


def count(
    session: Session,
    f: ResourceFilter,
    registry: TypeRegistry,
    *,
    task_retention: timedelta,
    cache: Cache,
) -> int:
    """The filter's resource count, shared by the list's `total` and `/resources/summary`.

    Paging and sort do not change the count, so they are left out of the key."""
    key = cache.key("resources", "count", params_digest(f, exclude={"page", "page_size", "sort"}))
    cached = cache.get(key)
    if cached is not None:
        return int(cached)
    statement = (
        select(func.count())
        .select_from(RESOURCE.join(ACCOUNT, R.account_id == A.account_id))
        .where(where(f, registry, task_retention=task_retention))
    )
    total = session.execute(statement).scalar_one()
    cache.set(key, total)
    return total


def summary(
    session: Session,
    f: ResourceFilter,
    registry: TypeRegistry,
    *,
    task_retention: timedelta,
    cache: Cache,
) -> ResourceSummary:
    """Distinct counts under the filter; unlike facets, nothing is capped. The resource count
    comes from the same cache as the list's `total`."""
    resources = count(session, f, registry, task_retention=task_retention, cache=cache)
    statement = (
        select(
            func.count(distinct(R.account_id)).label("accounts"),
            func.count(distinct(A.eim_id)).label("applications"),
            func.count(distinct(R.resource_type)).label("resource_types"),
            func.count(distinct(R.aws_region)).label("regions"),
        )
        .select_from(RESOURCE.join(ACCOUNT, R.account_id == A.account_id))
        .where(where(f, registry, task_retention=task_retention))
    )
    row = session.execute(statement).one()
    return ResourceSummary(
        resources=resources,
        accounts=row.accounts,
        applications=row.applications,
        resource_types=row.resource_types,
        regions=row.regions,
    )


def get_detail(session: Session, registry: TypeRegistry, resource_id: int) -> ResourceDetail:
    found = detail_rows(session, registry, R.id == resource_id)
    if not found:
        raise ApiError(404, "resource_not_found", "资源不存在", {"id": resource_id})
    return found[0]


def lookup(session: Session, registry: TypeRegistry, arn: str) -> list[ResourceDetail]:
    """Every resource with this ARN; a shared AMI's ARN is in several accounts, the owner first."""
    owner_first = case((R.configuration["ownership"].as_string() == "self", 0), else_=1)
    statement = (
        detail_select(live=True).where(R.arn == arn).order_by(owner_first, R.account_id, R.id)
    )
    found = [to_detail(row, registry, live=True) for row in session.execute(statement)]
    if not found:
        raise ApiError(404, "resource_not_found", "资源不存在", {"arn": arn})
    return found


# --------------------------------------------------------------------------- relationships


def relationships(
    session: Session,
    resource_id: int,
    direction: str,
    page: int,
    page_size: int,
) -> Page[RelationshipEntry]:
    if not session.execute(select(R.id).where(R.id == resource_id)).first():
        raise ApiError(404, "resource_not_found", "资源不存在", {"id": resource_id})
    parts = []
    if direction in ("out", "both"):
        parts.append(
            select(
                literal("out").label("direction"),
                RELATION.c.relation_name,
                RELATION.c.to_resource_type.label("other_type"),
                RELATION.c.to_resource_key.label("other_key"),
                RELATION.c.to_resource_id.label("other_id"),
            ).where(RELATION.c.from_resource_id == resource_id)
        )
    if direction in ("in", "both"):
        parts.append(
            select(
                literal("in").label("direction"),
                RELATION.c.relation_name,
                R.resource_type.label("other_type"),
                R.resource_id.label("other_key"),
                RELATION.c.from_resource_id.label("other_id"),
            )
            .select_from(RELATION.join(RESOURCE, R.id == RELATION.c.from_resource_id))
            .where(RELATION.c.to_resource_id == resource_id)
        )
    edges = union_all(*parts).subquery("edges")
    total = session.execute(select(func.count()).select_from(edges)).scalar_one()
    statement = (
        select(edges, *ITEM_COLUMNS)
        .select_from(edges.outerjoin(RESOURCE, R.id == edges.c.other_id))
        .outerjoin(ACCOUNT, R.account_id == A.account_id)
        .outerjoin(
            SHARD,
            and_(
                S.account_id == R.account_id,
                S.region == R.aws_region,
                S.resource_type == R.resource_type,
            ),
        )
        .order_by(
            edges.c.direction.desc(), edges.c.relation_name, edges.c.other_type, edges.c.other_key
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    entries = []
    for row in session.execute(statement):
        mapping = row._mapping
        entries.append(
            RelationshipEntry(
                direction=mapping["direction"],
                relation_name=mapping["relation_name"],
                resource_type=mapping["other_type"],
                resource_key=mapping["other_key"],
                resource=(
                    ResourceItem(**_item_fields(mapping, {}))
                    if mapping["other_id"] is not None
                    else None
                ),
            )
        )
    return Page(items=entries, page=page, page_size=page_size, total=total)
