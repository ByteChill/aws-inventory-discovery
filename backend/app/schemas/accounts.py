from datetime import datetime
from typing import Literal

from app.schemas.base import ApiModel


class AccountRef(ApiModel):
    account_id: str
    account_name: str
    account_type: Literal["dev", "preprod", "prod"]
    eim_id: str | None
    eim_app: str | None
    ba_id: str | None


class AccountSummary(AccountRef):
    active: bool
    access_status: str
    access_error: str | None
    resource_count: int
    last_scan_ok_at: datetime | None
    purge_at: datetime | None  # only for inactive accounts


class AccountRegionInfo(ApiModel):
    region: str
    opt_in_status: str
    reachable: bool
    config_recording: bool | None


class AccountDetail(AccountSummary):
    regions: list[AccountRegionInfo]


class CoverageCell(ApiModel):
    region: str
    resource_type: str
    state: Literal["ok", "failed", "access_denied", "stale", "never"]
    last_succeeded_at: datetime | None
    last_error_code: str | None
