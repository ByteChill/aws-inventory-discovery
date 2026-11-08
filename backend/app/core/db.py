from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import get_settings


def normalise_url(url: str) -> str:
    """Make sure SQLAlchemy uses psycopg 3, whatever scheme the URL arrived with."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str, schema: str, **engine_kwargs: Any) -> Engine:
    # Every connection starts with the service's schema on its search_path, so
    # table names carry no schema qualifier anywhere in the code. `public` stays
    # behind it only so that extension operator classes (pg_trgm) resolve.
    return create_engine(
        normalise_url(url),
        pool_pre_ping=True,
        connect_args={
            "options": f"-csearch_path={schema},public",
            "connect_timeout": 5,
        },
        **engine_kwargs,
    )


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return make_engine(settings.DATABASE_URL, settings.DATABASE_SCHEMA)


@lru_cache
def get_read_engine() -> Engine:
    settings = get_settings()
    return make_engine(settings.database_read_url, settings.DATABASE_SCHEMA)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def get_read_session() -> Iterator[Session]:
    with Session(get_read_engine()) as session:
        yield session
