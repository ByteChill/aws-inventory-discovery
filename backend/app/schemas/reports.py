"""Wire models of custom reports (docs/specs/reports.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.reports.definition import Condition
from app.schemas.base import ApiModel
from app.schemas.resources import ResourceItem


class ReportInput(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    subject_type: str | None = None
    where: Condition
    columns: list[str] = []
    enabled: bool = True


class ReportInfo(ApiModel):
    id: int
    name: str
    description: str | None
    subject_type: str | None
    enabled: bool
    created_by: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime


class ReportDetail(ReportInfo):
    definition_version: int
    where: Condition
    columns: list[str]


class ReportColumnInfo(ApiModel):
    key: str
    label: str
    type: str
    mono: bool = False
    array: bool = False


class ReportFieldInfo(ApiModel):
    """A field the report editor can offer: what a condition may match on."""

    key: str
    label: str
    type: str
    array: bool = False
    group: Literal["common", "type", "relation"]


class ReportRow(ResourceItem):
    """A matching resource plus the values of the report's own columns."""

    values: dict[str, Any]


class ReportPage(ApiModel):
    items: list[ReportRow]
    page: int
    page_size: int
    total: int
    columns: list[ReportColumnInfo]
    generated_at: datetime


class ReportOverviewItem(ApiModel):
    report_id: int
    name: str
    subject_type: str | None
    count: int
    error: str | None = None


class ReportOverview(ApiModel):
    generated_at: datetime
    items: list[ReportOverviewItem]
