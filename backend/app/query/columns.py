"""Columns of the resource list: the common ones, how type columns become SQL, and labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import Boolean, ColumnElement, Numeric, Text, case, cast, false, func, true
from sqlalchemy.dialects.postgresql import TIMESTAMP

from app.query.tables import A, R
from app.types.columns import ColumnPath
from app.types.spec import Column

Language = Literal["zh-CN", "en"]
LANGUAGES: tuple[Language, ...] = ("zh-CN", "en")

# A scope not scanned successfully for this long makes its resources `stale`.
STALE_AFTER = timedelta(hours=12)


def pick_language(accept_language: str | None) -> Language:
    """The first supported language of an Accept-Language header; Chinese by default."""
    if accept_language:
        weighted: list[tuple[float, int, str]] = []
        for position, part in enumerate(accept_language.split(",")):
            tag, _, params = part.strip().partition(";")
            quality = 1.0
            if params.strip().startswith("q="):
                try:
                    quality = float(params.strip()[2:])
                except ValueError:
                    quality = 0.0
            weighted.append((-quality, position, tag.strip().lower()))
        for _, _, tag in sorted(weighted):
            if tag.startswith("zh"):
                return "zh-CN"
            if tag.startswith("en"):
                return "en"
    return "zh-CN"


def label_of(label: Any, language: Language) -> str:
    """A spec `Label` in the given language."""
    return label.zh_cn if language == "zh-CN" else label.en


# --------------------------------------------------------------------------- common columns


@dataclass(frozen=True)
class CommonColumn:
    key: str
    zh: str
    en: str
    type: Literal["text", "enum", "number", "bool", "datetime"]
    expr: ColumnElement[Any] | None  # None: not sortable in SQL
    mono: bool = False
    filterable: bool = False

    @property
    def sortable(self) -> bool:
        return self.expr is not None

    def label(self, language: Language) -> str:
        return self.zh if language == "zh-CN" else self.en


COMMON_COLUMNS: tuple[CommonColumn, ...] = (
    CommonColumn("resourceName", "名称", "Name", "text", R.resource_name),
    CommonColumn("resourceType", "类型", "Type", "enum", R.resource_type, filterable=True),
    CommonColumn("resourceId", "资源 ID", "Resource ID", "text", R.resource_id, mono=True),
    CommonColumn("arn", "ARN", "ARN", "text", R.arn, mono=True),
    CommonColumn("accountId", "账号 ID", "Account ID", "text", R.account_id, True, filterable=True),
    CommonColumn("accountName", "账号名称", "Account name", "text", A.account_name),
    CommonColumn("eimId", "EIM", "EIM", "text", A.eim_id, True, filterable=True),
    CommonColumn("eimApp", "EIM 应用", "EIM app", "text", A.eim_app),
    CommonColumn("baId", "BA", "BA", "text", A.ba_id, True, filterable=True),
    CommonColumn("accountType", "环境", "Environment", "enum", A.account_type, filterable=True),
    CommonColumn("awsRegion", "区域", "Region", "enum", R.aws_region, filterable=True),
    CommonColumn("status", "状态", "Status", "enum", R.status, filterable=True),
    CommonColumn("tags", "标签", "Tags", "text", None, filterable=True),
    CommonColumn(
        "configurationItemCaptureTime",
        "配置更新时间",
        "Configuration updated",
        "datetime",
        R.config_capture_time,
        filterable=True,
    ),
    CommonColumn(
        "firstSeenAt", "首次发现", "First seen", "datetime", R.first_seen_at, filterable=True
    ),
    CommonColumn(
        "lastChangedAt",
        "最近变化",
        "Last changed",
        "datetime",
        R.last_changed_at,
        filterable=True,
    ),
    CommonColumn(
        "missingSince",
        "疑似删除时间",
        "Missing since",
        "datetime",
        R.missing_since,
        filterable=True,
    ),
    CommonColumn("deletedAt", "删除时间", "Deleted at", "datetime", R.deleted_at, filterable=True),
)
COMMON_BY_KEY = {column.key: column for column in COMMON_COLUMNS}

VALUE_LABELS: dict[str, dict[str, tuple[str, str]]] = {
    "status": {
        "active": ("正常", "Active"),
        "missing": ("可能已删除", "May be deleted"),
        "deleted": ("已删除", "Deleted"),
    },
    "accountType": {
        "dev": ("开发", "Dev"),
        "preprod": ("预发布", "Pre-prod"),
        "prod": ("生产", "Prod"),
    },
    "bool": {"true": ("是", "Yes"), "false": ("否", "No")},
}


def value_label(group: str, value: str, language: Language) -> str:
    pair = VALUE_LABELS[group].get(value)
    if pair is None:
        return value
    return pair[0] if language == "zh-CN" else pair[1]


# --------------------------------------------------------------------------- type columns in SQL


def _node(column: ColumnPath) -> Any:
    return R.configuration[column.path]  # jsonb #> path


def attr_expr(column: ColumnPath, *, raw_datetime: bool = False) -> ColumnElement[Any]:
    """The SQL value of a type column; mirrors `app.types.columns.column_values`.

    `raw_datetime` keeps datetimes as the text stored in the configuration (for output);
    otherwise they are parsed to timestamptz (for filtering and sorting).
    """
    node = _node(column)
    text = node.as_string()
    kind = func.jsonb_typeof(node)
    if column.exists:
        return node.is_not(None) & cast(node, Text).not_in(["null", '""', "[]", "{}"])
    if column.count:
        return case((kind == "array", func.jsonb_array_length(node)), else_=0)
    if column.type == "number":
        return case(
            (kind == "number", cast(text, Numeric)),
            (
                (kind == "string") & text.regexp_match(r"^-?[0-9]+(\.[0-9]+)?$"),
                cast(text, Numeric),
            ),
            else_=None,
        )
    if column.type == "bool":
        return case(
            (kind == "boolean", cast(text, Boolean)),
            (kind == "number", cast(text, Numeric) != 0),
            (
                kind == "string",
                case((func.lower(text) == "true", true()), (func.lower(text) == "false", false())),
            ),
            else_=None,
        )
    if column.type == "datetime" and not raw_datetime:
        return case(
            (text.regexp_match(r"^\d{4}-\d{2}-\d{2}T"), cast(text, TIMESTAMP(timezone=True))),
            else_=None,
        )
    return text


def attr_value(value: Any) -> Any:
    """A database value of a type column as JSON-ready data."""
    if hasattr(value, "as_integer_ratio") and not isinstance(value, float | int):
        return int(value) if value == int(value) else float(value)  # Decimal
    return value


def type_column_labels(columns: list[Column], language: Language) -> dict[str, str]:
    return {column.key: label_of(column.label, language) for column in columns}
