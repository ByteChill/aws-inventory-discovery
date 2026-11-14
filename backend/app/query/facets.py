"""Facet counts for the filter drop-downs (api.md `GET /resources/facets`)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.core.cache import Cache, validated
from app.query.cache_keys import params_digest
from app.query.columns import Language, attr_expr, label_of, value_label
from app.query.filters import ResourceFilter, invalid_filter, single_spec, where
from app.query.tables import ACCOUNT, RESOURCE, A, R
from app.schemas.resources import FacetBucket
from app.types.columns import column_paths
from app.types.registry import TypeRegistry

MAX_BUCKETS = 200

# facet field -> (value column, column the label comes from or None)
COMMON_FIELDS: dict[str, tuple[ColumnElement[Any], ColumnElement[Any] | None]] = {
    "accountId": (R.account_id, A.account_name),
    "eimId": (A.eim_id, A.eim_app),
    "baId": (A.ba_id, None),
    "accountType": (A.account_type, None),
    "awsRegion": (R.aws_region, None),
    "resourceType": (R.resource_type, None),
    "status": (R.status, None),
}
_BUCKETS = TypeAdapter(list[FacetBucket])


class FacetParams(ResourceFilter):
    field: list[str] = []


def cache_key(cache: Cache, digest: str, language: Language, field: str) -> str:
    """One entry per (filter, field, language): a request for a different set of fields reuses
    the entries it shares with earlier requests instead of computing them again."""
    return cache.key("facets", digest, field, language)


def _bucket_label(
    field: str, value: str, name: str | None, registry: TypeRegistry, language: Language
) -> str:
    if field in ("status", "accountType"):
        return value_label(field, value, language)
    if field == "awsRegion" and value == "global":
        return "全局" if language == "zh-CN" else "Global"
    if field == "resourceType" and value in registry:
        return label_of(registry.get(value).label, language)
    if field.startswith("attr.") and value in ("true", "false"):
        return value_label("bool", value, language)
    return name or value


def _field_columns(
    field: str, params: ResourceFilter, registry: TypeRegistry
) -> tuple[ColumnElement[Any], ColumnElement[Any] | None]:
    if field in COMMON_FIELDS:
        return COMMON_FIELDS[field]
    key = field.removeprefix("attr.")
    spec = single_spec(params, registry)
    if not field.startswith("attr.") or spec is None:
        raise invalid_filter(f"不能对 {field} 做分类计数", field=field)
    column = next((column for column in spec.columns if column.key == key), None)
    if column is None or column.type not in ("enum", "bool"):
        raise invalid_filter(f"{field} 不是枚举或布尔列", field=field)
    return attr_expr(column_paths(spec)[key]), None


def compute(
    session: Session,
    registry: TypeRegistry,
    params: FacetParams,
    language: Language,
    *,
    task_retention: timedelta,
    fields: list[str],
) -> dict[str, list[FacetBucket]]:
    result: dict[str, list[FacetBucket]] = {}
    for field in fields:
        value, label = _field_columns(field, params, registry)
        total = func.count().label("total")
        statement = (
            select(
                value.label("value"),
                total,
                *([func.max(label).label("name")] if label is not None else []),
            )
            .select_from(RESOURCE.join(ACCOUNT, R.account_id == A.account_id))
            .where(
                where(params, registry, task_retention=task_retention, skip=field),
                value.is_not(None),
            )
            .group_by(value)
            .order_by(total.desc(), value)
            .limit(MAX_BUCKETS)
        )
        result[field] = [
            FacetBucket(
                value=str(row.value).lower() if isinstance(row.value, bool) else str(row.value),
                label=_bucket_label(
                    field,
                    str(row.value).lower() if isinstance(row.value, bool) else str(row.value),
                    row.name if label is not None else None,
                    registry,
                    language,
                ),
                count=row.total,
            )
            for row in session.execute(statement)
        ]
    return result


def facets(
    session: Session,
    registry: TypeRegistry,
    cache: Cache,
    params: FacetParams,
    language: Language,
    *,
    task_retention: timedelta,
) -> dict[str, list[FacetBucket]]:
    fields = list(dict.fromkeys(params.field))
    digest = params_digest(params, exclude={"field"})  # the same for every field
    keys = [cache_key(cache, digest, language, field) for field in fields]
    values: dict[str, list[FacetBucket]] = {}
    missing: list[str] = []
    for field, raw in zip(fields, cache.get_many(keys), strict=True):
        cached = validated(_BUCKETS, raw)
        if cached is None:
            missing.append(field)
        else:
            values[field] = cached
    if missing:
        computed = compute(
            session, registry, params, language, task_retention=task_retention, fields=missing
        )
        cache.set_many(
            {
                cache_key(cache, digest, language, field): _BUCKETS.dump_python(
                    buckets, mode="json", by_alias=True
                )
                for field, buckets in computed.items()
            }
        )
        values.update(computed)
    return {field: values[field] for field in fields}
