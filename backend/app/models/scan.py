from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Column,
    ForeignKey,
    Identity,
    SmallInteger,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Index, SQLModel

from app.models.account import TIMESTAMPTZ

NOW = {"server_default": text("now()")}


def utcnow() -> datetime:
    return datetime.now(UTC)


class ScanRequest(SQLModel, table=True):
    """One API call or one scheduled sweep."""

    __tablename__ = "t_inv_aws_scan_request"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    trigger: str  # schedule / api
    caller_id: str | None = None
    scope: dict[str, Any] | None = Field(default=None, sa_type=JSONB)  # {accountIds[], ...}
    target: dict[str, Any] | None = Field(default=None, sa_type=JSONB)  # a single resource
    priority: int = Field(sa_type=SmallInteger)  # 0 = on demand, 1 = scheduled
    # queued / running / succeeded / partial / failed / cancelled
    status: str
    counts: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    idempotency_key: str | None = Field(default=None, unique=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=TIMESTAMPTZ, sa_column_kwargs=NOW)
    started_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    finished_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)


class ScanTask(SQLModel, table=True):
    """A scan task; the table is also the task queue."""

    __tablename__ = "t_inv_aws_scan_task"
    __table_args__ = (
        Index(
            "ix_t_inv_aws_scan_task_queue",
            "priority",
            "not_before",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_t_inv_aws_scan_task_scan_request_id", "scan_request_id"),
        # De-duplicates single-resource refreshes.
        Index(
            "uq_t_inv_aws_scan_task_resource_inflight",
            "account_id",
            "region",
            "resource_type",
            "resource_id",
            unique=True,
            postgresql_where=text("kind = 'resource' AND status IN ('queued', 'running')"),
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    scan_request_id: int = Field(foreign_key="t_inv_aws_scan_request.id", sa_type=BigInteger)
    # sweep_account / config_group / shard / resource / confirm_missing
    kind: str
    source: str | None = None  # config / cloudcontrol / native
    account_id: str = Field(sa_type=CHAR(12))
    region: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    priority: int = Field(sa_type=SmallInteger)
    # queued / running / succeeded / failed / skipped / coalesced / cancelled
    status: str
    not_before: datetime = Field(default_factory=utcnow, sa_type=TIMESTAMPTZ, sa_column_kwargs=NOW)
    attempt: int = Field(
        default=0, sa_type=SmallInteger, sa_column_kwargs={"server_default": text("0")}
    )
    max_attempts: int = Field(
        default=3, sa_type=SmallInteger, sa_column_kwargs={"server_default": text("3")}
    )
    worker_id: str | None = None
    heartbeat_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    observed_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    coalesced_into: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("t_inv_aws_scan_task.id")),
    )
    stats: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utcnow, sa_type=TIMESTAMPTZ, sa_column_kwargs=NOW)
    started_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    finished_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)


class ScanShard(SQLModel, table=True):
    """Per-scope lease, last success and deletion guard."""

    __tablename__ = "t_inv_aws_scan_shard"

    account_id: str = Field(sa_type=CHAR(12), primary_key=True)
    region: str = Field(primary_key=True)
    resource_type: str = Field(primary_key=True)
    lease_task_id: int | None = Field(default=None, sa_type=BigInteger)
    lease_expires_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    pending_task_id: int | None = Field(default=None, sa_type=BigInteger)  # at most one
    last_started_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    last_succeeded_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    last_task_id: int | None = Field(default=None, sa_type=BigInteger)
    last_error_code: str | None = None
    consecutive_failures: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    # normal / abnormal / confirmed
    guard_state: str = Field(
        default="normal", sa_column_kwargs={"server_default": text("'normal'")}
    )
    guard_detected_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    guard_confirmed_by: str | None = None
