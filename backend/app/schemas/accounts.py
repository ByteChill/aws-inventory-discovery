from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.base import ApiModel

AccountType = Literal["dev", "preprod", "prod"]
AccountSource = Literal["manual", "imported", "legacy"]
ScanRegionMode = Literal["all", "selected"]
RegionScanState = Literal[
    "selected",
    "not_selected",
    "account_disabled",
    "unreachable",
    "unknown",
]


class AccountRef(ApiModel):
    account_id: str
    account_name: str
    account_type: AccountType
    eim_id: str | None
    eim_app: str | None
    ba_id: str | None


class AccountSummary(AccountRef):
    active: bool
    source: AccountSource
    scan_enabled: bool
    scan_region_mode: ScanRegionMode
    updated_at: datetime
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
    selected: bool
    effective: bool
    state: RegionScanState


class AccountDetail(AccountSummary):
    regions: list[AccountRegionInfo]


class CoverageCell(ApiModel):
    region: str
    resource_type: str
    state: Literal["ok", "failed", "access_denied", "stale", "never"]
    last_succeeded_at: datetime | None
    last_error_code: str | None


REGION_PATTERN = r"^[a-z0-9-]+$"
RegionName = Annotated[str, StringConstraints(pattern=REGION_PATTERN, min_length=1, max_length=64)]


def _unique_regions(values: list[str]) -> list[str]:
    return sorted({value.strip().lower() for value in values if value.strip()})


class AccountCreate(ApiModel):
    model_config = ConfigDict(
        alias_generator=ApiModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="forbid",
    )

    account_id: str = Field(pattern=r"^\d{12}$")
    account_name: str = Field(min_length=1, max_length=200)
    account_type: AccountType
    eim_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    eim_app: str | None = Field(default=None, max_length=200)
    ba_id: str | None = Field(default=None, max_length=64)
    scan_enabled: bool = True
    scan_region_mode: ScanRegionMode = "all"
    selected_regions: list[RegionName] = []

    @field_validator("selected_regions")
    @classmethod
    def clean_regions(cls, values: list[RegionName]) -> list[RegionName]:
        return _unique_regions(values)


class AccountUpdate(ApiModel):
    model_config = ConfigDict(
        alias_generator=ApiModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="forbid",
    )

    account_name: str | None = Field(default=None, min_length=1, max_length=200)
    account_type: AccountType | None = None
    eim_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    eim_app: str | None = Field(default=None, max_length=200)
    ba_id: str | None = Field(default=None, max_length=64)
    scan_enabled: bool | None = None
    scan_region_mode: ScanRegionMode | None = None
    selected_regions: list[RegionName] | None = None

    @field_validator("selected_regions")
    @classmethod
    def clean_regions(cls, values: list[RegionName] | None) -> list[RegionName] | None:
        return None if values is None else _unique_regions(values)

    @model_validator(mode="after")
    def reject_empty_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("give at least one field to change")
        for field in ("account_name", "account_type", "scan_enabled", "scan_region_mode"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if "selected_regions" in self.model_fields_set and self.selected_regions is None:
            raise ValueError("selected_regions cannot be null")
        return self


class AccountSelector(ApiModel):
    model_config = ConfigDict(
        alias_generator=ApiModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="forbid",
    )

    account_ids: list[str] = []
    eim_ids: list[str] = []
    eim_apps: list[str] = []
    ba_ids: list[str] = []
    account_types: list[AccountType] = []
    access_statuses: list[str] = []
    q: str | None = None
    scan_enabled: bool | None = None
    all: bool = False

    @field_validator(
        "account_ids", "eim_ids", "eim_apps", "ba_ids", "account_types", "access_statuses"
    )
    @classmethod
    def deduplicate(cls, values: list[str]) -> list[str]:
        return sorted(set(values))

    @model_validator(mode="after")
    def require_a_selector(self) -> Self:
        has_filter = bool(
            self.account_ids
            or self.eim_ids
            or self.eim_apps
            or self.ba_ids
            or self.account_types
            or self.access_statuses
            or self.q
            or self.scan_enabled is not None
        )
        if self.all and has_filter:
            raise ValueError("all=true cannot be combined with filters")
        if not self.all and not has_filter:
            raise ValueError("give at least one filter, or all=true")
        return self


class AccountBatchUpdate(ApiModel):
    model_config = ConfigDict(
        alias_generator=ApiModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="forbid",
    )

    selector: AccountSelector
    scan_enabled: bool | None = None
    scan_region_mode: ScanRegionMode | None = None
    selected_regions: list[RegionName] | None = None

    @field_validator("selected_regions")
    @classmethod
    def clean_regions(cls, values: list[RegionName] | None) -> list[RegionName] | None:
        return None if values is None else _unique_regions(values)

    @model_validator(mode="after")
    def require_a_change(self) -> Self:
        if (
            self.scan_enabled is None
            and self.scan_region_mode is None
            and self.selected_regions is None
        ):
            raise ValueError("give at least one policy field to change")
        for field in ("scan_enabled", "scan_region_mode"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if "selected_regions" in self.model_fields_set and self.selected_regions is None:
            raise ValueError("selected_regions cannot be null")
        return self


class AccountBatchResult(ApiModel):
    updated: int


class AccountScanPreviewRequest(ApiModel):
    model_config = ConfigDict(
        alias_generator=ApiModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="forbid",
    )

    selector: AccountSelector
    override_scan_policy: bool = False
    scan_enabled: bool | None = None
    scan_region_mode: ScanRegionMode | None = None
    selected_regions: list[RegionName] | None = None

    @field_validator("selected_regions")
    @classmethod
    def clean_regions(cls, values: list[RegionName] | None) -> list[RegionName] | None:
        return None if values is None else _unique_regions(values)


class AccountScanPreview(ApiModel):
    accounts: int
    regions: int
    estimated_tasks: int
    accounts_without_region_data: int
    skipped_disabled_accounts: int


class AccountImportResult(ApiModel):
    fetched: int
    imported: int
    skipped: int


class AccountRegionOptions(ApiModel):
    reachable_regions: list[str]
    known_regions: list[str]
