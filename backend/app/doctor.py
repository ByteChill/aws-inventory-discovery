"""`python -m app.cli doctor`: can this deployment reach everything it needs?

Checks the database (and that its migrations are at head), Redis, the account registry (1),
the credential service (2) and, for every region in `REACHABLE_REGIONS`, the Config, Cloud
Control and EC2 endpoints. Endpoints come from boto3's own resolution, so the local
`AWS_ENDPOINT_URL*` variables are honoured. An endpoint is fine when it accepts a connection
(TLS included) and answers HTTP; whether the call is authorised is not tested here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import boto3
import httpx
import redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from botocore.exceptions import BotoCoreError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from app.aws.account_registry import AccountRegistryClient, RegistryError
from app.aws.credentials import CredentialError, CredentialService
from app.core.config import Settings
from app.core.db import make_engine
from app.models import Account

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
ENDPOINT_SERVICES = {"config": "config", "cloudcontrol": "cloudcontrol", "ec2": "ec2"}
TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Check:
    name: str
    target: str
    ok: bool
    detail: str


def _check(name: str, target: str, probe: Callable[[], str]) -> Check:
    """Run a probe that returns a detail line and raises on failure."""
    try:
        return Check(name, target, True, probe())
    except (
        SQLAlchemyError,
        redis.RedisError,
        RegistryError,
        CredentialError,
        httpx.HTTPError,
        BotoCoreError,
        RuntimeError,
        OSError,
    ) as exc:
        return Check(name, target, False, f"{type(exc).__name__}: {exc}"[:200])


def database(settings: Settings) -> Check:
    def probe() -> str:
        engine = make_engine(settings.DATABASE_URL, settings.DATABASE_SCHEMA)
        try:
            with Session(engine) as session:
                version = (
                    session.connection()
                    .execute(text("SELECT version_num FROM alembic_version"))
                    .scalar()
                )
        finally:
            engine.dispose()
        head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
        if version != head:
            raise RuntimeError(f"migrations at {version}, head is {head}: run alembic upgrade head")
        return f"migrations at head ({head})"

    return _check("database", settings.DATABASE_SCHEMA, probe)


def redis_check(settings: Settings) -> Check:
    def probe() -> str:
        if not settings.REDIS_URL:
            return "REDIS_URL not set: caching is off"
        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=TIMEOUT_SECONDS)
        try:
            client.ping()
        finally:
            client.close()
        return "ping ok"

    return _check("redis", "REDIS_URL", probe)


def registry(settings: Settings) -> Check:
    """The registry is an optional import source: unavailable is informational, not a failure."""
    if not settings.ACCOUNT_REGISTRY_URL:
        return Check(
            "account registry (1)",
            "ACCOUNT_REGISTRY_URL",
            True,
            "not configured (optional)",
        )
    url = settings.ACCOUNT_REGISTRY_URL

    def probe() -> str:
        accounts = AccountRegistryClient(url, retries=0).fetch_accounts()
        return f"{len(accounts)} accounts"

    check = _check("account registry (1)", "ACCOUNT_REGISTRY_URL", probe)
    if not check.ok:
        return Check(
            check.name,
            check.target,
            True,
            f"optional import source unavailable: {check.detail}",
        )
    return check


def credentials(settings: Settings) -> Check:
    def probe() -> str:
        engine = make_engine(settings.DATABASE_URL, settings.DATABASE_SCHEMA)
        try:
            with Session(engine) as session:
                account_id = session.exec(
                    select(Account.account_id)
                    .where(col(Account.active))
                    .order_by(col(Account.account_id))
                    .limit(1)
                ).first()
        finally:
            engine.dispose()
        if account_id is None:
            return "no active account configured"
        if not settings.CREDENTIAL_SERVICE_URL:
            raise RuntimeError("CREDENTIAL_SERVICE_URL is not set")
        service = CredentialService(settings.CREDENTIAL_SERVICE_URL, attempts=1)
        service.fetch(account_id)  # the credentials themselves are dropped, never shown
        return f"credentials issued for {account_id}"

    return _check("credential service (2)", "CREDENTIAL_SERVICE_URL", probe)


def endpoint(service: str, region: str) -> Check:
    def probe() -> str:
        client = boto3.client(
            service,
            region_name=region,
            aws_access_key_id="doctor",
            aws_secret_access_key="doctor",
        )
        url = client.meta.endpoint_url
        response = httpx.get(url, timeout=TIMEOUT_SECONDS)
        return f"{url} answered HTTP {response.status_code}"

    return _check(f"{service} endpoint", region, probe)


def run_checks(settings: Settings) -> list[Check]:
    registry_check = registry(settings)
    checks = [
        database(settings),
        redis_check(settings),
        registry_check,
        credentials(settings),
    ]
    for region in settings.REACHABLE_REGIONS:
        checks.extend(endpoint(service, region) for service in ENDPOINT_SERVICES.values())
    return checks


def format_table(checks: list[Check]) -> str:
    rows = [("check", "target", "result", "detail")]
    rows += [(c.name, c.target, "ok" if c.ok else "FAIL", c.detail) for c in checks]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:3])) + "  " + row[3]
        for row in rows
    ]
    return "\n".join(lines)
