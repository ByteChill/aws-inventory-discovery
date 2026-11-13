from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.accounts import AccountRef
from app.schemas.base import ApiModel

ResourceStatus = Literal["active", "missing", "deleted"]


class Tag(ApiModel):
    key: str
    value: str


class ResourceItem(ApiModel):
    id: int
    arn: str
    account_id: str
    aws_region: str
    resource_type: str
    resource_id: str
    resource_name: str | None
    tags: list[Tag]
    account: AccountRef
    attrs: dict[str, Any]  # the type's own columns; {} unless a single resourceType is given
    relationship_count: int
    status: ResourceStatus
    stale: bool  # its scope has not been scanned successfully for 12 hours
    configuration_item_capture_time: datetime | None
    first_seen_at: datetime
    last_changed_at: datetime
    missing_since: datetime | None
    deleted_at: datetime | None


class RelationshipRef(ApiModel):
    resource_type: str
    resource_id: str
    name: str


class LiveData(ApiModel):
    properties: dict[str, Any]
    observed_at: datetime


class ResourceDetail(ResourceItem):
    configuration: dict[str, Any] | None
    relationships: list[RelationshipRef]
    live: LiveData | None
    observed_at: datetime
    observed_source: Literal["config", "cloudcontrol", "native"]


class RelationshipEntry(ApiModel):
    direction: Literal["out", "in"]
    relation_name: str
    resource_type: str
    resource_key: str
    resource: ResourceItem | None  # None: the database has no such resource


class Column(ApiModel):
    key: str
    label: str
    type: Literal["text", "enum", "number", "bool", "datetime"]
    mono: bool
    sortable: bool
    filterable: bool
    scope: Literal["common", "type"]


class FacetBucket(ApiModel):
    value: str
    label: str
    count: int


class ResourceSummary(ApiModel):
    """Distinct counts under a filter, computed in SQL (facets cap their buckets at 200)."""

    resources: int
    accounts: int
    applications: int
    resource_types: int
    regions: int


class ResourceTypeInfo(ApiModel):
    type_name: str
    label: str
    global_: bool = Field(alias="global")


class TypeCount(ApiModel):
    resource_type: str
    label: str
    count: int


class AccountTypeCount(ApiModel):
    account_type: str
    label: str
    count: int


class StatsOverview(ApiModel):
    resource_total: int
    account_total: int
    account_unreachable: int
    by_resource_type: list[TypeCount]
    by_account_type: list[AccountTypeCount]
    generated_at: datetime
