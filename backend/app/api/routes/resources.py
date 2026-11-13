from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Header, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlmodel import select

from app.api import support
from app.api.deps import (
    CacheDep,
    ExportParamsDep,
    FacetParamsDep,
    LanguageDep,
    ReadSessionDep,
    RegistryDep,
    ResourceFilterDep,
    ResourceListDep,
    SessionDep,
)
from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import Resource
from app.query import export as export_query
from app.query import facets as facets_query
from app.query import resources as resources_query
from app.schemas.base import Page
from app.schemas.resources import (
    FacetBucket,
    RelationshipEntry,
    ResourceDetail,
    ResourceItem,
    ResourceSummary,
)
from app.schemas.scans import (
    RefreshAccepted,
    RefreshByArn,
    RefreshByKey,
    RefreshedResource,
    RefreshError,
)
from app.worker import requests

router = APIRouter(prefix="/resources", tags=["resources"])


def task_retention() -> timedelta:
    """How long finished scan tasks are kept; `scanRequestId` is resolved from them until then."""
    return timedelta(days=get_settings().TASK_RETENTION_DAYS)


class RelationshipParams(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    direction: Literal["out", "in", "both"] = "both"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


@router.get("")
def list_resources(
    params: ResourceListDep, session: ReadSessionDep, registry: RegistryDep, cache: CacheDep
) -> Page[ResourceItem]:
    return resources_query.list_resources(
        session, registry, cache, params, task_retention=task_retention()
    )


@router.get("/facets")
def resource_facets(
    params: FacetParamsDep,
    session: ReadSessionDep,
    registry: RegistryDep,
    cache: CacheDep,
    language: LanguageDep,
) -> dict[str, list[FacetBucket]]:
    return facets_query.facets(
        session, registry, cache, params, language, task_retention=task_retention()
    )


@router.get("/summary")
def resource_summary(
    params: ResourceFilterDep,
    session: ReadSessionDep,
    registry: RegistryDep,
    cache: CacheDep,
) -> ResourceSummary:
    return resources_query.summary(
        session, params, registry, task_retention=task_retention(), cache=cache
    )


@router.get("/export")
def export_resources(
    params: ExportParamsDep,
    session: ReadSessionDep,
    registry: RegistryDep,
    language: LanguageDep,
) -> StreamingResponse:
    result = export_query.export(
        session, registry, params, params.lang or language, task_retention=task_retention()
    )
    return StreamingResponse(
        result.body,
        media_type=result.media_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.get("/lookup")
def lookup_resource(
    arn: Annotated[str, Query()], session: ReadSessionDep, registry: RegistryDep
) -> list[ResourceDetail]:
    return resources_query.lookup(session, registry, arn)


@router.post("/refresh", responses={202: {"model": RefreshAccepted}})
def refresh_resource(
    body: Annotated[RefreshByKey | RefreshByArn, Body()],
    session: SessionDep,
    registry: RegistryDep,
    response: Response,
    wait: Annotated[int, Query(ge=0, le=20)] = 0,
    x_caller_id: Annotated[str | None, Header()] = None,
) -> RefreshedResource | RefreshAccepted:
    settings = get_settings()
    key = support.resolve_refresh_target(session, body)
    support.check_resource_type(registry, key.resource_type)
    support.check_resource_id(key.resource_type, key.resource_id)
    support.ensure_accounts(session, settings, [key.account_id])
    task = requests.request_resource_refresh(session, key, caller_id=x_caller_id).task
    assert task.id is not None
    task_id = task.id
    task = support.wait_for_task(session, task_id, wait)
    if task.status in support.NOT_FINISHED:
        response.status_code = 202
        return RefreshAccepted(task_id=task_id)
    found = session.exec(
        select(Resource.id).where(
            Resource.account_id == key.account_id,
            Resource.aws_region == key.aws_region,
            Resource.resource_type == key.resource_type,
            Resource.resource_id == key.resource_id,
        )
    ).first()
    if found is None:
        if task.status == "succeeded":  # AWS says it is gone and the inventory never had it
            raise ApiError(404, "resource_not_found", "资源不存在", key._asdict())
        raise ApiError(
            502,
            "refresh_failed",
            task.error_message or "刷新失败",
            {"taskId": task.id, "errorCode": task.error_code},
        )
    detail = resources_query.get_detail(session, registry, found)
    error = (
        None
        if task.status == "succeeded"
        else RefreshError(code=task.error_code or task.status, message=task.error_message or "")
    )
    return RefreshedResource(**detail.model_dump(), refresh_error=error)


@router.get("/{resource_id}")
def get_resource(
    resource_id: int, session: ReadSessionDep, registry: RegistryDep
) -> ResourceDetail:
    return resources_query.get_detail(session, registry, resource_id)


@router.get("/{resource_id}/relationships")
def get_relationships(
    resource_id: int, params: Annotated[RelationshipParams, Query()], session: ReadSessionDep
) -> Page[RelationshipEntry]:
    return resources_query.relationships(
        session, resource_id, params.direction, params.page, params.page_size
    )
