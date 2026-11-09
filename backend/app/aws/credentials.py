"""Client for the credential service (2) and the botocore hook that refreshes from it.

The service is assumed to answer an STS-shaped JSON document (DESIGN section 15):
`{AccessKeyId, SecretAccessKey, SessionToken, Expiration}`. Credentials live only in memory.
They are never logged, and error messages never contain them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Self

import httpx
from botocore.credentials import RefreshableCredentials

from app.core.config import Settings, get_settings

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class CredentialError(Exception):
    """The credential service failed or answered something unusable."""


@dataclass(frozen=True)
class CredentialService:
    url: str
    timeout: float = 10.0
    attempts: int = 3
    backoff: float = 0.5  # seconds, doubled after each failed attempt
    account_param: str = "account_id"

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Self:
        settings = settings or get_settings()
        if not settings.CREDENTIAL_SERVICE_URL:
            raise CredentialError("CREDENTIAL_SERVICE_URL is not set")
        return cls(settings.CREDENTIAL_SERVICE_URL)

    def fetch(self, account_id: str) -> dict[str, Any]:
        """botocore credential metadata for the account. Retries 429 and 5xx."""
        delay = self.backoff
        last = "no attempt made"
        for attempt in range(1, self.attempts + 1):
            try:
                response = httpx.get(
                    self.url, params={self.account_param: account_id}, timeout=self.timeout
                )
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            else:
                if response.status_code == 200:
                    return self._parse(response)
                last = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUSES:
                    break
            if attempt < self.attempts:
                time.sleep(delay)
                delay *= 2
        raise CredentialError(f"credential service failed for account {account_id}: {last}")

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        try:
            document = response.json()
            return {
                "access_key": document["AccessKeyId"],
                "secret_key": document["SecretAccessKey"],
                "token": document["SessionToken"],
                "expiry_time": document["Expiration"],
            }
        except ValueError, KeyError, TypeError:
            raise CredentialError("credential service returned an unexpected document") from None

    def credentials_for(self, account_id: str) -> RefreshableCredentials:
        """botocore's own refreshable credentials; boto3 calls `fetch` again before expiry."""
        return RefreshableCredentials.create_from_metadata(
            metadata=self.fetch(account_id),
            refresh_using=lambda: self.fetch(account_id),
            method="aws-inventory-discovery-credential-service",
        )
