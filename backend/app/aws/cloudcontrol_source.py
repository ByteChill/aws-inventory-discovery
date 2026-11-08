"""Cloud Control reads. Only `ResourceNotFoundException` on a by-ID read means "gone"."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from botocore.exceptions import ClientError

from app.types.hooks import GONE, Gone, Properties

PAGE_SIZE = 100


def list_resource_pages(
    client: Any, type_name: str, model: dict[str, Any] | None = None
) -> Iterator[list[tuple[str, Properties]]]:
    """`(identifier, properties)` of every resource of the type, one list per page.

    `model` is the optional `ResourceModel` for types whose list needs a parent property.
    A failed page raises, so a caller that finished iterating has seen everything.
    """
    arguments: dict[str, Any] = {"TypeName": type_name}
    if model is not None:
        arguments["ResourceModel"] = json.dumps(model)
    paginator = client.get_paginator("list_resources")
    for page in paginator.paginate(PaginationConfig={"PageSize": PAGE_SIZE}, **arguments):
        yield [
            (description["Identifier"], json.loads(description["Properties"]))
            for description in page["ResourceDescriptions"]
        ]


def get_resource(client: Any, type_name: str, identifier: str) -> Properties | Gone:
    """The resource's properties, `GONE` for ResourceNotFoundException, otherwise raise."""
    try:
        response = client.get_resource(TypeName=type_name, Identifier=identifier)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return GONE
        raise
    return json.loads(response["ResourceDescription"]["Properties"])
