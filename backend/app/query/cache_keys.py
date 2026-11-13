"""Stable cache keys for query results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection

from pydantic import BaseModel


def params_digest(params: BaseModel, *, exclude: Collection[str] = ()) -> str:
    """A SHA-256 of the parameters, independent of key order and of the order of repeated values.

    Values that mean "no filter" (None, an empty list) are left out, and list values are
    deduplicated and sorted; neither changes the query they describe (the lists are ORed).
    An empty string stays: `attr.<key>=` filters on the empty value, it is not an absent
    filter. `exclude` drops fields that do not change the result, such as paging or sort order.
    """
    dumped = params.model_dump(mode="json", by_alias=True, exclude=set(exclude))
    normalised = {
        name: sorted(set(value)) if isinstance(value, list) else value
        for name, value in dumped.items()
        if value is not None and value != []
    }
    return hashlib.sha256(json.dumps(normalised, sort_keys=True).encode()).hexdigest()
