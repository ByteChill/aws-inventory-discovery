"""AWS JSON protocol helpers: error format, responses, SigV4 `Authorization` parsing."""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Response


class AwsError(Exception):
    """An error the AWS JSON protocols report as `{"__type": code, "message": ...}`."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def json_response(payload: dict[str, Any], json_version: str) -> Response:
    return Response(
        content=json.dumps(payload, separators=(",", ":")),
        media_type=f"application/x-amz-json-{json_version}",
        headers={"x-amzn-RequestId": str(uuid.uuid4())},
    )


def error_response(err: AwsError, json_version: str) -> Response:
    return Response(
        content=json.dumps({"__type": err.code, "message": err.message}),
        status_code=err.status,
        media_type=f"application/x-amz-json-{json_version}",
        headers={"x-amzn-RequestId": str(uuid.uuid4()), "x-amzn-ErrorType": err.code},
    )


_CREDENTIAL_RE = re.compile(
    r"Credential=(?P<key>[^/,\s]+)/(?P<date>\d{8})/(?P<region>[^/]+)/(?P<service>[^/]+)/aws4_request"
)


@dataclass(frozen=True)
class Caller:
    access_key_id: str
    region: str
    service: str


def parse_authorization(header: str | None) -> Caller:
    """Pull the access key id and credential scope out of a SigV4 `Authorization` header."""
    match = _CREDENTIAL_RE.search(header or "")
    if match is None:
        raise AwsError("MissingAuthenticationToken", "Missing Authentication Token", status=403)
    return Caller(match["key"], match["region"], match["service"])


def encode_token(state: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode()


def decode_token(token: str, error: AwsError) -> dict[str, Any]:
    try:
        state = json.loads(base64.urlsafe_b64decode(token.encode()))
    except ValueError:
        raise error from None
    if not isinstance(state, dict):
        raise error
    return state
