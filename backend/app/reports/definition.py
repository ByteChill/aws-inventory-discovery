"""The saved definition of a custom report (v2): a boolean condition tree over inventory data.

The definition is stored as JSONB. It is validated against the resource type registry when it
is saved, and again before it is evaluated (a resource type can disappear between deploys).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.query.columns import COMMON_BY_KEY
from app.types.registry import TypeRegistry

FieldType = Literal["text", "enum", "number", "bool", "datetime", "tags"]
Operator = Literal[
    "eq",
    "neq",
    "in",
    "notIn",
    "contains",
    "startsWith",
    "exists",
    "notExists",
    "gt",
    "gte",
    "lt",
    "lte",
    "olderThanDays",
    "beforeNow",
    "afterNow",
    "has",
    "hasValue",
    "missing",
]

TEXT_OPS: frozenset[str] = frozenset(
    {"eq", "neq", "in", "notIn", "contains", "startsWith", "exists", "notExists"}
)
NUMBER_OPS: frozenset[str] = frozenset(
    {"eq", "neq", "in", "notIn", "gt", "gte", "lt", "lte", "exists"}
)
ENUM_OPS: frozenset[str] = frozenset({"eq", "neq", "in", "notIn", "exists", "notExists"})
BOOL_OPS: frozenset[str] = frozenset({"eq", "neq"})
DATETIME_OPS: frozenset[str] = frozenset(
    {"gt", "gte", "lt", "lte", "olderThanDays", "beforeNow", "afterNow"}
)
NO_VALUE_OPS: frozenset[str] = frozenset(
    {"exists", "notExists", "missing", "beforeNow", "afterNow"}
)
TAG_OPS: frozenset[str] = frozenset({"has", "hasValue", "missing"})

OPS_BY_TYPE: dict[str, frozenset[str]] = {
    "text": TEXT_OPS,
    "enum": ENUM_OPS,
    "number": NUMBER_OPS,
    "bool": BOOL_OPS,
    "datetime": DATETIME_OPS,
    "tags": TAG_OPS,
}

# Plenty for a report, and it keeps a saved definition from becoming a query program.
MAX_DEPTH = 10
MAX_CONDITIONS = 50
# The definition is re-executed by every overview; keep one condition from making it huge.
MAX_VALUE_LENGTH = 500
MAX_LIST_ITEMS = 100

RELATION_COUNT_FIELDS = ("inboundRelationshipCount", "outboundRelationshipCount")

# Findings are summarised per resource (D-07); a type opts in with `"findings": true`.
FINDING_FIELDS: dict[str, str] = {
    "findingsCriticalCount": "number",
    "findingsHighCount": "number",
    "findingsMediumCount": "number",
    "findingsLowCount": "number",
    "findingsMaxScore": "number",
}


class DefinitionError(ValueError):
    """The definition cannot be evaluated; `details` points at the condition."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details or None


class DefinitionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True
    )


class Condition(DefinitionModel):
    """One node of the condition tree. Exactly one form is set: `all`, `any`, `not`, or a
    field comparison."""

    all: list[Condition] | None = None
    any: list[Condition] | None = None
    not_: Condition | None = Field(default=None, alias="not")
    field: str | None = None
    op: Operator | None = None
    value: Any = None
    key: str | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        groups = [self.all is not None, self.any is not None, self.not_ is not None]
        if self.field is None and sum(groups) != 1:
            raise ValueError("a condition must have exactly one of all, any, not or field")
        if self.field is not None and (any(groups) or self.op is None):
            raise ValueError("a field condition has field and op only")
        for group in (self.all, self.any):
            if group is not None and not group:
                raise ValueError("all and any need at least one condition")
        return self


class ReportDefinition(DefinitionModel):
    definition_version: int = 1
    subject_type: str | None = None
    where: Condition
    columns: list[str] = []


def allowed_fields(subject_type: str | None, registry: TypeRegistry) -> dict[str, str]:
    """The fields a report with this subject type may use, with their value type."""
    fields: dict[str, str] = {
        key: column.type for key, column in COMMON_BY_KEY.items() if key != "tags"
    }
    fields["tags"] = "tags"
    fields.update(dict.fromkeys(RELATION_COUNT_FIELDS, "number"))
    if subject_type is None:
        return fields
    spec = registry.get(subject_type)
    for column in spec.columns:
        # A common field wins over a type column of the same key (see `resolve_field`).
        fields.setdefault(column.key, column.type)
    for report_field in spec.report_fields:
        # The same precedence as `resolve_field`: common fields and columns win.
        fields.setdefault(report_field.key, report_field.type)
    if spec.findings:
        for key, field_type in FINDING_FIELDS.items():
            fields.setdefault(key, field_type)
    # One hop over a declared relationship: `image.creationDate`. Config's own relationships
    # are not declared here, so they cannot be used in a saved definition.
    for relationship in spec.derived_relationships:
        target = registry.get(relationship.to_type)
        prefix = relationship.name.lower()
        # The target's common fields come first: a common field wins over a type column
        # with the same key (see `resolve_field`).
        for key, column in COMMON_BY_KEY.items():
            if key != "tags":
                fields.setdefault(f"{prefix}.{key}", column.type)
        for column in target.columns:
            fields.setdefault(f"{prefix}.{column.key}", column.type)
        for report_field in target.report_fields:
            # An array-crossing field is usable: a condition means "any element matches".
            fields.setdefault(f"{prefix}.{report_field.key}", report_field.type)
        if target.findings:
            for key, field_type in FINDING_FIELDS.items():
                fields.setdefault(f"{prefix}.{key}", field_type)
    return fields


def relation_prefixes(subject_type: str | None, registry: TypeRegistry) -> dict[str, str]:
    """`image` -> the target type of the declaration it comes from."""
    if subject_type is None:
        return {}
    spec = registry.get(subject_type)
    return {
        relationship.name.lower(): relationship.to_type
        for relationship in spec.derived_relationships
    }


def _check_value(field_type: str, op: str, value: Any, where: str) -> None:
    if op in NO_VALUE_OPS:
        if value is not None:
            raise DefinitionError(f"{where}: {op} takes no value", condition=where)
        return
    if op in ("in", "notIn"):
        if not isinstance(value, list) or not value:
            raise DefinitionError(f"{where}: {op} needs a non-empty list", condition=where)
        values = value
    else:
        if value is None:
            raise DefinitionError(f"{where}: {op} needs a value", condition=where)
        values = [value]

    if len(values) > MAX_LIST_ITEMS:
        raise DefinitionError(f"{where}: too many values", condition=where)
    if field_type in ("text", "enum"):
        if not all(isinstance(item, str) for item in values):
            raise DefinitionError(f"{where}: expected text", condition=where)
        if any(len(item) > MAX_VALUE_LENGTH for item in values):
            raise DefinitionError(f"{where}: value too long", condition=where)
    elif field_type == "number":
        if not all(isinstance(item, int | float) and not isinstance(item, bool) for item in values):
            raise DefinitionError(f"{where}: expected a number", condition=where)
        try:
            finite = all(math.isfinite(item) for item in values)
        except OverflowError:  # an integer too large even to convert to float
            finite = False
        if not finite:
            raise DefinitionError(f"{where}: expected a number", condition=where)
    elif field_type == "bool":
        if not all(isinstance(item, bool) for item in values):
            raise DefinitionError(f"{where}: expected true or false", condition=where)
    elif field_type == "datetime":
        if op == "olderThanDays":
            days = values[0]
            if len(values) != 1 or not isinstance(days, int) or isinstance(days, bool) or days < 0:
                raise DefinitionError(
                    f"{where}: olderThanDays needs a number of days", condition=where
                )
            return
        for item in values:
            if not isinstance(item, str):
                raise DefinitionError(f"{where}: expected an ISO 8601 time", condition=where)
            try:
                datetime.fromisoformat(item.replace("Z", "+00:00"))
            except ValueError:
                raise DefinitionError(
                    f"{where}: expected an ISO 8601 time", condition=where
                ) from None


def validate_definition(definition: ReportDefinition, registry: TypeRegistry) -> None:
    """Raise `DefinitionError` unless the definition can be evaluated against `registry`."""
    if definition.definition_version != 1:
        raise DefinitionError("不支持的报表定义版本")
    if definition.subject_type is not None and definition.subject_type not in registry:
        raise DefinitionError("未知的资源类型", resourceType=definition.subject_type)

    fields = allowed_fields(definition.subject_type, registry)
    for column in definition.columns:
        if column not in fields:
            raise DefinitionError(f"报表没有这个列 {column}", column=column)
    if len(definition.columns) != len(set(definition.columns)):
        raise DefinitionError("报表的列重复")

    count = _walk(definition.where, fields, depth=0, counter=[0])
    if count == 0:
        raise DefinitionError("报表至少需要一个条件")


def _walk(condition: Condition, fields: dict[str, str], *, depth: int, counter: list[int]) -> int:
    if depth > MAX_DEPTH:
        raise DefinitionError("条件嵌套太深")
    counter[0] += 1
    if counter[0] > MAX_CONDITIONS:
        raise DefinitionError("条件太多")

    total = 1
    if condition.all is not None:
        total += sum(
            _walk(child, fields, depth=depth + 1, counter=counter) for child in condition.all
        )
    elif condition.any is not None:
        total += sum(
            _walk(child, fields, depth=depth + 1, counter=counter) for child in condition.any
        )
    elif condition.not_ is not None:
        total += _walk(condition.not_, fields, depth=depth + 1, counter=counter)
    else:
        assert condition.field is not None and condition.op is not None
        field_type = fields.get(condition.field)
        if field_type is None:
            raise DefinitionError(f"当前主体类型没有字段 {condition.field}", field=condition.field)
        op = condition.op
        if field_type == "tags":
            if op not in TAG_OPS:
                raise DefinitionError(f"标签不支持 {op}", field=condition.field)
            if not condition.key:
                raise DefinitionError("标签条件需要 key", field=condition.field)
            if len(condition.key) > MAX_VALUE_LENGTH:
                raise DefinitionError("标签 key 太长", field=condition.field)
            if (op == "hasValue") != (condition.value is not None):
                raise DefinitionError(
                    "只有 hasValue 需要值，has 和 missing 不要值", field=condition.field
                )
            if op == "hasValue":
                if not isinstance(condition.value, str):
                    raise DefinitionError("标签值需要文本", field=condition.field)
                if len(condition.value) > MAX_VALUE_LENGTH:
                    raise DefinitionError("标签值太长", field=condition.field)
        else:
            if condition.key is not None:
                raise DefinitionError("只有标签条件有 key", field=condition.field)
            if op not in OPS_BY_TYPE[field_type]:
                raise DefinitionError(f"{field_type} 字段不支持 {op}", field=condition.field)
            _check_value(field_type, op, condition.value, where=condition.field)
    return total


def referenced_fields(condition: Condition) -> set[str]:
    """The field keys the tree mentions, used for the default status exclusion."""
    found: set[str] = set()
    if condition.field is not None:
        found.add(condition.field)
    for child in condition.all or ():
        found |= referenced_fields(child)
    for child in condition.any or ():
        found |= referenced_fields(child)
    if condition.not_ is not None:
        found |= referenced_fields(condition.not_)
    return found
