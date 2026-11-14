"""AWS Config reads: recorder detection, advanced queries and by-ID status."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SELECT_FIELDS = (
    "arn",
    "resourceId",
    "resourceName",
    "resourceType",
    "awsRegion",
    "accountId",
    "configuration",
    "supplementaryConfiguration",
    "tags",
    "relationships",
    "configurationItemCaptureTime",
    "configurationItemStatus",
)
DELETED_STATUSES = frozenset({"ResourceDeleted", "ResourceDeletedNotRecorded"})
PAGE_SIZE = 100
EXCLUSION = "EXCLUSION_BY_RESOURCE_TYPES"


@dataclass(frozen=True)
class RecorderState:
    """What a region's Config recorder records (stored as `config_recorder` JSON).

    `dailyTypes` lists the types recorded once a day; `["*"]` means the recorder's default
    frequency is daily. `excludedTypes` is only used by exclusion-style recorders.
    """

    recording: bool = False
    all_supported: bool = False
    include_global: bool = False
    resource_types: tuple[str, ...] = ()
    excluded_types: tuple[str, ...] = ()
    daily_types: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "recording": self.recording,
            "allSupported": self.all_supported,
            "includeGlobal": self.include_global,
            "resourceTypes": list(self.resource_types),
            "excludedTypes": list(self.excluded_types),
            "dailyTypes": list(self.daily_types),
        }

    def covers(self, type_name: str, *, is_global: bool = False) -> bool:
        if not self.recording or (is_global and not self.include_global):
            return False
        if self.all_supported:
            return type_name not in self.excluded_types
        return type_name in self.resource_types

    def continuous(self, type_name: str, *, is_global: bool = False) -> bool:
        """Recorded, and not only once a day."""
        daily = "*" in self.daily_types or type_name in self.daily_types
        return self.covers(type_name, is_global=is_global) and not daily


def detect_recorder(client: Any) -> RecorderState:
    recorders = client.describe_configuration_recorders()["ConfigurationRecorders"]
    if not recorders:
        return RecorderState()
    recorder = recorders[0]
    statuses = client.describe_configuration_recorder_status()["ConfigurationRecordersStatus"]
    recording = any(s.get("recording") for s in statuses)
    group = recorder.get("recordingGroup", {})
    mode = recorder.get("recordingMode", {})
    exclusion = group.get("recordingStrategy", {}).get("useOnly") == EXCLUSION
    daily: list[str] = []
    if mode.get("recordingFrequency") == "DAILY":
        daily.append("*")
    for override in mode.get("recordingModeOverrides", []):
        if override.get("recordingFrequency") == "DAILY":
            daily.extend(override.get("resourceTypes", []))
    return RecorderState(
        recording=recording,
        all_supported=bool(group.get("allSupported")) or exclusion,
        include_global=bool(group.get("includeGlobalResourceTypes")),
        resource_types=tuple(group.get("resourceTypes", [])),
        excluded_types=tuple(group.get("exclusionByResourceTypes", {}).get("resourceTypes", [])),
        daily_types=tuple(daily),
    )


def capture_time(value: Any) -> datetime:
    """`configurationItemCaptureTime` as a UTC datetime (epoch seconds or ISO 8601).

    The message never quotes the raw value: it reaches the task's error_message and the log.
    """
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)
    if not isinstance(value, str):
        raise ValueError("configurationItemCaptureTime is not a number or a string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        raise ValueError("configurationItemCaptureTime is not an ISO 8601 timestamp") from None


def select_pages(client: Any, type_names: Sequence[str]) -> Iterator[list[dict[str, Any]]]:
    """Config items of the given types in the client's region, one list per page (100 each).

    A failed page raises; an iterator that ran to the end has returned everything.
    """
    quoted = ", ".join(f"'{name}'" for name in type_names)
    expression = f"SELECT {', '.join(SELECT_FIELDS)} WHERE resourceType IN ({quoted})"
    paginator = client.get_paginator("select_resource_config")
    for page in paginator.paginate(Expression=expression, Limit=PAGE_SIZE):
        yield [json.loads(result) for result in page["Results"]]


@dataclass(frozen=True)
class ConfigStatus:
    status: str
    captured_at: datetime

    @property
    def deleted(self) -> bool:
        return self.status in DELETED_STATUSES


def batch_get_status(
    client: Any, keys: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], ConfigStatus]:
    """`{(type, resource id): status}` from `BatchGetResourceConfig`.

    Keys Config never discovered, or did not process, are absent: that is no evidence either way.
    """
    pending = list(keys)
    found: dict[tuple[str, str], ConfigStatus] = {}
    for start in range(0, len(pending), 100):
        chunk = pending[start : start + 100]
        response = client.batch_get_resource_config(
            resourceKeys=[{"resourceType": t, "resourceId": i} for t, i in chunk]
        )
        for item in response["baseConfigurationItems"]:
            found[(item["resourceType"], item["resourceId"])] = ConfigStatus(
                item["configurationItemStatus"],
                item["configurationItemCaptureTime"].astimezone(UTC),
            )
    return found
