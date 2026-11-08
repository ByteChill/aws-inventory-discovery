"""Cloud Control (and native API) data -> Config-format records, and the overlay rule.

Everything here is a pure function. Overlay runs under a DB lock elsewhere.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.types.spec import ResourceTypeSpec

Path = tuple[str, ...]
MISSING: Any = object()


class MappingError(ValueError):
    """The data cannot be mapped the way the spec says."""


# ---------------------------------------------------------------------------
# Built-in value transforms (resource-types.md 2.3). Add new ones here, explicitly.
# ---------------------------------------------------------------------------


def to_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError(f"not a boolean: {value!r}")


def to_iso_datetime(value: Any) -> str:
    if isinstance(value, int | float) and not isinstance(value, bool):
        moment = datetime.fromtimestamp(value, UTC)
    elif isinstance(value, str):
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    else:
        raise ValueError(f"not a timestamp: {value!r}")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "toString": to_string,
    "toInt": lambda value: int(float(value)) if isinstance(value, str) else int(value),
    "toFloat": float,
    "toBool": to_bool,
    "toIsoDatetime": to_iso_datetime,
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def split_path(path: str) -> Path:
    """`a.b[].c` -> ("a", "b", "c"). Lists are transparent when matching paths."""
    return tuple(segment.removesuffix("[]") for segment in path.split("."))


def get_path(data: Any, path: Path) -> Any:
    """The value at `path` in nested dicts, or MISSING."""
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return MISSING
        data = data[key]
    return data


def _set_path(data: dict[str, Any], path: Path, value: Any) -> None:
    for key in path[:-1]:
        child = data.setdefault(key, {})
        if not isinstance(child, dict):
            raise MappingError(f"cannot set {'.'.join(path)}: {key} is not an object")
        data = child
    data[path[-1]] = value


def _delete_path(data: dict[str, Any], path: Path) -> None:
    parent = get_path(data, path[:-1]) if len(path) > 1 else data
    if isinstance(parent, dict):
        parent.pop(path[-1], None)


# ---------------------------------------------------------------------------
# Key transformation
# ---------------------------------------------------------------------------


def camel_key(key: str) -> str:
    """Lower the leading run of capitals; keep the last one if a lowercase follows it.

    VpcId -> vpcId, VPCId -> vpcId, DNSName -> dnsName, Arn -> arn, ID -> id.
    """
    run = re.match(r"[A-Z]+", key)
    if run is None:
        return key
    head, rest = run.group(), key[run.end() :]
    if len(head) > 1 and rest[:1].islower():
        return head[:-1].lower() + head[-1] + rest
    return head.lower() + rest


def _convert_key(spec: ResourceTypeSpec, parent: Path, key: str) -> str:
    mapping = spec.mapping
    if mapping.key_case == "pascal":
        return key
    preserved = {split_path(p) for p in mapping.preserve_keys_under}
    if any(parent[:n] in preserved for n in range(1, len(parent) + 1)):
        return key
    return camel_key(key)


def config_path_of(spec: ResourceTypeSpec, cc_path: Path) -> Path:
    """Where a Cloud Control property path lands in the Config-format configuration."""
    renames = {split_path(src): split_path(dest) for src, dest in spec.mapping.rename.items()}
    for length in range(len(cc_path), 0, -1):
        dest = renames.get(cc_path[:length])
        if dest is not None:
            rest = tuple(
                _convert_key(spec, cc_path[:i], cc_path[i]) for i in range(length, len(cc_path))
            )
            return dest + rest
    return tuple(_convert_key(spec, cc_path[:i], cc_path[i]) for i in range(len(cc_path)))


# ---------------------------------------------------------------------------
# properties -> Config-format record
# ---------------------------------------------------------------------------


class ConfigTag(BaseModel):
    key: str
    value: str


class ConfigItem(BaseModel):
    """A Config-format record without the parts the Writer owns (relationships, capture time)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    arn: str | None
    resource_id: str
    resource_name: str | None
    resource_type: str
    aws_region: str
    account_id: str
    configuration: dict[str, Any]
    tags: list[ConfigTag] = Field(default_factory=list)


def convert_configuration(spec: ResourceTypeSpec, properties: dict[str, Any]) -> dict[str, Any]:
    """Steps 1-4 of resource-types.md 2.2 (default rule, preserve, rename/exclude, transform)."""
    mapping = spec.mapping
    excluded = {split_path(p) for p in mapping.exclude}
    renames = {split_path(src): split_path(dest) for src, dest in mapping.rename.items()}
    deposits: list[tuple[Path, Any]] = []

    def walk(value: Any, path: Path, in_list: bool) -> Any:
        if isinstance(value, list):
            return [walk(item, path, True) for item in value]
        if not isinstance(value, dict):
            return value
        out: dict[str, Any] = {}
        for key, child in value.items():
            child_path = (*path, key)
            if child_path in excluded or child_path == ("Tags",):
                continue
            dest = renames.get(child_path)
            if dest is not None:
                if in_list:
                    raise MappingError(f"cannot rename {'.'.join(child_path)} inside an array")
                deposits.append((dest, walk(child, child_path, False)))
                continue
            out[_convert_key(spec, path, key)] = walk(child, child_path, in_list)
        return out

    result = walk(properties, (), False)
    for dest, value in deposits:
        _set_path(result, dest, value)
    for path, name in mapping.transform.items():
        _transform_at(result, path.split("."), TRANSFORMS[name], path)
    return result


def _transform_at(node: Any, segments: list[str], fn: Callable[[Any], Any], label: str) -> None:
    if isinstance(node, list):
        for item in node:
            _transform_at(item, segments, fn, label)
        return
    if not isinstance(node, dict):
        return
    key = segments[0].removesuffix("[]")
    if key not in node:
        return
    if len(segments) > 1:
        _transform_at(node[key], segments[1:], fn, label)
        return
    try:
        node[key] = [fn(v) for v in node[key]] if isinstance(node[key], list) else fn(node[key])
    except (ValueError, TypeError) as exc:
        raise MappingError(f"transform of {label} failed: {exc}") from exc


def convert_tags(properties: dict[str, Any]) -> list[ConfigTag]:
    return [ConfigTag(key=tag["Key"], value=tag["Value"]) for tag in properties.get("Tags") or []]


def partition_of(region: str) -> str:
    if region.startswith("cn-"):
        return "aws-cn"
    if region.startswith("us-gov-"):
        return "aws-us-gov"
    return "aws"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _resource_name(
    spec: ResourceTypeSpec, configuration: dict[str, Any], tags: list[ConfigTag]
) -> str | None:
    source = spec.identity.name_from
    if source is None:
        return None
    if source.startswith("tag:"):
        return next((tag.value for tag in tags if tag.key == source[4:]), None)
    if source.startswith("tail:"):
        value = _text(get_path(configuration, split_path(source[5:])))
        return re.split(r"[:/]", value)[-1] if value else None
    return _text(get_path(configuration, split_path(source)))


def to_config_item(
    spec: ResourceTypeSpec, properties: dict[str, Any], *, account_id: str, region: str
) -> ConfigItem:
    """Map one Cloud Control `Properties` object to a Config-format record (no overlay)."""
    configuration = convert_configuration(spec, properties)
    tags = convert_tags(properties)
    identity = spec.identity
    resource_id = _text(get_path(configuration, split_path(identity.resource_id_from)))
    if resource_id is None:
        raise MappingError(f"{spec.type_name}: no {identity.resource_id_from} in the data")
    if identity.arn_from is not None:
        arn = _text(get_path(configuration, split_path(identity.arn_from)))
    else:
        assert identity.arn_template is not None
        arn = identity.arn_template.format(
            partition=partition_of(region), region=region, account=account_id, id=resource_id
        )
    return ConfigItem(
        arn=arn,
        resource_id=resource_id,
        resource_name=_resource_name(spec, configuration, tags),
        resource_type=spec.type_name,
        aws_region="global" if spec.global_ else region,
        account_id=account_id,
        configuration=configuration,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Cloud Control Identifier <-> Config-format fields
# ---------------------------------------------------------------------------


def cloudcontrol_identifier(spec: ResourceTypeSpec, configuration: dict[str, Any]) -> str:
    """The Cloud Control Identifier of a resource; composite identifiers join with `|`."""
    parts = []
    for field in spec.identity.identifier_fields:
        value = get_path(configuration, split_path(field))
        if value is MISSING:
            raise MappingError(f"{spec.type_name}: no {field} to build the identifier from")
        parts.append(str(value))
    return "|".join(parts)


def split_identifier(spec: ResourceTypeSpec, identifier: str) -> dict[str, str]:
    """A Cloud Control Identifier as {identifier field: value}."""
    fields = spec.identity.identifier_fields
    values = identifier.split("|")
    if len(values) != len(fields):
        raise MappingError(f"{spec.type_name}: identifier {identifier!r} needs {len(fields)} parts")
    return dict(zip(fields, values, strict=True))


# ---------------------------------------------------------------------------
# Overlay (resource-types.md 2.2 step 5)
# ---------------------------------------------------------------------------


def covered_paths(spec: ResourceTypeSpec) -> frozenset[Path]:
    """Configuration paths the mapping can produce. Overlay and conversion share this."""
    mapping = spec.mapping
    excluded = {split_path(p) for p in mapping.exclude}

    def is_excluded(cc_path: Path) -> bool:
        return any(cc_path[:n] in excluded for n in range(1, len(cc_path) + 1))

    covered: set[Path] = set()
    for prop in mapping.properties:
        cc_path = split_path(prop)
        if not is_excluded(cc_path):
            covered.add(config_path_of(spec, cc_path))
    for src, dest in mapping.rename.items():
        if not is_excluded(split_path(src)):
            covered.add(split_path(dest))
    for path in mapping.transform:
        segments = path.split(".")
        cut = next((i + 1 for i, s in enumerate(segments) if s.endswith("[]")), len(segments))
        covered.add(split_path(".".join(segments[:cut])))
    return frozenset(covered)


def overlay(
    existing_configuration: dict[str, Any],
    new_configuration: dict[str, Any],
    covered: Iterable[Path],
) -> dict[str, Any]:
    """Covered fields follow the new data (absent means cleared); the rest keep their value."""
    result = copy.deepcopy(existing_configuration)
    covered = sorted(covered)
    for path in covered:
        value = get_path(new_configuration, path)
        if value is MISSING:
            _delete_path(result, path)
        else:
            _set_path(result, path, copy.deepcopy(value))
    touched = {path[0] for path in covered}
    for key, value in new_configuration.items():
        if key not in touched:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# AMI (native API, resource-types.md 4.1)
# ---------------------------------------------------------------------------


def ami_to_config_item(
    spec: ResourceTypeSpec, image: dict[str, Any], *, account_id: str, region: str
) -> ConfigItem:
    """Map one `DescribeImages` image and add `ownership` (self or shared)."""
    ownership = "self" if image["OwnerId"] == account_id else "shared"
    return to_config_item(
        spec, {**image, "Ownership": ownership}, account_id=account_id, region=region
    )


def ami_gone(response: dict[str, Any]) -> bool:
    """A by-ID `DescribeImages` answer that says the AMI no longer exists."""
    return all(image.get("State") == "deregistered" for image in response["Images"])
