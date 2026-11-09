"""Type-specific list columns: values from a Config-format configuration, and their JSON paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.types.mapping import MISSING, Path, get_path, split_path
from app.types.spec import Column, ResourceTypeSpec


@dataclass(frozen=True)
class ColumnPath:
    """What the query layer needs to filter, sort or aggregate a column in SQL."""

    key: str
    type: str
    path: Path  # JSON path inside `configuration`
    exists: bool
    count: bool


def column_paths(spec: ResourceTypeSpec) -> dict[str, ColumnPath]:
    return {
        column.key: ColumnPath(
            key=column.key,
            type=column.type,
            path=split_path(column.path),
            exists=column.exists,
            count=column.count,
        )
        for column in spec.columns
    }


def _present(value: Any) -> bool:
    return (
        value is not MISSING and value is not None and value != "" and value != [] and value != {}
    )


def _coerce(value: Any, column_type: str) -> Any:
    if value is MISSING or value is None:
        return None
    if column_type == "bool":
        if isinstance(value, str):
            return {"true": True, "false": False}.get(value.lower())
        return bool(value)
    if column_type == "number":
        try:
            return value if isinstance(value, int | float) else float(value)
        except TypeError, ValueError:
            return None
    return value if isinstance(value, str) else str(value)


def _value(column: Column, configuration: dict[str, Any]) -> Any:
    raw = get_path(configuration, split_path(column.path))
    if column.exists:
        return _present(raw)
    if column.count:
        return len(raw) if isinstance(raw, list) else 0
    return _coerce(raw, column.type)


def column_values(spec: ResourceTypeSpec, configuration: dict[str, Any]) -> dict[str, Any]:
    """The `attrs` of a resource: one value per defined column, None where there is no data."""
    return {column.key: _value(column, configuration) for column in spec.columns}
