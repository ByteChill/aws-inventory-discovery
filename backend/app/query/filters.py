"""The resource filter shared by list, facets and export (DESIGN 8.2), as typed SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy import ColumnElement, and_, func, or_, select

from app.core.errors import ApiError
from app.core.regions import GLOBAL_HOME, GLOBAL_REGION
from app.query.columns import COMMON_BY_KEY, attr_expr
from app.query.search import like_pattern
from app.query.tables import RESOURCE, SCAN_REQUEST, SCAN_TASK, A, R
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
    scan_request_id: int | None = None
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


def _scope_list_allows(key: str, value: ColumnElement[Any]) -> ColumnElement[bool]:
    """The request's `scope[key]` list names the value, or is empty or absent (no restriction)."""
    listed = SCAN_REQUEST.c.scope[key]
    return or_(func.coalesce(func.jsonb_array_length(listed), 0) == 0, listed.op("?")(value))


def _covered_by_tasks(registry: TypeRegistry) -> ColumnElement[bool]:
    """A task of the request covers the resource.

    A `shard` task covers its (account, region, type) and `confirm_missing` the same scope.
    A `config_group` task reads Config for its account and region: it covers the types Config
    can serve (never Cloud Control-only or native types such as AMI, which always get their
    own shard), narrowed by the request's `resourceTypes`. A `resource` task names one
    resource. Global resources are written under the `global` region but read in us-east-1.
    A `sweep_account` task is planning only (it covers nothing by itself).
    """
    config_types = [spec.type_name for spec in registry if spec.sources.config]
    task_region = or_(
        SCAN_TASK.c.region == R.aws_region,
        and_(R.aws_region == GLOBAL_REGION, SCAN_TASK.c.region == GLOBAL_HOME),
    )
    return and_(
        SCAN_TASK.c.account_id == R.account_id,
        task_region,
        or_(
            and_(
                SCAN_TASK.c.kind.in_(("shard", "confirm_missing")),
                SCAN_TASK.c.resource_type == R.resource_type,
            ),
            and_(
                SCAN_TASK.c.kind == "config_group",
                R.resource_type.in_(config_types),
                _scope_list_allows("resourceTypes", R.resource_type),
            ),
            and_(
                SCAN_TASK.c.kind == "resource",
                SCAN_TASK.c.resource_type == R.resource_type,
                SCAN_TASK.c.resource_id == R.resource_id,
            ),
        ),
    )


def _covered_by_request() -> ColumnElement[bool]:
    """What the request itself says it covers: its `target` (one resource, or one scope when
    it has no resource id) or its `scope`. A refresh stores the target's keys in snake_case,
    confirming a scope stores them in camelCase."""

    def target(camel: str, snake: str) -> ColumnElement[Any]:
        stored = SCAN_REQUEST.c.target
        return func.coalesce(stored[camel].astext, stored[snake].astext)

    target_account = target("accountId", "account_id")
    target_resource_id = target("resourceId", "resource_id")
    by_target = and_(
        target_account == R.account_id,
        target("awsRegion", "aws_region") == R.aws_region,
        target("resourceType", "resource_type") == R.resource_type,
        or_(target_resource_id.is_(None), target_resource_id == R.resource_id),
    )
    regions = SCAN_REQUEST.c.scope["awsRegions"]
    by_scope = and_(
        target_account.is_(None),  # an unset target is stored as JSON null, not SQL NULL
        or_(
            SCAN_REQUEST.c.scope["all"].astext == "true",
            SCAN_REQUEST.c.scope["accountIds"].op("?")(R.account_id),
        ),
        or_(
            _scope_list_allows("awsRegions", R.aws_region),
            and_(R.aws_region == GLOBAL_REGION, regions.op("?")(GLOBAL_HOME)),
        ),
        _scope_list_allows("resourceTypes", R.resource_type),
    )
    return or_(by_target, by_scope)


def _scan_request_condition(
    scan_request_id: int, registry: TypeRegistry, task_retention: timedelta
) -> ColumnElement[bool]:
    """Resources inside the scope of a scan (DESIGN 8.2).

    While the request's tasks are all still there, the scope is the resolved one: what the
    tasks actually cover, not the scope written on the request. Tasks are purged
    `task_retention` after they finish and none finishes before its request is created, so
    a request older than that may have lost some: from then on the scope is the one the
    request recorded, rather than an ever smaller list that ends up empty.
    """
    tasks_kept = SCAN_REQUEST.c.created_at >= func.now() - task_retention
    by_tasks = (
        select(SCAN_TASK.c.id)
        .select_from(SCAN_TASK.join(SCAN_REQUEST, SCAN_TASK.c.scan_request_id == SCAN_REQUEST.c.id))
        .where(SCAN_REQUEST.c.id == scan_request_id, tasks_kept, _covered_by_tasks(registry))
        .correlate(RESOURCE)
        .exists()
    )
    by_request = (
        select(SCAN_REQUEST.c.id)
        .where(SCAN_REQUEST.c.id == scan_request_id, ~tasks_kept, _covered_by_request())
        .correlate(RESOURCE)
        .exists()
    )
    return or_(by_tasks, by_request)


def conditions(
    f: ResourceFilter,
    registry: TypeRegistry,
    *,
    task_retention: timedelta,
    skip: str | None = None,
) -> list[ColumnElement[bool]]:
    """WHERE conditions of the filter. `task_retention` is how long finished scan tasks are
    kept (it decides how `scanRequestId` is resolved); `skip` names a facet field whose own
    filter is left out."""
    result: list[ColumnElement[bool]] = []
    for name, (attribute, column) in EQUALS_FIELDS.items():
        values = getattr(f, attribute)
        if values and skip != name:
            result.append(column.in_(values))
    if skip != "status":
        result.append(R.status.in_(f.status or DEFAULT_STATUSES))
    if f.scan_request_id is not None:
        result.append(_scan_request_condition(f.scan_request_id, registry, task_retention))
    if f.q:
        pattern = like_pattern(f.q)
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


def where(
    f: ResourceFilter,
    registry: TypeRegistry,
    *,
    task_retention: timedelta,
    skip: str | None = None,
) -> Any:
    return and_(*conditions(f, registry, task_retention=task_retention, skip=skip))


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
