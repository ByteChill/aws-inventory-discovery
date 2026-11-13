import logging
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import CacheDep, ReadSessionDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    database: Literal["ok", "unavailable"]
    redis: Literal["ok", "unavailable"]


@router.get("")
def health(session: ReadSessionDep, cache: CacheDep, response: Response) -> HealthStatus:
    """Database down is a 503; Redis down only slows things, so it stays 200."""
    try:
        session.connection().execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        logger.exception("database health check failed")
        database = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    # The app's shared client, not a new connection per check (ECS calls this every 30 s).
    redis_status = "ok" if cache.ping() else "unavailable"
    return HealthStatus(database=database, redis=redis_status)
