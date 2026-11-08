"""Cloud Control API (AWS JSON 1.0, target prefix `CloudApiService`): ListResources, GetResource."""

from __future__ import annotations

import json
from typing import Any

from .awsjson import AwsError, decode_token, encode_token
from .store import CLOUDCONTROL_ERRORS, Store

JSON_VERSION = "1.0"
TARGET_PREFIX = "CloudApiService"
DEFAULT_MAX_RESULTS = 100


def _description(identifier: str, properties: dict[str, Any]) -> dict[str, str]:
    # Properties is a JSON document inside a JSON string, as in the real API.
    return {"Identifier": identifier, "Properties": json.dumps(properties, separators=(",", ":"))}


def _raise_injected(store: Store, account: str, region: str, op: str, type_name: str) -> None:
    error = store.take_error(account, region, op, [type_name])
    if error is not None:
        code, status = CLOUDCONTROL_ERRORS[error]
        raise AwsError(code, f"Injected {error} for {op} on {type_name}", status)


def _type_name(body: dict[str, Any]) -> str:
    type_name = body.get("TypeName")
    if not isinstance(type_name, str) or not type_name:
        raise AwsError("ValidationException", "TypeName is required")
    return type_name


def list_resources(store: Store, account: str, region: str, body: dict[str, Any]) -> dict:
    type_name = _type_name(body)
    max_results = body.get("MaxResults", DEFAULT_MAX_RESULTS)
    if not isinstance(max_results, int) or not 1 <= max_results <= 100:
        raise AwsError("InvalidRequestException", "MaxResults must be between 1 and 100")
    offset = 0
    if body.get("NextToken"):
        state = decode_token(
            body["NextToken"], AwsError("InvalidRequestException", "Invalid NextToken")
        )
        if state.get("t") != type_name or not isinstance(state.get("o"), int):
            raise AwsError("InvalidRequestException", "Invalid NextToken")
        offset = state["o"]
    _raise_injected(store, account, region, "ListResources", type_name)

    with store.lock:
        items = sorted(
            (ident, props)
            for (t, ident), props in store.resources.get((account, region), {}).items()
            if t == type_name
        )
    page = items[offset : offset + max_results]
    response: dict[str, Any] = {
        "TypeName": type_name,
        "ResourceDescriptions": [_description(i, p) for i, p in page],
    }
    if offset + max_results < len(items):
        response["NextToken"] = encode_token({"t": type_name, "o": offset + max_results})
    return response


def get_resource(store: Store, account: str, region: str, body: dict[str, Any]) -> dict:
    type_name = _type_name(body)
    identifier = body.get("Identifier")
    if not isinstance(identifier, str) or not identifier:
        raise AwsError("ValidationException", "Identifier is required")
    _raise_injected(store, account, region, "GetResource", type_name)
    with store.lock:
        props = store.resources.get((account, region), {}).get((type_name, identifier))
    if props is None:
        raise AwsError(
            "ResourceNotFoundException",
            f"Resource of type '{type_name}' with identifier '{identifier}' was not found.",
            status=404,
        )
    return {"TypeName": type_name, "ResourceDescription": _description(identifier, props)}


OPERATIONS = {"ListResources": list_resources, "GetResource": get_resource}
