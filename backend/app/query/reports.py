"""Custom reports: compile a saved condition tree to SQL, count, list and export (v2).

The report definition is data, not code: every node is validated against the resource type
registry before it reaches SQL, and the query only ever reads the inventory tables.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import (
    ColumnElement,
    Row,
    and_,
    cast,
    false,
    func,
    literal,
    not_,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlmodel import col

from app.core.cache import Cache
from app.core.errors import ApiError
from app.models.report import Report
from app.query.columns import (
    COMMON_BY_KEY,
    COMMON_COLUMNS,
    Language,
    attr_expr,
    attr_value,
    label_of,
)
from app.query.export import BATCH_SIZE, BOM, Export, _cell
from app.query.resources import item_select, to_item
from app.query.search import like_pattern
from app.query.tables import ACCOUNT, FINDING, RELATION, RESOURCE, A, R
from app.reports.definition import (
    FINDING_FIELDS,
    RELATION_COUNT_FIELDS,
    Condition,
    DefinitionError,
    ReportDefinition,
    referenced_fields,
    relation_prefixes,
    validate_definition,
)
from app.schemas.reports import (
    ReportColumnInfo,
    ReportDetail,
    ReportFieldInfo,
    ReportInfo,
    ReportOverview,
    ReportOverviewItem,
    ReportPage,
    ReportRow,
)
from app.types.columns import ColumnPath, column_paths
from app.types.mapping import split_path
from app.types.registry import TypeRegistry

DEFAULT_COLUMNS = (
    "resourceName",
    "resourceType",
    "accountId",
    "accountType",
    "awsRegion",
    "status",
)
DEFAULT_SORT = "-lastChangedAt"
Scope = tuple[Literal["app", "acct"], str]


# --------------------------------------------------------------------------- fields


@dataclass(frozen=True)
class ResolvedField:
    key: str
    type: str
    expr: ColumnElement[Any] | None = None
    jsonpath: str | None = None  # set for a report field that crosses an array
    label_zh: str = ""
    label_en: str = ""
    mono: bool = False

    def label(self, language: Language) -> str:
        return self.label_zh if language == "zh-CN" else self.label_en


def _jsonpath(path: str) -> str:
    """`ContainerDefinitions[].Image` -> `$.ContainerDefinitions[*].Image`."""
    return "$." + ".".join(
        segment.removesuffix("[]") + ("[*]" if segment.endswith("[]") else "")
        for segment in path.split(".")
    )


def _relation_count_sql(direction: Literal["in", "out"]) -> ColumnElement[Any]:
    column = RELATION.c.to_resource_id if direction == "in" else RELATION.c.from_resource_id
    return (
        select(func.count())
        .select_from(RELATION)
        .where(column == R.id)
        .correlate(RESOURCE)
        .scalar_subquery()
    )


FINDING_COLUMNS = {
    "findingsCriticalCount": FINDING.c.critical_count,
    "findingsHighCount": FINDING.c.high_count,
    "findingsMediumCount": FINDING.c.medium_count,
    "findingsLowCount": FINDING.c.low_count,
    "findingsMaxScore": FINDING.c.max_score,
}

FINDING_LABELS: dict[str, tuple[str, str]] = {
    "findingsCriticalCount": ("严重发现", "Critical findings"),
    "findingsHighCount": ("高危发现", "High findings"),
    "findingsMediumCount": ("中危发现", "Medium findings"),
    "findingsLowCount": ("低危发现", "Low findings"),
    "findingsMaxScore": ("最高风险分", "Highest score"),
}


def _findings_expr(key: str, table: Any) -> ColumnElement[Any]:
    """The current summary of this resource; NULL when it has never been checked."""
    return (
        select(FINDING_COLUMNS[key])
        .where(
            FINDING.c.account_id == table.c.account_id,
            FINDING.c.aws_region == table.c.aws_region,
            FINDING.c.resource_type == table.c.resource_type,
            FINDING.c.resource_id == table.c.resource_id,
        )
        .scalar_subquery()
    )


# The common fields of a related resource. Account-wide fields are read through a scalar
# subquery on the account table, the same shape as the findings lookup.
_RELATED_RESOURCE_COMMON = {
    "resourceName": "resource_name",
    "resourceType": "resource_type",
    "resourceId": "resource_id",
    "arn": "arn",
    "accountId": "account_id",
    "awsRegion": "aws_region",
    "status": "status",
    "configurationItemCaptureTime": "config_capture_time",
    "firstSeenAt": "first_seen_at",
    "lastChangedAt": "last_changed_at",
    "missingSince": "missing_since",
    "deletedAt": "deleted_at",
}
_RELATED_ACCOUNT_COMMON = {
    "accountName": "account_name",
    "eimId": "eim_id",
    "eimApp": "eim_app",
    "baId": "ba_id",
    "accountType": "account_type",
}


def resolve_field(key: str, definition: ReportDefinition, registry: TypeRegistry) -> ResolvedField:
    if key in RELATION_COUNT_FIELDS:
        direction = "in" if key.startswith("in") else "out"
        return ResolvedField(
            key,
            "number",
            expr=_relation_count_sql(direction),
            label_zh="被引用" if direction == "in" else "引用",
            label_en="Referenced by" if direction == "in" else "References",
        )
    common = COMMON_BY_KEY.get(key)
    if common is not None:
        if key == "tags":
            return ResolvedField(key, "tags", expr=R.tags, label_zh="标签", label_en="Tags")
        return ResolvedField(
            key,
            common.type,
            expr=common.expr,
            label_zh=common.zh,
            label_en=common.en,
            mono=common.mono,
        )
    if definition.subject_type is None:
        raise ApiError(422, "validation_error", f"所有类型的报表不能使用字段 {key}", {"field": key})
    spec = registry.get(definition.subject_type)
    if "." in key:
        return _related_column(key, spec, registry)
    column = next((item for item in spec.columns if item.key == key), None)
    if column is not None:
        path = column_paths(spec)[key]
        return ResolvedField(
            key,
            column.type,
            expr=attr_expr(path),
            label_zh=column.label.zh_cn,
            label_en=column.label.en,
            mono=column.mono,
        )
    report_field = next((item for item in spec.report_fields if item.key == key), None)
    if report_field is not None:
        if "[]" in report_field.path:
            return ResolvedField(
                key,
                report_field.type,
                jsonpath=_jsonpath(report_field.path),
                label_zh=report_field.label.zh_cn,
                label_en=report_field.label.en,
            )
        path = ColumnPath(
            key=key,
            type=report_field.type,
            path=split_path(report_field.path),
            exists=False,
            count=False,
        )
        return ResolvedField(
            key,
            report_field.type,
            expr=attr_expr(path),
            label_zh=report_field.label.zh_cn,
            label_en=report_field.label.en,
        )
    if spec.findings and key in FINDING_FIELDS:
        zh, en = FINDING_LABELS[key]
        return ResolvedField(
            key, "number", expr=_findings_expr(key, RESOURCE), label_zh=zh, label_en=en
        )
    raise ApiError(422, "validation_error", f"当前主体类型没有字段 {key}", {"field": key})


# --------------------------------------------------------------------------- SQL


def _never_null(predicate: ColumnElement[Any]) -> ColumnElement[Any]:
    """A leaf predicate is true or false, never NULL, so `not` means "does not match".

    A NULL row (a null column, a configuration without the path) does not match, and taking
    the opposite of that must include it.
    """
    return func.coalesce(predicate, false())


def _jsonpath_exists(path: str, predicate: str | None = None) -> ColumnElement[Any]:
    full = path if predicate is None else f"{path} ? ({predicate})"
    return _never_null(
        func.jsonb_path_exists(
            R.configuration, cast(literal(full), JSONPATH), cast(literal("{}"), JSONB)
        )
    )


def _string_literal(value: str) -> str:
    """A JSONPath string literal: JSON's double-quoted escaping is compatible."""
    return json.dumps(value, ensure_ascii=False)


def _array_condition(field: ResolvedField, op: str, value: Any) -> ColumnElement[Any]:
    assert field.jsonpath is not None
    path = field.jsonpath
    if op == "exists":
        return _jsonpath_exists(path)
    if op == "notExists":
        return not_(_jsonpath_exists(path))
    if op in ("eq", "neq"):
        predicate = f"@ == {_string_literal(value)}"
        found = _jsonpath_exists(path, predicate)
        return found if op == "eq" else not_(found)
    if op in ("in", "notIn"):
        values = [str(item) for item in value]
        predicate = " || ".join(f"@ == {_string_literal(item)}" for item in values)
        found = _jsonpath_exists(path, predicate)
        return found if op == "in" else not_(found)
    if op in ("contains", "startsWith"):
        pattern = (
            (".*" + re.escape(str(value)) + ".*")
            if op == "contains"
            else ("^" + re.escape(str(value)))
        )
        predicate = f'@ like_regex {_string_literal(pattern)} flag "i"'
        return _jsonpath_exists(path, predicate)
    raise ApiError(422, "validation_error", f"字段 {field.key} 不支持 {op}", {"field": field.key})


def _scalar_condition(field: ResolvedField, op: str, value: Any) -> ColumnElement[Any]:
    assert field.expr is not None
    expr = field.expr
    if field.type == "tags":
        key = value["key"] if isinstance(value, dict) else ""
        wanted = {"key": key}
        if op == "hasValue":
            wanted["value"] = value["value"]
        condition = R.tags.contains([wanted])
        return condition if op != "missing" else not_(condition)
    if op == "exists":
        return expr.is_not(None)
    if op == "notExists":
        return expr.is_(None)
    if op == "eq":
        return _never_null(expr == value)
    if op == "neq":
        return expr.is_distinct_from(value)
    if op == "in":
        return _never_null(expr.in_(value))
    if op == "notIn":
        return or_(expr.is_(None), expr.not_in(value))
    if op == "contains":
        return _never_null(expr.ilike(like_pattern(str(value))))
    if op == "startsWith":
        escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return _never_null(expr.ilike(escaped + "%"))
    if op == "beforeNow":
        return _never_null(expr < func.now())
    if op == "afterNow":
        return _never_null(expr > func.now())
    if op == "olderThanDays":
        return _never_null(expr < func.now() - func.make_interval(0, 0, 0, int(value)))
    if op in ("gt", "gte", "lt", "lte"):
        if field.type == "datetime" and isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _never_null(
            {
                "gt": expr > value,
                "gte": expr >= value,
                "lt": expr < value,
                "lte": expr <= value,
            }[op]
        )
    raise ApiError(422, "validation_error", f"字段 {field.key} 不支持 {op}", {"field": field.key})


def _related_column(key: str, spec: Any, registry: TypeRegistry) -> ResolvedField:
    """A related resource's value as a display column: every value, deduplicated.

    A relationship can have several targets, so the cell is an array; a single target still
    renders as one value. Only the fields `allowed_fields` exposes for the relationship exist.
    """
    prefix, _, related_key = key.partition(".")
    to_type = relation_prefixes(spec.type_name, registry).get(prefix)
    target_spec = registry.get(to_type) if to_type is not None else None
    if target_spec is None or not related_key:
        raise ApiError(422, "validation_error", f"当前主体类型没有字段 {key}", {"field": key})
    target = RESOURCE.alias("related")
    field = _related_field(related_key, target, target_spec)
    assert field.expr is not None
    values = func.array_agg(func.distinct(field.expr)).filter(field.expr.is_not(None))
    expr = (
        select(values)
        .select_from(RELATION.join(target, RELATION.c.to_resource_id == target.c.id))
        .where(
            RELATION.c.from_resource_id == R.id,
            RELATION.c.to_resource_type == to_type,
        )
        .correlate(RESOURCE)
        .scalar_subquery()
    )
    return ResolvedField(
        key,
        field.type,
        expr=expr,
        label_zh=f"{label_of(target_spec.label, 'zh-CN')} · {field.label_zh}",
        label_en=f"{label_of(target_spec.label, 'en')} · {field.label_en}",
        mono=field.mono,
    )


def _related_field(key: str, table: Any, spec: Any) -> ResolvedField:
    """A field of a related resource, read through `table` (an alias of the resource table)."""
    # A common field wins over a type column of the same key (see `resolve_field`).
    common = COMMON_BY_KEY.get(key)
    if common is not None and key != "tags":
        if key in _RELATED_RESOURCE_COMMON:
            expr: ColumnElement[Any] = getattr(table.c, _RELATED_RESOURCE_COMMON[key])
        else:
            expr = (
                select(getattr(ACCOUNT.c, _RELATED_ACCOUNT_COMMON[key]))
                .where(ACCOUNT.c.account_id == table.c.account_id)
                .scalar_subquery()
            )
        return ResolvedField(
            key,
            common.type,
            expr=expr,
            label_zh=common.zh,
            label_en=common.en,
            mono=common.mono,
        )
    if spec.findings and key in FINDING_FIELDS:
        zh, en = FINDING_LABELS[key]
        return ResolvedField(
            key, "number", expr=_findings_expr(key, table), label_zh=zh, label_en=en
        )
    column = next((item for item in spec.columns if item.key == key), None)
    if column is not None:
        path = column_paths(spec)[key]
        return ResolvedField(
            key,
            column.type,
            expr=attr_expr(path, table=table),
            label_zh=column.label.zh_cn,
            label_en=column.label.en,
            mono=column.mono,
        )
    report_field = next((item for item in spec.report_fields if item.key == key), None)
    if report_field is not None and "[]" not in report_field.path:
        path = ColumnPath(
            key=key,
            type=report_field.type,
            path=split_path(report_field.path),
            exists=False,
            count=False,
        )
        return ResolvedField(
            key,
            report_field.type,
            expr=attr_expr(path, table=table),
            label_zh=report_field.label.zh_cn,
            label_en=report_field.label.en,
        )
    raise ApiError(422, "validation_error", f"关联资源没有字段 {key}", {"field": key})


def _related_condition(
    condition: Condition, definition: ReportDefinition, registry: TypeRegistry
) -> ColumnElement[Any]:
    """`image.creationDate`: any related resource of the declared type matches."""
    assert condition.field is not None and condition.op is not None
    prefix, _, key = condition.field.partition(".")
    to_type = relation_prefixes(definition.subject_type, registry).get(prefix)
    if to_type is None:
        raise ApiError(
            422, "validation_error", f"当前主体类型没有关联 {prefix}", {"field": condition.field}
        )
    target = RESOURCE.alias("related")
    field = _related_field(key, target, registry.get(to_type))
    predicate = _scalar_condition(field, condition.op, condition.value)
    return (
        select(literal(1))
        .select_from(RELATION.join(target, RELATION.c.to_resource_id == target.c.id))
        .where(
            RELATION.c.from_resource_id == R.id,
            RELATION.c.to_resource_type == to_type,
            predicate,
        )
        .exists()
    )


def _condition(condition: Condition, definition: ReportDefinition, registry: TypeRegistry):
    if condition.all is not None:
        return and_(*[_condition(child, definition, registry) for child in condition.all])
    if condition.any is not None:
        return or_(*[_condition(child, definition, registry) for child in condition.any])
    if condition.not_ is not None:
        return not_(_condition(condition.not_, definition, registry))
    assert condition.field is not None and condition.op is not None
    if "." in condition.field:
        return _related_condition(condition, definition, registry)
    field = resolve_field(condition.field, definition, registry)
    if field.type == "tags":
        return _scalar_condition(
            field, condition.op, {"key": condition.key, "value": condition.value}
        )
    if field.jsonpath is not None:
        return _array_condition(field, condition.op, condition.value)
    return _scalar_condition(field, condition.op, condition.value)


def parse_scope(value: str | None) -> Scope | None:
    if not value:
        return None
    kind, _, name = value.partition(":")
    if kind == "app" and name:
        return ("app", name)
    if kind == "acct" and name:
        return ("acct", name)
    raise ApiError(400, "invalid_filter", "范围参数不合法", {"s": value})


def _scope_condition(scope: Scope | None) -> ColumnElement[bool] | None:
    if scope is None:
        return None
    kind, value = scope
    return A.eim_id == value if kind == "app" else R.account_id == value


def compile_where(
    definition: ReportDefinition, registry: TypeRegistry, scope: Scope | None
) -> ColumnElement[bool]:
    conditions: list[ColumnElement[bool]] = []
    if definition.subject_type is not None:
        conditions.append(R.resource_type == definition.subject_type)
    if "status" not in referenced_fields(definition.where):
        conditions.append(R.status.in_(("active", "missing")))
    conditions.append(_condition(definition.where, definition, registry))
    scope_condition = _scope_condition(scope)
    if scope_condition is not None:
        conditions.append(scope_condition)
    return and_(*conditions)


# --------------------------------------------------------------------------- editor palette


def field_palette(
    registry: TypeRegistry, language: Language, subject_type: str | None
) -> list[ReportFieldInfo]:
    """Every field a condition of this subject type may name, for the report editor."""
    if subject_type is not None and subject_type not in registry:
        raise ApiError(422, "validation_error", "未知的资源类型", {"resourceType": subject_type})
    fields = [
        ReportFieldInfo(
            key=column.key,
            label=column.label(language),
            type=column.type,
            group="common",
        )
        for column in COMMON_COLUMNS
        if column.key != "tags"
    ]
    fields.append(
        ReportFieldInfo(
            key="tags", label="标签" if language == "zh-CN" else "Tags", type="tags", group="common"
        )
    )
    relation_labels = {
        "inboundRelationshipCount": ("被引用", "Referenced by"),
        "outboundRelationshipCount": ("引用", "References"),
    }
    for key in RELATION_COUNT_FIELDS:
        zh, en = relation_labels[key]
        fields.append(
            ReportFieldInfo(
                key=key,
                label=zh if language == "zh-CN" else en,
                type="number",
                group="common",
            )
        )
    if subject_type is not None:
        spec = registry.get(subject_type)
        taken = {field.key for field in fields}
        fields.extend(
            ReportFieldInfo(
                key=column.key,
                label=label_of(column.label, language),
                type=column.type,
                group="type",
            )
            for column in spec.columns
            if column.key not in taken
        )
        fields.extend(
            ReportFieldInfo(
                key=field.key,
                label=label_of(field.label, language),
                type=field.type,
                array="[]" in field.path,
                group="type",
            )
            for field in spec.report_fields
        )
        if spec.findings:
            fields.extend(
                ReportFieldInfo(
                    key=key,
                    label=zh if language == "zh-CN" else en,
                    type=FINDING_FIELDS[key],
                    group="type",
                )
                for key, (zh, en) in FINDING_LABELS.items()
            )
        taken = {field.key for field in fields}
        for relationship in spec.derived_relationships:
            target = registry.get(relationship.to_type)
            prefix = relationship.name.lower()
            type_label = label_of(target.label, language)
            for common_key, common in COMMON_BY_KEY.items():
                if common_key == "tags":
                    continue
                field_key = f"{prefix}.{common_key}"
                taken.add(field_key)
                fields.append(
                    ReportFieldInfo(
                        key=field_key,
                        label=(f"{type_label} · {common.zh if language == 'zh-CN' else common.en}"),
                        type=common.type,
                        group="relation",
                        array=True,
                    )
                )
            for column in target.columns:
                key = f"{prefix}.{column.key}"
                if key in taken:
                    continue
                fields.append(
                    ReportFieldInfo(
                        key=key,
                        label=f"{type_label} · {label_of(column.label, language)}",
                        type=column.type,
                        group="relation",
                        array=True,
                    )
                )
            for report_field in target.report_fields:
                key = f"{prefix}.{report_field.key}"
                if "[]" in report_field.path or key in taken:
                    continue
                fields.append(
                    ReportFieldInfo(
                        key=key,
                        label=f"{type_label} · {label_of(report_field.label, language)}",
                        type=report_field.type,
                        group="relation",
                        array=True,
                    )
                )
            if target.findings:
                for field_key, (zh, en) in FINDING_LABELS.items():
                    key = f"{prefix}.{field_key}"
                    if key in taken:
                        continue
                    fields.append(
                        ReportFieldInfo(
                            key=key,
                            label=f"{type_label} · {zh if language == 'zh-CN' else en}",
                            type=FINDING_FIELDS[field_key],
                            group="relation",
                            array=True,
                        )
                    )
    return fields


# --------------------------------------------------------------------------- output


@dataclass(frozen=True)
class Column:
    field: ResolvedField
    expr: ColumnElement[Any] | None
    array: bool = False


def _column_expr(field: ResolvedField) -> ColumnElement[Any] | None:
    if field.jsonpath is not None:
        return func.jsonb_path_query_array(R.configuration, cast(literal(field.jsonpath), JSONPATH))
    return field.expr


def report_columns(definition: ReportDefinition, registry: TypeRegistry) -> list[Column]:
    keys = list(definition.columns)
    if not keys:
        keys = list(DEFAULT_COLUMNS)
        if definition.subject_type is not None:
            # A type column can repeat a common key (the ECS task definition has `status`).
            keys.extend(
                column.key
                for column in registry.get(definition.subject_type).columns
                if column.key not in DEFAULT_COLUMNS
            )
    return [
        Column(
            field=field,
            expr=_column_expr(field),
            # A relation field always renders as an array (there may be several targets).
            array=field.jsonpath is not None or "." in field.key,
        )
        for field in (resolve_field(key, definition, registry) for key in keys)
    ]


def _column_infos(columns: list[Column], language: Language) -> list[ReportColumnInfo]:
    return [
        ReportColumnInfo(
            key=column.field.key,
            label=column.field.label(language),
            type=column.field.type,
            mono=column.field.mono,
            array=column.array,
        )
        for column in columns
    ]


def _sort_expr(key: str, columns: dict[str, Column]) -> ColumnElement[Any] | None:
    common = COMMON_BY_KEY.get(key)
    if common is not None and common.expr is not None:
        return common.expr
    column = columns.get(key)
    if column is not None and column.expr is not None and not column.array:
        return column.expr
    if key in RELATION_COUNT_FIELDS:
        return _relation_count_sql("in" if key.startswith("in") else "out")
    return None


def _order_by(sort: str | None, columns: list[Column]) -> list[ColumnElement[Any]]:
    clauses: list[ColumnElement[Any]] = []
    by_key = {column.field.key: column for column in columns}
    for token in (sort or DEFAULT_SORT).split(","):
        token = token.strip()
        descending = token.startswith("-")
        key = token.removeprefix("-")
        expr = _sort_expr(key, by_key)
        if expr is None:
            raise ApiError(400, "invalid_sort", f"不能按 {key or token} 排序", {"sort": token})
        clauses.append(expr.desc().nulls_last() if descending else expr.asc().nulls_last())
    clauses.append(R.id.asc())
    return clauses


def _report_row(row: Row[Any], columns: list[Column]) -> ReportRow:
    item = to_item(row, [])
    mapping = row._mapping
    values: dict[str, Any] = {}
    for index, column in enumerate(columns):
        value = mapping[f"col_{index}"]
        if column.array:
            # No related row is SQL NULL; the contract says an array, so send [].
            values[column.field.key] = [attr_value(entry) for entry in (value or [])]
        elif isinstance(value, list):
            values[column.field.key] = [attr_value(entry) for entry in value]
        else:
            values[column.field.key] = attr_value(value)
    return ReportRow(**item.model_dump(), values=values)


def _count(
    session: Session,
    definition: ReportDefinition,
    registry: TypeRegistry,
    scope: Scope | None,
    *,
    report_id: int,
    cache: Cache,
) -> int:
    digest = hashlib.sha256(definition.model_dump_json(by_alias=True).encode()).hexdigest()
    key = cache.key(
        "reports", "count", str(report_id), scope[0] + ":" + scope[1] if scope else "all", digest
    )
    cached = cache.get(key)
    if isinstance(cached, int) and not isinstance(cached, bool):
        return cached
    statement = (
        select(func.count())
        .select_from(RESOURCE.join(ACCOUNT, R.account_id == A.account_id))
        .where(compile_where(definition, registry, scope))
    )
    total = session.execute(statement).scalar_one()
    cache.set(key, total)
    return total


# --------------------------------------------------------------------------- CRUD


def _to_info(report: Report) -> ReportInfo:
    assert report.id is not None
    return ReportInfo(
        id=report.id,
        name=report.name,
        description=report.description,
        subject_type=report.subject_type,
        enabled=report.enabled,
        created_by=report.created_by,
        updated_by=report.updated_by,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


def _definition(report: Report) -> ReportDefinition:
    return ReportDefinition.model_validate(report.definition)


def _valid_definition(report: Report, registry: TypeRegistry) -> ReportDefinition:
    try:
        definition = _definition(report)
        validate_definition(definition, registry)
    except (DefinitionError, ValueError) as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc
    return definition


def list_reports(session: Session, *, enabled: bool | None = None) -> list[ReportInfo]:
    statement = select(Report).order_by(col(Report.name))
    if enabled is not None:
        statement = statement.where(col(Report.enabled) == enabled)
    return [_to_info(report) for report in session.execute(statement).scalars()]


def get_report(session: Session, report_id: int) -> Report:
    report = session.get(Report, report_id)
    if report is None:
        raise ApiError(404, "report_not_found", "报表不存在", {"id": report_id})
    return report


def detail(session: Session, registry: TypeRegistry, report_id: int) -> ReportDetail:
    report = get_report(session, report_id)
    definition = _valid_definition(report, registry)
    return ReportDetail(
        **_to_info(report).model_dump(),
        definition_version=definition.definition_version,
        where=definition.where,
        columns=definition.columns,
    )


def _save(
    session: Session,
    registry: TypeRegistry,
    report: Report | None,
    *,
    name: str,
    description: str | None,
    subject_type: str | None,
    where: Condition,
    columns: list[str],
    enabled: bool,
    caller_id: str | None,
) -> Report:
    definition = ReportDefinition(subject_type=subject_type, where=where, columns=columns)
    try:
        validate_definition(definition, registry)
    except DefinitionError as exc:
        raise ApiError(422, "validation_error", str(exc), exc.details) from exc
    now = datetime.now(UTC)
    taken = session.execute(select(Report).where(col(Report.name) == name)).scalars().first()
    if taken is not None and (report is None or taken.id != report.id):
        raise ApiError(409, "report_name_taken", "报表名称已存在", {"name": name})
    if report is None:
        report = Report(
            name=name,
            description=description,
            subject_type=subject_type,
            definition_version=definition.definition_version,
            definition=definition.model_dump(mode="json", by_alias=True),
            enabled=enabled,
            created_by=caller_id,
            updated_by=caller_id,
            created_at=now,
            updated_at=now,
        )
    else:
        report.name = name
        report.description = description
        report.subject_type = subject_type
        report.definition_version = definition.definition_version
        report.definition = definition.model_dump(mode="json", by_alias=True)
        report.enabled = enabled
        report.updated_by = caller_id
        report.updated_at = now
    session.add(report)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "report_name_taken", "报表名称已存在", {"name": name}) from None
    session.refresh(report)
    return report


def create_report(
    session: Session, registry: TypeRegistry, payload: Any, caller_id: str | None
) -> ReportDetail:
    report = _save(
        session,
        registry,
        None,
        name=payload.name,
        description=payload.description,
        subject_type=payload.subject_type,
        where=payload.where,
        columns=payload.columns,
        enabled=payload.enabled,
        caller_id=caller_id,
    )
    assert report.id is not None
    return detail(session, registry, report.id)


def update_report(
    session: Session, registry: TypeRegistry, report_id: int, payload: Any, caller_id: str | None
) -> ReportDetail:
    report = get_report(session, report_id)
    _save(
        session,
        registry,
        report,
        name=payload.name,
        description=payload.description,
        subject_type=payload.subject_type,
        where=payload.where,
        columns=payload.columns,
        enabled=payload.enabled,
        caller_id=caller_id,
    )
    return detail(session, registry, report_id)


def delete_report(session: Session, report_id: int) -> None:
    report = get_report(session, report_id)
    session.delete(report)
    session.commit()


# --------------------------------------------------------------------------- evaluation


def overview(
    session: Session,
    registry: TypeRegistry,
    cache: Cache,
    scope: Scope | None,
    *,
    enabled: bool = True,
) -> ReportOverview:
    statement = select(Report).where(col(Report.enabled) == enabled).order_by(col(Report.name))
    items: list[ReportOverviewItem] = []
    for report in session.execute(statement).scalars():
        assert report.id is not None
        try:
            definition = _definition(report)
            validate_definition(definition, registry)
        except (DefinitionError, ValueError) as exc:
            items.append(
                ReportOverviewItem(
                    report_id=report.id,
                    name=report.name,
                    subject_type=report.subject_type,
                    count=0,
                    error=str(exc),
                )
            )
            continue
        items.append(
            ReportOverviewItem(
                report_id=report.id,
                name=report.name,
                subject_type=report.subject_type,
                count=_count(
                    session, definition, registry, scope, report_id=report.id, cache=cache
                ),
            )
        )
    return ReportOverview(generated_at=datetime.now(UTC), items=items)


def results(
    session: Session,
    registry: TypeRegistry,
    cache: Cache,
    report_id: int,
    *,
    page: int,
    page_size: int,
    sort: str | None,
    scope: Scope | None,
    language: Language,
) -> ReportPage:
    report = get_report(session, report_id)
    definition = _valid_definition(report, registry)
    columns = report_columns(definition, registry)
    expressions = [
        column.expr.label(f"col_{index}")
        if column.expr is not None
        else literal(None).label(f"col_{index}")
        for index, column in enumerate(columns)
    ]
    statement = (
        item_select(expressions)
        .where(compile_where(definition, registry, scope))
        .order_by(*_order_by(sort, columns))
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = [_report_row(row, columns) for row in session.execute(statement)]
    total = _count(session, definition, registry, scope, report_id=report_id, cache=cache)
    return ReportPage(
        items=rows,
        page=page,
        page_size=page_size,
        total=total,
        columns=_column_infos(columns, language),
        generated_at=datetime.now(UTC),
    )


def _report_cell(value: Any) -> str:
    if isinstance(value, list):
        return _cell(";".join(str(item) for item in value))
    if isinstance(value, dict):
        return _cell(json.dumps(value, ensure_ascii=False))
    return _cell(value)


def export(
    session: Session,
    registry: TypeRegistry,
    report_id: int,
    *,
    format: Literal["csv", "jsonl"],
    language: Language,
    scope: Scope | None,
    sort: str | None,
) -> Export:
    report = get_report(session, report_id)
    definition = _valid_definition(report, registry)
    columns = report_columns(definition, registry)
    expressions = [
        column.expr.label(f"col_{index}")
        if column.expr is not None
        else literal(None).label(f"col_{index}")
        for index, column in enumerate(columns)
    ]
    statement = (
        item_select(expressions)
        .where(compile_where(definition, registry, scope))
        .order_by(*_order_by(sort, columns))
        .execution_options(yield_per=BATCH_SIZE)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"aws-inventory-report-{report_id}-{stamp}"
    if format == "jsonl":

        def chunks():
            for batch in session.execute(statement).partitions():
                yield "".join(
                    _report_row(row, columns).model_dump_json(by_alias=True) + "\n" for row in batch
                )

        return Export("application/x-ndjson", f"{filename}.jsonl", chunks())

    def csv_chunks():
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([column.field.label(language) for column in columns])
        yield BOM + buffer.getvalue()
        for batch in session.execute(statement).partitions():
            buffer.seek(0)
            buffer.truncate()
            for row in batch:
                mapping = row._mapping
                writer.writerow(
                    _report_cell(attr_value(mapping[f"col_{index}"]))
                    for index in range(len(columns))
                )
            yield buffer.getvalue()

    return Export("text/csv; charset=utf-8", f"{filename}.csv", csv_chunks())
