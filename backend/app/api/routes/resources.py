from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.api.deps import (
    CacheDep,
    ExportParamsDep,
    FacetParamsDep,
    LanguageDep,
    ReadSessionDep,
    RegistryDep,
    ResourceListDep,
)
from app.query import export as export_query
from app.query import facets as facets_query
from app.query import resources as resources_query
from app.schemas.base import Page
from app.schemas.resources import FacetBucket, RelationshipEntry, ResourceDetail, ResourceItem

router = APIRouter(prefix="/resources", tags=["resources"])


class RelationshipParams(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    direction: Literal["out", "in", "both"] = "both"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


@router.get("")
def list_resources(
    params: ResourceListDep, session: ReadSessionDep, registry: RegistryDep
) -> Page[ResourceItem]:
    return resources_query.list_resources(session, registry, params)


@router.get("/facets")
def resource_facets(
    params: FacetParamsDep,
    session: ReadSessionDep,
    registry: RegistryDep,
    cache: CacheDep,
    language: LanguageDep,
) -> dict[str, list[FacetBucket]]:
    return facets_query.facets(session, registry, cache, params, language)


@router.get("/export")
def export_resources(
    params: ExportParamsDep,
    session: ReadSessionDep,
    registry: RegistryDep,
    language: LanguageDep,
) -> StreamingResponse:
    result = export_query.export(session, registry, params, language)
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
