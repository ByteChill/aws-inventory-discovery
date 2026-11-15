from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, Identity, SmallInteger, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Index, SQLModel

from app.models.account import TIMESTAMPTZ


class Report(SQLModel, table=True):
    """A saved custom report: a condition tree over the current inventory (v2)."""

    __tablename__ = "t_inv_aws_report"
    __table_args__ = (
        Index("uq_t_inv_aws_report_name", "name", unique=True),
        Index("ix_t_inv_aws_report_enabled", "enabled"),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    name: str
    description: str | None = None
    # None: the report may match every resource type (common fields, tags and relation counts).
    subject_type: str | None = None
    definition_version: int = Field(
        default=1, sa_type=SmallInteger, sa_column_kwargs={"server_default": text("1")}
    )
    definition: dict[str, Any] = Field(sa_type=JSONB)
    enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime = Field(sa_type=TIMESTAMPTZ)
    updated_at: datetime = Field(sa_type=TIMESTAMPTZ)
