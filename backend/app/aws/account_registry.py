"""Client for the internal account registry (service 1).

The real response format is not known yet (DESIGN 15). The assumed format is a JSON array of
objects; everything that depends on it lives in `RegistryAccount`, so adapting to the real
format means editing that one model (and, if the service paginates, `fetch_accounts`).
"""

import logging
import time
from collections.abc import Callable
from typing import Literal, Self

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.core.config import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class RegistryError(Exception):
    """The registry could not be read (error_code `registry_error`)."""


class RegistryAccount(BaseModel):
    """One account as the registry describes it (assumed format)."""

    account_id: str = Field(pattern=r"^\d{12}$")
    account_name: str
    account_type: Literal["dev", "preprod", "prod"]
    eim_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    eim_app: str | None = None
    ba_id: str | None = None


_ACCOUNT_LIST = TypeAdapter(list[RegistryAccount])


class AccountRegistryClient:
    def __init__(
        self,
        url: str,
        *,
        client: httpx.Client | None = None,
        retries: int = 3,
        backoff: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url
        self.client = client or httpx.Client(timeout=httpx.Timeout(10.0))
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        if not settings.ACCOUNT_REGISTRY_URL:
            raise RegistryError("ACCOUNT_REGISTRY_URL is not set")
        return cls(settings.ACCOUNT_REGISTRY_URL)

    def fetch_accounts(self) -> list[RegistryAccount]:
        """Fetch the full account list. 429, 5xx and network errors are retried with
        exponential backoff (2 s, 4 s, 8 s by default); anything else fails at once."""
        body = self._get_with_retries()
        try:
            return _ACCOUNT_LIST.validate_json(body)
        except ValidationError as exc:
            raise RegistryError(f"unexpected registry response: {exc}") from exc

    def _get_with_retries(self) -> bytes:
        failure = "no attempt made"
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(self.backoff * 2 ** (attempt - 1))
            try:
                response = self.client.get(self.url)
            except httpx.TransportError as exc:
                failure = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code in RETRYABLE_STATUSES:
                    failure = f"HTTP {response.status_code}"
                elif response.is_success:
                    return response.content
                else:
                    raise RegistryError(f"registry answered HTTP {response.status_code}")
            logger.warning("account registry request failed", extra={"attempt": attempt + 1})
        raise RegistryError(f"registry unavailable after {self.retries + 1} attempts: {failure}")
