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


def create_app() -> FastAPI:
    settings = get_settings()
    # Everything is served under BASE_PATH (e.g. "/aws-inventory" behind the shared ALB, which
    # forwards the path unchanged); empty means the root. The dashboard is built with the same
    # prefix (Vite `base`, see backend/Dockerfile).
    prefix = settings.BASE_PATH
    app = FastAPI(
        title="aws-inventory-discovery",
        openapi_url=f"{prefix}/api/v1/openapi.json",
        docs_url=f"{prefix}/docs",
        redoc_url=f"{prefix}/redoc",
        swagger_ui_oauth2_redirect_url=f"{prefix}/docs/oauth2-redirect",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(api_router, prefix=f"{prefix}/api/v1")
    # Registered last so the API routes win; the dashboard is absent in plain dev runs.
    if settings.frontend_dist.is_dir():
        app.frontend(prefix or "/", directory=settings.frontend_dist, fallback="index.html")
    return app


app = create_app()
