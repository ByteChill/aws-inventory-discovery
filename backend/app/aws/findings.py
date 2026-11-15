"""Client for the internal findings service (service 3).

The real response format is not known yet (DESIGN section 15). The assumed format is one JSON
object per image:

    GET {FINDINGS_SERVICE_URL}/ami/{imageId}/findings
    {"counts": {"critical": 1, "high": 2, "medium": 0, "low": 3}, "maxScore": 8.4}

Severity comes from the service: the worker stores the four counts and the highest score as
they are, and never re-bands scores itself. Everything that depends on the format lives in
`FindingLevels`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class FindingsError(Exception):
    """The findings service could not be read for an image."""


class FindingsUnavailable(FindingsError):
    """The service itself is unreachable or keeps failing, not just one image."""


class Counts(BaseModel):
    """All four levels are required: a response that leaves one out is not "clean"."""

    model_config = ConfigDict(extra="forbid")

    critical: int = Field(ge=0)
    high: int = Field(ge=0)
    medium: int = Field(ge=0)
    low: int = Field(ge=0)


class FindingLevels(BaseModel):
    """The assumed shape of one image's findings (see the module docstring)."""

    counts: Counts
    max_score: float | None = Field(default=None, alias="maxScore")


class FindingsService:
    def __init__(
        self,
        url: str,
        *,
        client: httpx.Client | None = None,
        attempts: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=httpx.Timeout(10.0))
        self.attempts = attempts
        self.backoff = backoff
        self.sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        if not settings.FINDINGS_SERVICE_URL:
            raise FindingsError("FINDINGS_SERVICE_URL is not set")
        return cls(settings.FINDINGS_SERVICE_URL)

    def fetch(self, image_id: str) -> FindingLevels:
        """The current findings of one image. 429 and 5xx are retried; any other answer or a
        malformed body raises `FindingsError`. Repeated transport or server failures raise
        `FindingsUnavailable`, so a caller can stop asking the rest of a page."""
        delay = self.backoff
        last = "no attempt made"
        unavailable = False
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.client.get(f"{self.url}/ami/{image_id}/findings")
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                unavailable = True
            else:
                if response.status_code == 200:
                    try:
                        return FindingLevels.model_validate_json(response.content)
                    except ValidationError as exc:
                        raise FindingsError(f"unexpected findings response: {exc}") from exc
                last = f"HTTP {response.status_code}"
                unavailable = response.status_code in RETRYABLE_STATUSES
                if response.status_code not in RETRYABLE_STATUSES:
                    break
            if attempt < self.attempts:
                self.sleep(delay)
                delay *= 2
        if unavailable:
            raise FindingsUnavailable(f"{image_id}: {last}")
        raise FindingsError(f"{image_id}: {last}")
