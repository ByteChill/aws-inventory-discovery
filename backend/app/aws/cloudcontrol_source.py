"""Cloud Control reads. Only `ResourceNotFoundException` on a by-ID read means "gone"."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from botocore.exceptions import ClientError

from app.types.hooks import GONE, Gone, Properties


def list_resources(
    client: Any, type_name: str, model: dict[str, Any] | None = None
) -> Iterator[tuple[str, Properties]]:
    """`(identifier, properties)` for every resource of the type, page by page.

    `model` is the optional `ResourceModel` for types whose list needs a parent property.
    A failed page raises, so a caller that finished iterating has seen everything.
    """
    arguments: dict[str, Any] = {"TypeName": type_name}
    if model is not None:
        arguments["ResourceModel"] = json.dumps(model)
    paginator = client.get_paginator("list_resources")
    for page in paginator.paginate(PaginationConfig={"PageSize": 100}, **arguments):
        for description in page["ResourceDescriptions"]:
            yield description["Identifier"], json.loads(description["Properties"])


def get_resource(client: Any, type_name: str, identifier: str) -> Properties | Gone:
    """The resource's properties, `GONE` for ResourceNotFoundException, otherwise raise."""
    try:
        response = client.get_resource(TypeName=type_name, Identifier=identifier)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return GONE
        raise
    return json.loads(response["ResourceDescription"]["Properties"])
