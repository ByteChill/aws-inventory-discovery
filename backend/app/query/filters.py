"""The resource filter shared by list, facets and export (DESIGN 8.2), as typed SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy import ColumnElement, and_, or_

from app.core.errors import ApiError
from app.query.columns import COMMON_BY_KEY, attr_expr
from app.query.tables import A, R
from app.types.columns import ColumnPath, column_paths
from app.types.registry import TypeRegistry
from app.types.spec import ResourceTypeSpec

DEFAULT_STATUSES = ("active", "missing")
DEFAULT_SORT = "-lastChangedAt"

# Filter parameters that apply to one Resource/Account column, by their name.
EQUALS_FIELDS: dict[str, tuple[str, ColumnElement[Any]]] = {
    "accountId": ("account_id", R.account_id),
    "eimId": ("eim_id", A.eim_id),
    "baId": ("ba_id", A.ba_id),
    "accountType": ("account_type", A.account_type),
    "awsRegion": ("aws_region", R.aws_region),
    "resourceType": ("resource_type", R.resource_type),
}
TIME_FIELDS: dict[str, ColumnElement[Any]] = {
    "configuration_item_capture_time": R.config_capture_time,
    "first_seen_at": R.first_seen_at,
    "last_changed_at": R.last_changed_at,
    "missing_since": R.missing_since,
    "deleted_at": R.deleted_at,
}


def invalid_filter(message: str, **details: Any) -> ApiError:
    return ApiError(400, "invalid_filter", message, details or None)


class ResourceFilter(BaseModel):
    """Received as `Annotated[ResourceFilter, Query()]`; `attr.<key>[.gte|.lte]` come as extras."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    account_id: list[str] = []
    eim_id: list[str] = []
    ba_id: list[str] = []
    account_type: list[str] = []
    aws_region: list[str] = []
    resource_type: list[str] = []
    status: list[Literal["active", "missing", "deleted"]] = []
    q: str | None = None
    tag: list[str] = []
    tag_missing: list[str] = []
    configuration_item_capture_time_gte: datetime | None = Field(
        default=None, alias="configurationItemCaptureTime.gte"
    )
    configuration_item_capture_time_lte: datetime | None = Field(
        default=None, alias="configurationItemCaptureTime.lte"
    )
    first_seen_at_gte: datetime | None = Field(default=None, alias="firstSeenAt.gte")
    first_seen_at_lte: datetime | None = Field(default=None, alias="firstSeenAt.lte")
    last_changed_at_gte: datetime | None = Field(default=None, alias="lastChangedAt.gte")
    last_changed_at_lte: datetime | None = Field(default=None, alias="lastChangedAt.lte")
    missing_since_gte: datetime | None = Field(default=None, alias="missingSince.gte")
    missing_since_lte: datetime | None = Field(default=None, alias="missingSince.lte")
    deleted_at_gte: datetime | None = Field(default=None, alias="deletedAt.gte")
    deleted_at_lte: datetime | None = Field(default=None, alias="deletedAt.lte")


class Paging(ResourceFilter):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort: str | None = None

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


# --------------------------------------------------------------------------- type columns


@dataclass(frozen=True)
class AttrFilter:
    key: str
    op: Literal["eq", "gte", "lte"]
    values: list[str]


def single_spec(f: ResourceFilter, registry: TypeRegistry) -> ResourceTypeSpec | None:
    """The spec of the one resource type the filter names, if it names exactly one."""
    names = set(f.resource_type)
    if len(names) == 1:
        (name,) = names
        if name in registry:
            return registry.get(name)
    return None


def attr_filters(f: ResourceFilter) -> list[AttrFilter]:
    filters = []
    for name, raw in (f.model_extra or {}).items():
        if not name.startswith("attr."):
            raise invalid_filter(f"不支持的筛选参数 {name}", parameter=name)
        key, op = name.removeprefix("attr."), "eq"
        for suffix in ("gte", "lte"):
            if key.endswith("." + suffix):
                key, op = key.removesuffix("." + suffix), suffix
        values = raw if isinstance(raw, list) else [raw]
        filters.append(AttrFilter(key, op, [str(value) for value in values]))
    return filters


def _parse_values(column: ColumnPath, attr: AttrFilter, name: str) -> list[Any]:
    try:
        if column.type == "number":
            return [float(value) for value in attr.values]
        if column.type == "bool":
            return [{"true": True, "false": False}[value.lower()] for value in attr.values]
        if column.type == "datetime":
            return [TypeAdapter(datetime).validate_python(value) for value in attr.values]
    except ValueError, KeyError, ValidationError:
        raise invalid_filter(f"{name} 的取值与列类型 {column.type} 不符", parameter=name) from None
    return list(attr.values)


def attr_condition(
    attr: AttrFilter, paths: dict[str, ColumnPath], enabled: dict[str, bool]
) -> ColumnElement[bool]:
    name = f"attr.{attr.key}" + ("" if attr.op == "eq" else f".{attr.op}")
    column = paths.get(attr.key)
    if column is None or not enabled[attr.key]:
        raise invalid_filter(f"当前类型没有可筛选的列 {attr.key}", parameter=name)
    if column.type == "datetime" or attr.op != "eq":
        allowed = attr.op != "eq" and column.type in ("number", "datetime")
    else:
        allowed = True
    if not allowed:
        raise invalid_filter(f"{name} 不适用于类型 {column.type} 的列", parameter=name)
    values = _parse_values(column, attr, name)
    expr = attr_expr(column)
    if attr.op == "gte":
        return or_(*(expr >= value for value in values))
    if attr.op == "lte":
        return or_(*(expr <= value for value in values))
    return expr.in_(values)


# --------------------------------------------------------------------------- conditions


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _tag_conditions(f: ResourceFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if f.tag:
        options = []
        for spec in f.tag:
            key, has_value, value = spec.partition(":")
            wanted = {"key": key, "value": value} if has_value else {"key": key}
            options.append(R.tags.contains([wanted]))
        conditions.append(or_(*options))
    conditions.extend(~R.tags.contains([{"key": key}]) for key in f.tag_missing)
    return conditions


def conditions(
    f: ResourceFilter, registry: TypeRegistry, *, skip: str | None = None
) -> list[ColumnElement[bool]]:
    """WHERE conditions of the filter. `skip` names a facet field whose own filter is left out."""
    result: list[ColumnElement[bool]] = []
    for name, (attribute, column) in EQUALS_FIELDS.items():
        values = getattr(f, attribute)
        if values and skip != name:
            result.append(column.in_(values))
    if skip != "status":
        result.append(R.status.in_(f.status or DEFAULT_STATUSES))
    if f.q:
        pattern = _like(f.q)
        result.append(
            or_(
                R.resource_name.ilike(pattern),
                R.arn.ilike(pattern),
                R.resource_id.ilike(pattern),
            )
        )
    result.extend(_tag_conditions(f))
    for prefix, column in TIME_FIELDS.items():
        if (lower := getattr(f, f"{prefix}_gte")) is not None:
            result.append(column >= lower)
        if (upper := getattr(f, f"{prefix}_lte")) is not None:
            result.append(column <= upper)
    attrs = attr_filters(f)
    if attrs:
        spec = single_spec(f, registry)
        if spec is None:
            raise invalid_filter("attr.* 筛选必须同时指定且仅指定一个资源类型")
        if skip != "resourceType":
            paths = column_paths(spec)
            enabled = {column.key: column.filterable for column in spec.columns}
            result.extend(
                attr_condition(attr, paths, enabled) for attr in attrs if skip != f"attr.{attr.key}"
            )
    return result


def where(f: ResourceFilter, registry: TypeRegistry, *, skip: str | None = None) -> Any:
    return and_(*conditions(f, registry, skip=skip))


# --------------------------------------------------------------------------- sorting


def order_by(
    sort: str | None, f: ResourceFilter, registry: TypeRegistry
) -> list[ColumnElement[Any]]:
    """ORDER BY of a `sort` value; only sortable common columns and the single type's columns."""
    spec = single_spec(f, registry)
    type_columns = {column.key: column for column in spec.columns} if spec else {}
    paths = column_paths(spec) if spec else {}
    clauses: list[ColumnElement[Any]] = []
    for token in (sort or DEFAULT_SORT).split(","):
        token = token.strip()
        descending = token.startswith("-")
        key = token.removeprefix("-")
        common = COMMON_BY_KEY.get(key)
        expr: ColumnElement[Any] | None = None
        if common is not None:
            expr = common.expr
        elif key.removeprefix("attr.") in type_columns:
            name = key.removeprefix("attr.")
            expr = attr_expr(paths[name]) if type_columns[name].sortable else None
        if expr is None:
            raise ApiError(400, "invalid_sort", f"不能按 {key or token} 排序", {"sort": token})
        clauses.append(expr.desc().nulls_last() if descending else expr.asc().nulls_last())
    clauses.append(R.id.asc())  # a total order keeps pages stable
    return clauses
