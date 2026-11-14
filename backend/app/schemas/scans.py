from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.base import ApiModel
from app.schemas.resources import ResourceDetail


class ResourceCounts(ApiModel):
    new: int = 0
    updated: int = 0
    missing: int = 0
    deleted: int = 0


class ScanCounts(ApiModel):
    tasks: dict[str, int]
    resources: ResourceCounts


class ScanScope(ApiModel):
    account_ids: list[str] | None = None
    aws_regions: list[str] | None = None
    resource_types: list[str] | None = None
    all: bool | None = None


class ScanRequestOut(ApiModel):
    id: int
    trigger: Literal["schedule", "api"]
    caller_id: str | None
    scope: ScanScope | None
    target: dict[str, str] | None
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    counts: ScanCounts
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class FailureCount(ApiModel):
    error_code: str
    count: int


class ScanDetail(ScanRequestOut):
    failures_by_code: list[FailureCount]


class TaskOut(ApiModel):
    id: int
    scan_request_id: int
    kind: str
    source: str | None
    account_id: str
    region: str | None
    resource_type: str | None
    resource_id: str | None
    status: str
    attempt: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    stats: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class Requeued(ApiModel):
    requeued: int


class SweepProgress(ApiModel):
    scan_request_id: int
    accounts_done: int
    accounts_total: int
    started_at: datetime
    estimated_finish_at: datetime | None
    behind_schedule: bool


class Backlog(ApiModel):
    queued: int
    oldest_wait_seconds: int


class BacklogPair(ApiModel):
    on_demand: Backlog
    scheduled: Backlog


class WorkerActivity(ApiModel):
    active_tasks: int
    last_heartbeat_at: datetime | None


class ScanSummary(ApiModel):
    sweep: SweepProgress | None
    backlog: BacklogPair
    failures24h: list[FailureCount] = Field(alias="failures24h")
    missing_count: int
    abnormal_scope_count: int
    workers: WorkerActivity


class LogEvent(ApiModel):
    timestamp: datetime
    level: str
    message: str
    data: dict[str, Any]


class TaskLogs(ApiModel):
    events: list[LogEvent]
    next_token: str | None


class ScanScopeOut(ApiModel):
    scan_shard_id: int
    account_id: str
    region: str
    resource_type: str
    guard_state: str
    guard_detected_at: datetime | None
    candidate_count: int
    active_count: int


InventoryStatus = Literal["active", "missing", "deleted", "purged"]


class ScopeCandidate(ApiModel):
    """A resource the guard spared. `purged` means the row is gone from the inventory."""

    id: int | None
    resource_id: str
    resource_name: str | None
    arn: str
    inventory_status: InventoryStatus


class ScanCreated(ApiModel):
    scan_request_id: int


class ConfirmChangeBody(ApiModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(pattern=r"^\d{12}$")
    aws_region: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    # The detection the reader reviewed; a newer detection must not be confirmed unseen.
    guard_detected_at: datetime | None = None


class RefreshByKey(ApiModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(pattern=r"^\d{12}$")
    aws_region: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)


class RefreshByArn(ApiModel):
    model_config = ConfigDict(extra="forbid")

    arn: str = Field(min_length=1)


class RefreshError(ApiModel):
    code: str
    message: str


class RefreshedResource(ResourceDetail):
    refresh_error: RefreshError | None = None


class RefreshAccepted(ApiModel):
    task_id: int
