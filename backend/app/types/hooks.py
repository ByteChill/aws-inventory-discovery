"""Per-type code for the few things a spec file cannot express (resource-types.md section 4).

The hook functions arrive in a later milestone. The signatures below are the contract
they implement: a hook only reads and enriches data. It never touches the database,
redacts, or decides deletion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from boto3.session import Session

Properties = dict[str, Any]


class ListLive(Protocol):
    def __call__(self, session: Session, *, account_id: str, region: str) -> list[Properties]:
        """List every resource. Raise on any failed page or parent so a list is never partial."""
        ...


class GetLive(Protocol):
    def __call__(
        self, session: Session, *, account_id: str, region: str, resource_id: str
    ) -> Properties | None:
        """Return the resource, or None when it is explicitly gone. Raise on anything else."""
        ...


class Filter(Protocol):
    def __call__(self, items: list[Properties]) -> list[Properties]: ...


class Enrich(Protocol):
    def __call__(
        self, session: Session, *, account_id: str, region: str, item: Properties
    ) -> Properties: ...


@dataclass(frozen=True)
class Hooks:
    list_live: ListLive | None = None  # default: Cloud Control ListResources
    get_live: GetLive | None = None  # default: Cloud Control GetResource
    filter: Filter | None = None  # default: no filtering
    enrich: Enrich | None = None  # default: no enrichment


TYPE_HOOKS: dict[str, Hooks] = {}
