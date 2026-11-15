"""Custom reports: CRUD, counts, matching resources and export (docs/specs/reports.md)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.api.deps import CacheDep, LanguageDep, ReadSessionDep, RegistryDep, SessionDep
from app.query import reports as reports_query
from app.query.columns import Language
from app.schemas.reports import (
    ReportDetail,
    ReportFieldInfo,
    ReportInfo,
    ReportInput,
    ReportOverview,
    ReportPage,
)

router = APIRouter(prefix="/reports", tags=["reports"])


class ResultParams(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort: str | None = None
    s: str | None = None  # the dashboard's scope: `app:<EIM>` or `acct:<account id>`


class ExportParams(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    format: Literal["csv", "jsonl"] = "csv"
    lang: Language | None = None
    sort: str | None = None
    s: str | None = None


@router.get("")
def list_reports(
    session: ReadSessionDep, enabled: Annotated[bool | None, Query()] = None
) -> list[ReportInfo]:
    return reports_query.list_reports(session, enabled=enabled)


@router.post("", status_code=201)
def create_report(
    payload: ReportInput,
    session: SessionDep,
    registry: RegistryDep,
    x_caller_id: Annotated[str | None, Header()] = None,
) -> ReportDetail:
    return reports_query.create_report(session, registry, payload, x_caller_id)


@router.get("/overview")
def reports_overview(
    session: ReadSessionDep,
    registry: RegistryDep,
    cache: CacheDep,
    s: Annotated[str | None, Query()] = None,
) -> ReportOverview:
    return reports_query.overview(session, registry, cache, reports_query.parse_scope(s))


@router.get("/fields")
def report_fields(
    registry: RegistryDep,
    language: LanguageDep,
    subject_type: Annotated[str | None, Query(alias="subjectType")] = None,
) -> list[ReportFieldInfo]:
    return reports_query.field_palette(registry, language, subject_type)


@router.get("/{report_id}")
def get_report(report_id: int, session: ReadSessionDep, registry: RegistryDep) -> ReportDetail:
    return reports_query.detail(session, registry, report_id)


@router.put("/{report_id}")
def update_report(
    report_id: int,
    payload: ReportInput,
    session: SessionDep,
    registry: RegistryDep,
    x_caller_id: Annotated[str | None, Header()] = None,
) -> ReportDetail:
    return reports_query.update_report(session, registry, report_id, payload, x_caller_id)


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: int, session: SessionDep) -> Response:
    reports_query.delete_report(session, report_id)
    return Response(status_code=204)


@router.get("/{report_id}/results")
def report_results(
    report_id: int,
    params: Annotated[ResultParams, Query()],
    session: ReadSessionDep,
    registry: RegistryDep,
    cache: CacheDep,
    language: LanguageDep,
) -> ReportPage:
    return reports_query.results(
        session,
        registry,
        cache,
        report_id,
        page=params.page,
        page_size=params.page_size,
        sort=params.sort,
        scope=reports_query.parse_scope(params.s),
        language=language,
    )


@router.get("/{report_id}/export")
def export_report(
    report_id: int,
    params: Annotated[ExportParams, Query()],
    session: ReadSessionDep,
    registry: RegistryDep,
    language: LanguageDep,
) -> StreamingResponse:
    result = reports_query.export(
        session,
        registry,
        report_id,
        format=params.format,
        language=params.lang or language,
        scope=reports_query.parse_scope(params.s),
        sort=params.sort,
    )
    return StreamingResponse(
        result.body,
        media_type=result.media_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )
