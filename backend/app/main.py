from fastapi import FastAPI

from app.api.main import api_router
from app.core.config import get_settings
from app.core.logging import setup_logging

setup_logging()

app = FastAPI(title="aws-inventory-discovery", openapi_url="/api/v1/openapi.json")
app.include_router(api_router, prefix="/api/v1")

# Registered last so the API routes win; the dashboard is absent in plain dev runs.
frontend_dist = get_settings().frontend_dist
if frontend_dist.is_dir():
    app.frontend("/", directory=frontend_dist)
