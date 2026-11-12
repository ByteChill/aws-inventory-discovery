from fastapi import APIRouter

from app.api.deps import ApplicationFilterDep, ReadSessionDep
from app.query import applications as applications_query
from app.schemas.applications import Application
from app.schemas.base import Page

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("")
def list_applications(params: ApplicationFilterDep, session: ReadSessionDep) -> Page[Application]:
    return applications_query.list_applications(session, params)
