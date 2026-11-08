from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import get_registry
from app.api.errors import register_error_handlers
from app.api.main import api_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.types.hooks import TYPE_HOOKS
from app.types.registry import validate_hooks

setup_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # Load the resource types once; an invalid spec file stops the start-up.
    validate_hooks(get_registry(), TYPE_HOOKS)
    yield


app = FastAPI(
    title="aws-inventory-discovery", openapi_url="/api/v1/openapi.json", lifespan=lifespan
)
register_error_handlers(app)
app.include_router(api_router, prefix="/api/v1")

# Registered last so the API routes win; the dashboard is absent in plain dev runs.
frontend_dist = get_settings().frontend_dist
if frontend_dist.is_dir():
    app.frontend("/", directory=frontend_dist)
