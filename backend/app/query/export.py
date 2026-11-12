"""Streaming CSV and JSON Lines export (api.md `GET /resources/export`)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Row
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.query.columns import (
    COMMON_BY_KEY,
    COMMON_COLUMNS,
    Language,
    attr_expr,
    attr_value,
    label_of,
)
from app.query.filters import DEFAULT_SORT, ResourceFilter, order_by, single_spec, where
from app.query.resources import detail_select, item_select, to_detail
from app.types.columns import column_paths
from app.types.registry import TypeRegistry

BATCH_SIZE = 1000
BOM = "﻿"

# Common column key -> the result column that holds its value.
COMMON_FIELD = {
    "resourceName": "resource_name",
    "resourceType": "resource_type",
    "resourceId": "resource_id",
    "arn": "arn",
    "accountId": "account_id",
    "accountName": "account_name",
    "eimId": "eim_id",
    "eimApp": "eim_app",
    "baId": "ba_id",
    "accountType": "account_type",
    "awsRegion": "aws_region",
    "status": "status",
    "tags": "tags",
    "configurationItemCaptureTime": "config_capture_time",
    "firstSeenAt": "first_seen_at",
    "lastChangedAt": "last_changed_at",
    "missingSince": "missing_since",
    "deletedAt": "deleted_at",
}


class ExportParams(ResourceFilter):
    format: Literal["csv", "jsonl"] = "csv"
    columns: str | None = None  # comma-separated column keys (CSV)
    sort: str | None = None
    lang: Language | None = None  # overrides Accept-Language for the header labels


@dataclass(frozen=True)
class Export:
    media_type: str
    filename: str
    body: Iterator[str]


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return _timestamp(value)
    text = str(value)
    # A cell that starts like a formula would be executed by spreadsheet programs.
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _common_cell(key: str, mapping: Any) -> str:
    value = mapping[COMMON_FIELD[key]]
    if key == "tags":
        return _cell(";".join(f"{tag['key']}={tag['value']}" for tag in value))
    return _cell(value)


def _csv_chunks(
    rows: Iterator[list[Row[Any]]], header: list[str], keys: list[tuple[str, int | None]]
):
    """`keys` are (common key, None) or ("", attr index)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    yield BOM + buffer.getvalue()
    for batch in rows:
        buffer.seek(0)
        buffer.truncate()
        for row in batch:
            mapping = row._mapping
            writer.writerow(
                _common_cell(key, mapping)
                if index is None
                else _cell(attr_value(mapping[f"attr_{index}"]))
                for key, index in keys
            )
        yield buffer.getvalue()


def _resolve_columns(requested: str | None, spec_keys: list[str] | None) -> list[str]:
    available = [column.key for column in COMMON_COLUMNS] + [
        f"attr.{key}" for key in spec_keys or []
    ]
    if requested is None:
        return available
    chosen = [key.strip() for key in requested.split(",") if key.strip()]
    for key in chosen:
        bare = key if key in COMMON_BY_KEY else f"attr.{key.removeprefix('attr.')}"
        if bare not in available:
            raise ApiError(422, "validation_error", f"未知的导出列 {key}", {"columns": key})
    return [key if key in COMMON_BY_KEY else f"attr.{key.removeprefix('attr.')}" for key in chosen]


def export(
    session: Session,
    registry: TypeRegistry,
    params: ExportParams,
    language: Language,
    *,
    task_retention: timedelta,
) -> Export:
    condition = where(params, registry, task_retention=task_retention)
    ordering = order_by(params.sort or DEFAULT_SORT, params, registry)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if params.format == "jsonl":
        statement = (
            detail_select(live=False)
            .where(condition)
            .order_by(*ordering)
            .execution_options(yield_per=BATCH_SIZE)
        )
        return Export(
            "application/x-ndjson",
            f"aws-inventory-{stamp}.jsonl",
            _jsonl_chunks(session, registry, statement),
        )

    spec = single_spec(params, registry)
    keys = _resolve_columns(params.columns, [c.key for c in spec.columns] if spec else None)
    labels = {c.key: c.label(language) for c in COMMON_COLUMNS}
    attr_labels = (
        {f"attr.{c.key}": label_of(c.label, language) for c in spec.columns} if spec else {}
    )
    attr_columns = []
    layout: list[tuple[str, int | None]] = []
    for key in keys:
        if key.startswith("attr.") and key not in COMMON_BY_KEY:
            assert spec is not None
            expr = attr_expr(column_paths(spec)[key.removeprefix("attr.")], raw_datetime=True)
            layout.append(("", len(attr_columns)))
            attr_columns.append(expr.label(f"attr_{len(attr_columns)}"))
        else:
            layout.append((key, None))
    statement = (
        item_select(attr_columns)
        .where(condition)
        .order_by(*ordering)
        .execution_options(yield_per=BATCH_SIZE)
    )
    header = [labels.get(key) or attr_labels[key] for key in keys]
    batches = (list(batch) for batch in session.execute(statement).partitions())
    return Export(
        "text/csv; charset=utf-8",
        f"aws-inventory-{stamp}.csv",
        _csv_chunks(batches, header, layout),
    )


def _jsonl_chunks(session: Session, registry: TypeRegistry, statement: Any) -> Iterator[str]:
    for batch in session.execute(statement).partitions():
        yield "".join(
            to_detail(row, registry, live=False).model_dump_json(by_alias=True, exclude={"live"})
            + "\n"
            for row in batch
        )
