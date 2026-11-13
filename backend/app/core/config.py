import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/core/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All configuration comes from environment variables (DESIGN 12.3)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    DATABASE_READ_URL: str | None = None
    DATABASE_SCHEMA: str
    REDIS_URL: str | None = None
    # How long cached facets and overview numbers stay valid. Entries are written with a
    # slightly shorter random TTL so they do not all expire on the same second.
    CACHE_TTL_SECONDS: int = Field(default=60, ge=1)

    ACCOUNT_REGISTRY_URL: str | None = None
    CREDENTIAL_SERVICE_URL: str | None = None

    REACHABLE_REGIONS: Annotated[list[str], NoDecode] = [
        "ap-east-1",
        "us-east-1",
        "eu-west-1",
        "eu-west-2",
    ]

    SWEEP_ENABLED: bool = True
    SWEEP_INTERVAL_HOURS: int = 6

    WORKER_THREADS: int = 16
    WORKER_ONDEMAND_THREADS: int = 4
    PER_ACCOUNT_CONCURRENCY: int = 4
    MAX_SCAN_ACCOUNTS: int = 50

    CONFIG_LAG_GRACE_MINUTES: int = 15
    MISSING_GUARD_RATIO: float = 0.5
    MISSING_GUARD_MIN: int = 20

    TASK_RETENTION_DAYS: int = 14
    REQUEST_RETENTION_DAYS: int = 90
    DELETED_PURGE_DAYS: int = 30
    INACTIVE_ACCOUNT_PURGE_DAYS: int = 30

    WORKER_LOG_GROUP: str = "/aws-inventory-discovery/worker"
    # Touched by the worker every 30 s; `python -m app.cli healthcheck` reads its age.
    WORKER_HEARTBEAT_FILE: Path = Path("/tmp/aws-inventory-discovery-worker.heartbeat")  # noqa: S108

    # boto3 reads these two natively; they are only ever set in the local environment.
    AWS_ENDPOINT_URL: str | None = None
    AWS_ENDPOINT_URL_CLOUDCONTROL: str | None = None

    # Where the built dashboard lives; unset means "look in the usual places".
    FRONTEND_DIST: Path | None = None
    # Path prefix everything is served under, e.g. "/aws-inventory" behind the shared ALB; empty
    # means the root. The image sets it from the same build argument the dashboard is built with.
    BASE_PATH: str = ""

    @field_validator("REACHABLE_REGIONS", mode="before")
    @classmethod
    def split_regions(cls, value: object) -> object:
        if isinstance(value, str):
            return [region.strip() for region in value.split(",") if region.strip()]
        return value

    @field_validator("DATABASE_SCHEMA")
    @classmethod
    def check_schema(cls, value: str) -> str:
        # The schema goes into search_path, so keep it a plain identifier.
        if not value.replace("_", "").isalnum():
            raise ValueError("DATABASE_SCHEMA must be a plain identifier")
        return value

    @field_validator("BASE_PATH")
    @classmethod
    def check_base_path(cls, value: str) -> str:
        # It becomes part of every route and of the dashboard's URLs: plain path segments only.
        value = value.rstrip("/")
        if value and not re.fullmatch(r"(/[A-Za-z0-9_-]+)+", value):
            raise ValueError('BASE_PATH must look like "/aws-inventory"')
        return value

    @property
    def database_read_url(self) -> str:
        return self.DATABASE_READ_URL or self.DATABASE_URL

    @property
    def frontend_dist(self) -> Path:
        if self.FRONTEND_DIST is not None:
            return self.FRONTEND_DIST
        image = Path("/app/frontend/dist")
        return image if image.exists() else REPO_ROOT / "frontend" / "dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()
