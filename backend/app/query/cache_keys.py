"""Stable cache keys for query results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection

from pydantic import BaseModel


def params_digest(params: BaseModel, *, exclude: Collection[str] = ()) -> str:
    """A SHA-256 of the parameters, independent of key order and of the order of repeated values.

    Values that mean "no filter" (None, an empty list, an empty string) are left out, and list
    values are deduplicated and sorted; neither changes the query they describe (the lists are
    ORed). `exclude` drops fields that do not change the result, such as paging or sort order.
    """
    dumped = params.model_dump(mode="json", by_alias=True, exclude=set(exclude))
    normalised = {
        name: sorted(set(value)) if isinstance(value, list) else value
        for name, value in dumped.items()
        if value not in (None, [], "")
    }
    return hashlib.sha256(json.dumps(normalised, sort_keys=True).encode()).hexdigest()
