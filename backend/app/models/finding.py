from datetime import datetime

from sqlalchemy import CHAR, text
from sqlmodel import Field, SQLModel

from app.models.account import TIMESTAMPTZ


class FindingSummary(SQLModel, table=True):
    """The current findings of one resource, from the internal findings service (v2).

    Only a summary: four severity counts and the highest score, no finding history (D-07).
    A resource without a row has never been checked, which is not the same as having none.
    """

    __tablename__ = "t_inv_aws_finding_summary"

    account_id: str = Field(sa_type=CHAR(12), primary_key=True)
    aws_region: str = Field(primary_key=True)
    resource_type: str = Field(primary_key=True)
    resource_id: str = Field(primary_key=True)
    critical_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    high_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    medium_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    low_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    max_score: float | None = None
    checked_at: datetime = Field(sa_type=TIMESTAMPTZ)
