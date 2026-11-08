"""Resource types, columns and the overview numbers (api.md section 4)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, LanguageDep, ReadSessionDep, RegistryDep
from app.query import stats as stats_query
from app.query.columns import COMMON_COLUMNS, label_of
from app.schemas.resources import Column, ResourceTypeInfo, StatsOverview

types_router = APIRouter(prefix="/resource-types", tags=["metadata"])
columns_router = APIRouter(prefix="/columns", tags=["metadata"])
stats_router = APIRouter(prefix="/stats", tags=["metadata"])


@types_router.get("")
def list_resource_types(registry: RegistryDep, language: LanguageDep) -> list[ResourceTypeInfo]:
    return [
        ResourceTypeInfo(
            type_name=spec.type_name, label=label_of(spec.label, language), global_=spec.global_
        )
        for spec in registry
    ]


@columns_router.get("")
def list_columns(
    registry: RegistryDep,
    language: LanguageDep,
    resource_type: Annotated[str | None, Query(alias="resourceType")] = None,
) -> list[Column]:
    columns = [
        Column(
            key=column.key,
            label=column.label(language),
            type=column.type,
            mono=column.mono,
            sortable=column.sortable,
            filterable=column.filterable,
            scope="common",
        )
        for column in COMMON_COLUMNS
    ]
    if resource_type is not None and resource_type in registry:
        columns.extend(
            Column(
                key=column.key,
                label=label_of(column.label, language),
                type=column.type,
                mono=column.mono,
                sortable=column.sortable,
                filterable=column.filterable,
                scope="type",
            )
            for column in registry.get(resource_type).columns
        )
    return columns


@stats_router.get("/overview")
def overview(
    session: ReadSessionDep, registry: RegistryDep, cache: CacheDep, language: LanguageDep
) -> StatsOverview:
    return stats_query.overview(session, registry, cache, language)
