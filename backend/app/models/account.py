from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Index, SQLModel

TIMESTAMPTZ = DateTime(timezone=True)


class Account(SQLModel, table=True):
    """Account snapshot, refreshed from the account registry at the start of each sweep."""

    __tablename__ = "t_inv_aws_account"
    __table_args__ = (
        Index("ix_t_inv_aws_account_account_type", "account_type"),
        Index("ix_t_inv_aws_account_eim_id", "eim_id"),
        Index("ix_t_inv_aws_account_ba_id", "ba_id"),
        Index("ix_t_inv_aws_account_access_status", "access_status"),
    )

    account_id: str = Field(sa_type=CHAR(12), primary_key=True)
    account_name: str
    account_type: str  # dev / preprod / prod
    eim_id: str | None = Field(default=None, sa_type=CHAR(8))
    eim_app: str | None = None
    ba_id: str | None = None
    active: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    inactive_since: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    registry_fetched_at: datetime = Field(sa_type=TIMESTAMPTZ)
    # unknown / ok / partial / access_denied / credential_error
    access_status: str = Field(
        default="unknown", sa_column_kwargs={"server_default": text("'unknown'")}
    )
    access_error: str | None = None
    access_checked_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
    last_scan_ok_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)


class AccountRegion(SQLModel, table=True):
    """Regions enabled per account, plus the Config recorder setup of each region."""

    __tablename__ = "t_inv_aws_account_region"

    account_id: str = Field(
        sa_type=CHAR(12), foreign_key="t_inv_aws_account.account_id", primary_key=True
    )
    region: str = Field(primary_key=True)
    opt_in_status: str
    reachable: bool  # whether the region is in REACHABLE_REGIONS
    # {recording, allSupported, resourceTypes[], dailyTypes[]}
    config_recorder: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    checked_at: datetime = Field(sa_type=TIMESTAMPTZ)
