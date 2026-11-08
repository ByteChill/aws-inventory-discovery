from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Any

import boto3
from botocore.config import Config
from fastapi import Depends, Header, Query
from sqlmodel import Session

from app.core.cache import Cache
from app.core.config import get_settings
from app.core.db import get_read_session, get_session
from app.query.accounts import AccountFilter
from app.query.columns import Language, pick_language
from app.query.export import ExportParams
from app.query.facets import FacetParams
from app.query.filters import Paging
from app.types.registry import TypeRegistry

# Writes (scan requests, guard confirmation) go to the primary.
SessionDep = Annotated[Session, Depends(get_session)]
# Queries and exports read from DATABASE_READ_URL (falls back to DATABASE_URL).
ReadSessionDep = Annotated[Session, Depends(get_read_session)]


LOGS_REGION = "ap-east-1"  # the hub account's log group, when no region is configured


@lru_cache
def get_registry() -> TypeRegistry:
    """The resource types, loaded from their spec files once per process."""
    return TypeRegistry.from_directory()


@lru_cache
def get_logs_client() -> Any:
    """CloudWatch Logs in the hub account: the API's only AWS call. Endpoint overrides come
    from boto3's own environment variables."""
    return boto3.client(
        "logs",
        region_name=boto3.Session().region_name or LOGS_REGION,
        config=Config(
            connect_timeout=3, read_timeout=10, retries={"mode": "standard", "max_attempts": 2}
        ),
    )


def get_cache() -> Iterator[Cache]:
    cache = Cache.from_url(get_settings().REDIS_URL)
    try:
        yield cache
    finally:
        cache.close()


def get_language(accept_language: Annotated[str | None, Header()] = None) -> Language:
    return pick_language(accept_language)


RegistryDep = Annotated[TypeRegistry, Depends(get_registry)]
CacheDep = Annotated[Cache, Depends(get_cache)]
LogsClientDep = Annotated[Any, Depends(get_logs_client)]
LanguageDep = Annotated[Language, Depends(get_language)]

# Filters shared by list, facets and export (DESIGN 8.2), each with its own extras.
ResourceListDep = Annotated[Paging, Query()]
FacetParamsDep = Annotated[FacetParams, Query()]
ExportParamsDep = Annotated[ExportParams, Query()]
AccountFilterDep = Annotated[AccountFilter, Query()]
