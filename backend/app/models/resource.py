from datetime import datetime
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
from sqlmodel import Field, Index, SQLModel, UniqueConstraint

from app.models.account import TIMESTAMPTZ

TRGM = "gin_trgm_ops"


class Resource(SQLModel, table=True):
    """Current state of one resource, in AWS Config format."""

    __tablename__ = "t_inv_aws_resource"
    __table_args__ = (
        # A resource is identified by this tuple, never by ARN.
        UniqueConstraint(
            "account_id",
            "aws_region",
            "resource_type",
            "resource_id",
            name="uq_t_inv_aws_resource_identity",
        ),
        Index("ix_t_inv_aws_resource_arn", "arn"),
        Index("ix_t_inv_aws_resource_type_status", "resource_type", "status"),
        Index(
            "ix_t_inv_aws_resource_scope_live",
            "account_id",
            "aws_region",
            "resource_type",
            postgresql_where=text("status <> 'deleted'"),
        ),
        Index(
            "ix_t_inv_aws_resource_tags",
            "tags",
            postgresql_using="gin",
            postgresql_ops={"tags": "jsonb_path_ops"},
        ),
        Index(
            "ix_t_inv_aws_resource_name_trgm",
            "resource_name",
            postgresql_using="gin",
            postgresql_ops={"resource_name": TRGM},
        ),
        Index(
            "ix_t_inv_aws_resource_arn_trgm",
            "arn",
            postgresql_using="gin",
            postgresql_ops={"arn": TRGM},
        ),
        Index(
            "ix_t_inv_aws_resource_resource_id_trgm",
            "resource_id",
            postgresql_using="gin",
            postgresql_ops={"resource_id": TRGM},
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    arn: str  # not unique: a shared AMI has the same ARN in every account
    account_id: str = Field(sa_type=CHAR(12), foreign_key="t_inv_aws_account.account_id")
    aws_region: str  # 'global' for global resources
    resource_type: str  # AWS::EC2::VPC
    resource_id: str
    resource_name: str | None = None
    tags: list[Any] = Field(
        default_factory=list,
        sa_type=JSONB,
        sa_column_kwargs={"server_default": text("'[]'::jsonb")},
    )
    configuration: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    relationships: list[Any] = Field(
        default_factory=list,
        sa_type=JSONB,
        sa_column_kwargs={"server_default": text("'[]'::jsonb")},
    )
    live_raw: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    config_capture_time: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    live_observed_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    observed_at: datetime = Field(sa_type=TIMESTAMPTZ)
    observed_source: str  # config / cloudcontrol / native
    content_hash: str
    # active / missing / deleted
    status: str = Field(default="active", sa_column_kwargs={"server_default": text("'active'")})
    missing_since: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    # First piece of deletion evidence: the last complete scan did not see it.
    absent_in_scan_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    missing_checks: int = Field(
        default=0, sa_type=SmallInteger, sa_column_kwargs={"server_default": text("0")}
    )
    deleted_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    first_seen_at: datetime = Field(sa_type=TIMESTAMPTZ)
    last_changed_at: datetime = Field(sa_type=TIMESTAMPTZ)
    last_task_id: int | None = Field(default=None, sa_type=BigInteger)


class ResourceRelation(SQLModel, table=True):
    """Relationships between resources, for reverse lookups."""

    __tablename__ = "t_inv_aws_resource_relation"
    __table_args__ = (Index("ix_t_inv_aws_resource_relation_to", "to_resource_id"),)

    from_resource_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("t_inv_aws_resource.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    to_resource_type: str = Field(primary_key=True)
    to_resource_key: str = Field(primary_key=True)  # resourceId as given by Config
    # NULL when no matching resource exists
    to_resource_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("t_inv_aws_resource.id", ondelete="SET NULL")),
    )
    relation_name: str = Field(primary_key=True)
