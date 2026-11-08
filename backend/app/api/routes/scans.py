from typing import Annotated, Literal

from fastapi import APIRouter, Body, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.api.deps import LogsClientDep, RegistryDep, SessionDep
from app.api.support import check_resource_type, ensure_accounts
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import ApiError
from app.models import ScanShard
from app.query import scans as scans_query
from app.schemas.base import Page
from app.schemas.scans import (
    ConfirmChangeBody,
    Requeued,
    ScanCreated,
    ScanDetail,
    ScanRequestOut,
    ScanScopeOut,
    ScanSummary,
    TaskLogs,
    TaskOut,
)
from app.worker import queue, requests
from app.worker.writer import ResourceWriter

router = APIRouter(prefix="/scans", tags=["scans"])
tasks_router = APIRouter(prefix="/tasks", tags=["scans"])
scopes_router = APIRouter(prefix="/scan-scopes", tags=["scans"])

CallerId = Annotated[str | None, Header()]


class Paged(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class ScanListParams(Paged):
    trigger: Literal["schedule", "api"] | None = None
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"] | None = (
        None
    )


class TaskListParams(Paged):
    status: str | None = None
    error_code: str | None = None
    account_id: str | None = None
    kind: str | None = None


class LogParams(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    limit: int = Field(default=200, ge=1, le=1000)
    next_token: str | None = None


class ScopeParams(Paged):
    guard_state: Literal["normal", "abnormal", "confirmed"] = "abnormal"


@router.get("/summary")
def scan_summary(session: SessionDep) -> ScanSummary:
    return scans_query.summary(session, get_settings())


@router.post("", status_code=201)
def create_scan(
    spec: Annotated[requests.ScanSpec, Body()],
    session: SessionDep,
    registry: RegistryDep,
    response: Response,
    x_caller_id: CallerId = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> ScanRequestOut:
    settings = get_settings()
    for type_name in spec.resource_types:
        check_resource_type(registry, type_name)
    try:
        try:
            created = requests.create_scan(
                session, spec, settings, caller_id=x_caller_id, idempotency_key=idempotency_key
            )
        except requests.UnknownAccounts as exc:
            ensure_accounts(session, settings, exc.account_ids)
            created = requests.create_scan(
                session, spec, settings, caller_id=x_caller_id, idempotency_key=idempotency_key
            )
    except requests.ScopeTooLarge as exc:
        raise ApiError(
            422,
            "scope_too_large",
            f"一次最多扫描 {exc.limit} 个账号",
            {"limit": exc.limit, "requested": exc.requested},
        ) from exc
    except requests.IdempotencyConflict as exc:
        raise ApiError(
            409, "idempotency_conflict", "同一个 Idempotency-Key 对应了不同的请求内容"
        ) from exc
    except requests.UnknownAccounts as exc:
        raise ApiError(
            404, "account_not_found", "账号不存在", {"accountIds": exc.account_ids}
        ) from exc
    if not created.created:
        response.status_code = 200
    return scans_query.request_out(created.request)


@router.get("")
def list_scans(
    params: Annotated[ScanListParams, Query()], session: SessionDep
) -> Page[ScanRequestOut]:
    return scans_query.list_scans(
        session, params.trigger, params.status, params.page, params.page_size
    )


@router.get("/{scan_id}")
def get_scan(scan_id: int, session: SessionDep) -> ScanDetail:
    return scans_query.get_scan(session, scan_id)


@router.get("/{scan_id}/tasks")
def list_scan_tasks(
    scan_id: int, params: Annotated[TaskListParams, Query()], session: SessionDep
) -> Page[TaskOut]:
    return scans_query.list_tasks(
        session,
        scan_id,
        status=params.status,
        error_code=params.error_code,
        account_id=params.account_id,
        kind=params.kind,
        page=params.page,
        page_size=params.page_size,
    )


@router.post("/{scan_id}/retry-failed")
def retry_failed(scan_id: int, session: SessionDep) -> Requeued:
    scans_query.get_scan(session, scan_id)  # 404 scan_not_found
    try:
        return Requeued(requeued=requests.retry_failed(session, scan_id))
    except requests.InvalidRequestState as exc:
        raise ApiError(409, "scan_cancelled", "已取消的扫描不能重试") from exc


@router.post("/{scan_id}/cancel")
def cancel_scan(scan_id: int, session: SessionDep) -> ScanRequestOut:
    current = scans_query.get_scan(session, scan_id)
    if current.status in queue.TERMINAL_REQUEST_STATUSES:
        raise ApiError(409, "scan_not_cancellable", "扫描已经结束", {"status": current.status})
    cancelled = queue.cancel_request(session, scan_id)
    if cancelled is None:
        raise scans_query.scan_not_found(scan_id)
    if cancelled.status != "cancelled":  # it finished while we were cancelling
        raise ApiError(409, "scan_not_cancellable", "扫描已经结束", {"status": cancelled.status})
    return scans_query.request_out(cancelled)


@tasks_router.get("/{task_id}")
def get_task(task_id: int, session: SessionDep) -> TaskOut:
    return scans_query.task_out(scans_query.get_task(session, task_id))


@tasks_router.get("/{task_id}/logs")
def get_task_logs(
    task_id: int,
    params: Annotated[LogParams, Query()],
    session: SessionDep,
    logs: LogsClientDep,
) -> TaskLogs:
    task = scans_query.get_task(session, task_id)
    return scans_query.task_logs(
        logs, get_settings().WORKER_LOG_GROUP, task, params.limit, params.next_token
    )


@scopes_router.get("")
def list_scan_scopes(
    params: Annotated[ScopeParams, Query()], session: SessionDep
) -> Page[ScanScopeOut]:
    return scans_query.list_scopes(session, params.guard_state, params.page, params.page_size)


@scopes_router.post("/confirm-change")
def confirm_scope_change(
    body: ConfirmChangeBody,
    session: SessionDep,
    registry: RegistryDep,
    x_caller_id: CallerId = None,
) -> ScanCreated:
    check_resource_type(registry, body.resource_type)
    shard = session.get(ScanShard, (body.account_id, body.aws_region, body.resource_type))
    if shard is None:
        raise ApiError(404, "scope_not_found", "范围不存在")
    if shard.guard_state != "abnormal":
        raise ApiError(
            409,
            "scope_not_abnormal",
            "这个范围没有等待确认的异常",
            {"guardState": shard.guard_state},
        )
    session.rollback()
    writer = ResourceWriter(get_engine(), registry, get_settings())
    writer.confirm_guard(
        account_id=body.account_id,
        region=body.aws_region,
        resource_type=body.resource_type,
        by=x_caller_id or "api",
    )
    spec = registry.get(body.resource_type)
    request = requests.create_scope_scan(
        session,
        body.account_id,
        body.aws_region,
        body.resource_type,
        source="native" if spec.sources.native else "cloudcontrol",
        caller_id=x_caller_id,
    )
    assert request.id is not None
    return ScanCreated(scan_request_id=request.id)
