"""Redaction (resource-types.md section 3). Pure: every function returns a new dict.

Rules name Config-format paths. For raw Cloud Control properties each key is first mapped
to its Config-format path with the same function the conversion uses, so one rule covers
both representations.
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable
from typing import Any

from app.types.mapping import Path, config_path_of, split_path
from app.types.spec import RedactRule, ResourceTypeSpec

MASK = "***"


def redact_configuration(spec: ResourceTypeSpec, configuration: dict[str, Any]) -> dict[str, Any]:
    return _redact(spec.redact, configuration, lambda path: path)


def redact_live_raw(spec: ResourceTypeSpec, properties: dict[str, Any]) -> dict[str, Any]:
    return _redact(spec.redact, properties, lambda path: config_path_of(spec, path))


def _mask(value: Any) -> Any:
    if isinstance(value, dict):
        return dict.fromkeys(value, MASK)
    if isinstance(value, list):
        return [MASK] * len(value)
    return MASK if value is not None else None


def _summarize(
    items: Any, by: str, path: Path, to_config: Callable[[Path], Path]
) -> dict[str, int] | Any:
    if not isinstance(items, list):
        return copy.deepcopy(items)  # already a summary
    wanted = (*to_config(path), by)
    counts: Counter[str] = Counter()
    for item in items:
        group = next(
            (str(value) for key, value in item.items() if to_config((*path, key)) == wanted),
            "other",
        )
        counts[group] += 1
    return dict(counts)


def _redact(
    rules: list[RedactRule], data: dict[str, Any], to_config: Callable[[Path], Path]
) -> dict[str, Any]:
    by_path = {split_path(rule.path): rule for rule in rules}

    def visit(node: Any, path: Path) -> Any:
        if isinstance(node, list):
            return [visit(item, path) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            child = (*path, key)
            rule = by_path.get(to_config(child))
            if rule is None:
                out[key] = visit(value, child)
            elif rule.mode == "values":
                out[key] = _mask(value)
            elif rule.mode == "summarize":
                assert rule.by is not None
                out[key] = _summarize(value, rule.by, child, to_config)
            # mode "drop": leave the key out
        return out

    return visit(data, ())
