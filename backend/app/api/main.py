from fastapi import APIRouter

from app.api.routes import accounts, health, metadata, resources, scans

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(resources.router)
api_router.include_router(metadata.types_router)
api_router.include_router(metadata.columns_router)
api_router.include_router(metadata.stats_router)
api_router.include_router(accounts.router)
api_router.include_router(scans.router)
api_router.include_router(scans.tasks_router)
api_router.include_router(scans.scopes_router)
