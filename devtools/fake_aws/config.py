"""AWS Config (AWS JSON 1.1, target prefix `StarlingDoveService`): SelectResourceConfig,
DescribeConfigurationRecorders, DescribeConfigurationRecorderStatus, BatchGetResourceConfig."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .awsjson import AwsError, decode_token, encode_token
from .store import CONFIG_ERRORS, Store

JSON_VERSION = "1.1"
TARGET_PREFIX = "StarlingDoveService"
MAX_LIMIT = 100
DELETED_STATUSES = ("ResourceDeleted", "ResourceDeletedNotRecorded")

# What the worker sends, and nothing more: SELECT <fields> WHERE resourceType IN ('A', 'B')
# (or resourceType = 'A').
_SELECT_RE = re.compile(
    r"^\s*SELECT\s+(?P<fields>.+?)\s+WHERE\s+resourceType\s*"
    r"(?:IN\s*\((?P<types>[^)]*)\)|=\s*(?P<one>'[^']*'))\s*$",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_RE = re.compile(r"^'([^']+)'$")
SELECTABLE_FIELDS = frozenset(
    {
        "arn",
        "resourceId",
        "resourceName",
        "resourceType",
        "awsRegion",
        "accountId",
        "availabilityZone",
        "resourceCreationTime",
        "configuration",
        "supplementaryConfiguration",
        "tags",
        "relationships",
        "configurationItemCaptureTime",
        "configurationItemStatus",
    }
)
_TIMESTAMP_KEYS = ("lastStartTime", "lastStopTime", "lastStatusChangeTime")


def parse_select(expression: str) -> tuple[list[str], list[str]]:
    """(selected fields, resource types) of a supported expression."""
    match = _SELECT_RE.match(expression or "")
    if match is None:
        raise AwsError(
            "InvalidExpressionException",
            "Unsupported expression: only SELECT <fields> WHERE resourceType IN ('...') "
            "or resourceType = '...' is supported by this fake.",
        )
    fields = [f.strip() for f in match["fields"].split(",")]
    unknown = [f for f in fields if f not in SELECTABLE_FIELDS]
    if unknown:
        raise AwsError("InvalidExpressionException", f"Unsupported field(s): {', '.join(unknown)}")
    raw_types = [match["one"]] if match["one"] else [t.strip() for t in match["types"].split(",")]
    types = []
    for raw in raw_types:
        quoted = _QUOTED_RE.match(raw)
        if quoted is None:
            raise AwsError("InvalidExpressionException", f"Invalid resource type literal: {raw}")
        types.append(quoted[1])
    return fields, types


def _raise_injected(
    store: Store, account: str, region: str, op: str, types: list[str], extra: str = ""
) -> None:
    error = store.take_error(account, region, op, types)
    if error is not None:
        code, status = CONFIG_ERRORS[error]
        raise AwsError(code, f"Injected {error} for {op}{extra}", status)


def _epoch(value: Any) -> Any:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return value


def select_resource_config(store: Store, account: str, region: str, body: dict[str, Any]) -> dict:
    fields, types = parse_select(body.get("Expression", ""))
    limit = body.get("Limit", MAX_LIMIT)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise AwsError("InvalidLimitException", f"Limit must be between 1 and {MAX_LIMIT}")
    offset = 0
    if body.get("NextToken"):
        invalid = AwsError("InvalidNextTokenException", "Invalid NextToken")
        state = decode_token(body["NextToken"], invalid)
        if state.get("e") != body.get("Expression") or not isinstance(state.get("o"), int):
            raise invalid
        offset = state["o"]
    _raise_injected(store, account, region, "SelectResourceConfig", types)

    with store.lock:
        items = sorted(
            (key, item)
            for key, item in store.config_items.get((account, region), {}).items()
            if key[0] in types and item.get("configurationItemStatus") not in DELETED_STATUSES
        )
    page = items[offset : offset + limit]
    response: dict[str, Any] = {
        "Results": [
            json.dumps({f: item[f] for f in fields if f in item}, separators=(",", ":"))
            for _, item in page
        ],
        "QueryInfo": {"SelectFields": [{"Name": f} for f in fields]},
    }
    if offset + limit < len(items):
        response["NextToken"] = encode_token({"e": body["Expression"], "o": offset + limit})
    return response


def _recorder_entries(
    store: Store, account: str, region: str, body: dict[str, Any]
) -> list[dict[str, Any]]:
    with store.lock:
        state = store.recorders.get((account, region)) or {}
        recorder = state.get("configuration_recorder")
        status = state.get("recorder_status") or {}
    if recorder is None:
        names = body.get("ConfigurationRecorderNames") or []
        if names:
            raise AwsError(
                "NoSuchConfigurationRecorderException",
                f"Cannot find configuration recorder with the specified name '{names[0]}'.",
            )
        return []
    names = body.get("ConfigurationRecorderNames")
    if names and recorder["name"] not in names:
        raise AwsError(
            "NoSuchConfigurationRecorderException",
            f"Cannot find configuration recorder with the specified name '{names[0]}'.",
        )
    return [{"recorder": recorder, "status": status}]


def describe_configuration_recorders(
    store: Store, account: str, region: str, body: dict[str, Any]
) -> dict:
    _raise_injected(store, account, region, "DescribeConfigurationRecorders", [])
    entries = _recorder_entries(store, account, region, body)
    return {"ConfigurationRecorders": [e["recorder"] for e in entries]}


def describe_configuration_recorder_status(
    store: Store, account: str, region: str, body: dict[str, Any]
) -> dict:
    _raise_injected(store, account, region, "DescribeConfigurationRecorderStatus", [])
    result = []
    for entry in _recorder_entries(store, account, region, body):
        status = {"name": entry["recorder"]["name"], **entry["status"]}
        for key in _TIMESTAMP_KEYS:
            if key in status:
                status[key] = _epoch(status[key])
        result.append(status)
    return {"ConfigurationRecordersStatus": result}


def _base_item(item: dict[str, Any]) -> dict[str, Any]:
    supplementary = {
        k: v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
        for k, v in (item.get("supplementaryConfiguration") or {}).items()
    }
    base: dict[str, Any] = {
        "version": "1.3",
        "accountId": item["accountId"],
        "configurationItemCaptureTime": _epoch(item["configurationItemCaptureTime"]),
        "configurationItemStatus": item["configurationItemStatus"],
        "configurationStateId": str(int(_epoch(item["configurationItemCaptureTime"]) * 1000)),
        "arn": item.get("arn"),
        "resourceType": item["resourceType"],
        "resourceId": item["resourceId"],
        "awsRegion": item["awsRegion"],
        "configuration": json.dumps(item.get("configuration", {}), separators=(",", ":")),
        "supplementaryConfiguration": supplementary,
    }
    if item.get("resourceName"):
        base["resourceName"] = item["resourceName"]
    if item.get("availabilityZone"):
        base["availabilityZone"] = item["availabilityZone"]
    if item.get("resourceCreationTime"):
        base["resourceCreationTime"] = _epoch(item["resourceCreationTime"])
    return base


def batch_get_resource_config(
    store: Store, account: str, region: str, body: dict[str, Any]
) -> dict:
    keys = body.get("resourceKeys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 100:
        raise AwsError("ValidationException", "resourceKeys must contain 1 to 100 items")
    types = sorted({k.get("resourceType", "") for k in keys})
    _raise_injected(store, account, region, "BatchGetResourceConfig", types)
    found = []
    with store.lock:
        items = store.config_items.get((account, region), {})
        for key in keys:
            item = items.get((key.get("resourceType"), key.get("resourceId")))
            if item is not None:
                found.append(_base_item(item))
    # Resources Config never discovered are simply absent from the response.
    return {"baseConfigurationItems": found, "unprocessedResourceKeys": []}


OPERATIONS = {
    "SelectResourceConfig": select_resource_config,
    "DescribeConfigurationRecorders": describe_configuration_recorders,
    "DescribeConfigurationRecorderStatus": describe_configuration_recorder_status,
    "BatchGetResourceConfig": batch_get_resource_config,
}
