"""Config relationships plus the type's explicitly declared derived relationships.

Config's `relationships` are authoritative. A type may also declare references in
`derivedRelationships`; they are recomputed from the current `configuration` on every write,
so a Cloud Control observation cannot leave an old derived edge behind.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.types.spec import ResourceTypeSpec

DERIVED = "derived"
RESOURCE_ID = "resourceId"


def is_derived(relationship: dict[str, Any]) -> bool:
    return relationship.get("origin") == DERIVED


def values_at_path(data: Any, path: str) -> list[str]:
    """All non-empty strings at a path. `[]` fans out over every element of an array."""
    found: list[str] = []

    def walk(node: Any, segments: list[str]) -> None:
        if not segments:
            if isinstance(node, str) and node:
                found.append(node)
            return
        segment, rest = segments[0], segments[1:]
        key = segment.removesuffix("[]")
        if not isinstance(node, dict) or key not in node:
            return
        child = node[key]
        if not segment.endswith("[]"):
            walk(child, rest)
            return
        if not isinstance(child, list):
            return
        for item in child:
            walk(item, rest)

    walk(data, path.split("."))
    return found


def derive_relationships(
    spec: ResourceTypeSpec, configuration: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The edges declared by `derivedRelationships`, from the current configuration."""
    derived: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for rule in spec.derived_relationships:
        for value in values_at_path(configuration, rule.from_path):
            key = (rule.to_type, rule.to_field, value)
            if key in seen:
                continue
            seen.add(key)
            relationship = {
                "resourceType": rule.to_type,
                "resourceId": value,
                "relationshipName": rule.name,
                "origin": DERIVED,
            }
            if rule.to_field != RESOURCE_ID:
                relationship["toField"] = rule.to_field
            derived.append(relationship)
    return derived


def target_key(relationship: dict[str, Any]) -> tuple[str, str, str]:
    return (
        relationship["resourceType"],
        relationship.get("toField", RESOURCE_ID),
        relationship["resourceId"],
    )


def merge_relationships(
    config_relationships: Iterable[dict[str, Any]],
    derived_relationships: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Config edges first; a derived edge to the same target is not added twice."""
    merged = [dict(relationship) for relationship in config_relationships]
    seen = {target_key(item): item for item in merged}
    for relationship in derived_relationships:
        key = target_key(relationship)
        existing = seen.get(key)
        if existing is not None:
            if not existing.get("relationshipName") and relationship.get("relationshipName"):
                existing["relationshipName"] = relationship["relationshipName"]
            continue
        copy = dict(relationship)
        seen[key] = copy
        merged.append(copy)
    return merged


def effective_relationships(
    spec: ResourceTypeSpec,
    configuration: dict[str, Any] | None,
    existing: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Config relationships from `existing` (Config observations or the stored row), plus
    derived relationships computed from `configuration`. Existing derived edges are ignored,
    which is what removes them when their source field disappears."""
    config_relationships = [dict(item) for item in existing if not is_derived(item)]
    return merge_relationships(config_relationships, derive_relationships(spec, configuration))
