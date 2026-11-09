import logging
from typing import Literal

import redis
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import ReadSessionDep
from app.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    database: Literal["ok", "unavailable"]
    redis: Literal["ok", "unavailable"]


def redis_is_up(url: str | None) -> bool:
    if url is None:
        return False
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except redis.RedisError, OSError, ValueError:
        logger.warning("redis health check failed", exc_info=True)
        return False


@router.get("")
def health(session: ReadSessionDep, response: Response) -> HealthStatus:
    """Database down is a 503; Redis down only slows things, so it stays 200."""
    try:
        session.connection().execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        logger.exception("database health check failed")
        database = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    redis_status = "ok" if redis_is_up(get_settings().REDIS_URL) else "unavailable"
    return HealthStatus(database=database, redis=redis_status)
