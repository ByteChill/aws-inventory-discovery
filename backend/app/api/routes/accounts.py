from typing import Annotated

from fastapi import APIRouter, Body, Response
from sqlmodel import Session, select

from app import accounts as account_service
from app.api.deps import AccountFilterDep, CacheDep, ReadSessionDep, RegistryDep, SessionDep
from app.aws.account_registry import AccountRegistryClient, RegistryError
from app.core.cache import Cache
from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import AccountRegion, AccountScanRegion
from app.models.scan import utcnow
from app.query import accounts as accounts_query
from app.schemas.accounts import (
    AccountBatchResult,
    AccountBatchUpdate,
    AccountCreate,
    AccountDetail,
    AccountImportResult,
    AccountRegionOptions,
    AccountScanPreview,
    AccountScanPreviewRequest,
    AccountSummary,
    AccountUpdate,
    CoverageCell,
)
from app.schemas.base import Page

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _invalidate(cache: Cache) -> None:
    for namespace in (
        "accounts",
        "applications",
        "facets",
        "stats",
        "resources",
        "reports",
    ):
        cache.delete_prefix(namespace)


def _detail(session: Session, account_id: str) -> AccountDetail:
    return accounts_query.get_account(
        session,
        account_id,
        get_settings().INACTIVE_ACCOUNT_PURGE_DAYS,
        list(get_settings().REACHABLE_REGIONS),
    )


@router.get("/region-options")
def region_options(session: ReadSessionDep) -> AccountRegionOptions:
    observed = set(session.exec(select(AccountRegion.region).distinct()))
    selected = set(session.exec(select(AccountScanRegion.region).distinct()))
    return AccountRegionOptions(
        reachable_regions=sorted(get_settings().REACHABLE_REGIONS),
        known_regions=sorted(observed | selected),
    )


@router.post("/scan-preview")
def scan_preview(
    body: Annotated[AccountScanPreviewRequest, Body()],
    session: ReadSessionDep,
    registry: RegistryDep,
) -> AccountScanPreview:
    counts = account_service.scan_preview(
        session,
        body.selector,
        resource_type_count=len(registry),
        reachable_regions=set(get_settings().REACHABLE_REGIONS),
        override_scan_policy=body.override_scan_policy,
        scan_enabled=body.scan_enabled,
        scan_region_mode=body.scan_region_mode,
        selected_regions=body.selected_regions,
    )
    return AccountScanPreview(**counts)


@router.post("/batch-policy")
def batch_policy(
    body: Annotated[AccountBatchUpdate, Body()],
    session: SessionDep,
    cache: CacheDep,
) -> AccountBatchResult:
    updated = account_service.batch_update(session, body, utcnow())
    _invalidate(cache)
    return AccountBatchResult(updated=updated)


@router.post("/import")
def import_from_registry(
    session: SessionDep,
    cache: CacheDep,
) -> AccountImportResult:
    settings = get_settings()
    if not settings.ACCOUNT_REGISTRY_URL:
        raise ApiError(
            503,
            "dependency_unavailable",
            "账号清单服务没有配置；可以手动新增账号",
        )
    try:
        fetched = AccountRegistryClient(
            settings.ACCOUNT_REGISTRY_URL, retries=1, backoff=0.5
        ).fetch_accounts()
        result = account_service.import_accounts(session, fetched, utcnow())
    except RegistryError as exc:
        raise ApiError(503, "dependency_unavailable", "账号清单服务暂时不可用") from exc
    except account_service.EmptyRegistry as exc:
        raise ApiError(503, "dependency_unavailable", "账号清单服务返回了空清单") from exc
    _invalidate(cache)
    return AccountImportResult(
        fetched=result.fetched, imported=result.imported, skipped=result.skipped
    )


@router.post("", status_code=201)
def create_account(
    body: Annotated[AccountCreate, Body()],
    session: SessionDep,
    cache: CacheDep,
) -> AccountDetail:
    try:
        account_service.create_account(session, body, utcnow())
    except account_service.AccountExists as exc:
        raise ApiError(
            409,
            "account_exists",
            "这个账号 ID 已经在账号主数据里",
            {"accountId": exc.account_id},
        ) from exc
    _invalidate(cache)
    return _detail(session, body.account_id)


@router.get("")
def list_accounts(
    params: AccountFilterDep, session: ReadSessionDep, cache: CacheDep
) -> Page[AccountSummary]:
    return accounts_query.list_accounts(
        session, params, get_settings().INACTIVE_ACCOUNT_PURGE_DAYS, cache
    )


@router.patch("/{account_id}")
def update_account(
    account_id: str,
    body: Annotated[AccountUpdate, Body()],
    session: SessionDep,
    cache: CacheDep,
) -> AccountDetail:
    try:
        account_service.update_account(session, account_id, body, utcnow())
    except account_service.AccountNotFound as exc:
        raise ApiError(404, "account_not_found", "账号不存在") from exc
    _invalidate(cache)
    return _detail(session, account_id)


@router.delete("/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    session: SessionDep,
    cache: CacheDep,
) -> Response:
    try:
        account_service.soft_delete_account(session, account_id, utcnow())
    except account_service.AccountNotFound as exc:
        raise ApiError(404, "account_not_found", "账号不存在") from exc
    _invalidate(cache)
    return Response(status_code=204)


@router.post("/{account_id}/restore")
def restore_account(
    account_id: str,
    session: SessionDep,
    cache: CacheDep,
) -> AccountDetail:
    try:
        account_service.restore_account(session, account_id, utcnow())
    except account_service.AccountNotFound as exc:
        raise ApiError(404, "account_not_found", "账号不存在") from exc
    _invalidate(cache)
    return _detail(session, account_id)


@router.get("/{account_id}")
def get_account(account_id: str, session: ReadSessionDep) -> AccountDetail:
    return _detail(session, account_id)


@router.get("/{account_id}/coverage")
def get_coverage(account_id: str, session: ReadSessionDep) -> list[CoverageCell]:
    return accounts_query.coverage(session, account_id)
