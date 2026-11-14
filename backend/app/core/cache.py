import json
import logging
import random
from typing import Any, Self

import redis
from pydantic import TypeAdapter, ValidationError

logger = logging.getLogger(__name__)

PREFIX = "aws-inventory-discovery:"
DEFAULT_TTL_SECONDS = 60
# One pool per process (the API serves requests from a thread pool), not one connection per request.
MAX_CONNECTIONS = 32


class Cache:
    """A JSON cache in Redis. Redis being down is a miss, never an error (it only costs speed).

    Every key is `aws-inventory-discovery:<DATABASE_SCHEMA>:<name>` (`key()`), so deployments
    (or a test run and a local stack) that share one Redis never read each other's data.

    One instance is built per app (`create_app`) and reused by every request, so requests share
    its connection pool instead of connecting and authenticating to Redis again each time."""

    def __init__(
        self,
        client: redis.Redis | None,
        schema: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._client = client
        self.schema = schema
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_url(
        cls, url: str | None, schema: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> Self:
        if not url:  # unset or empty: no cache at all, so a blank variable does not stop start-up
            return cls(None, schema, ttl_seconds=ttl_seconds)
        try:
            client = redis.Redis.from_url(
                url,
                socket_connect_timeout=1,
                socket_timeout=1,
                health_check_interval=30,
                max_connections=MAX_CONNECTIONS,
                client_name="aws-inventory-discovery",
            )
        except ValueError:
            # A malformed URL (usually an AUTH token that was not URL-encoded) costs speed, not
            # service, the same as Redis being down (D-03). The URL holds the token: never log it.
            logger.error("REDIS_URL cannot be parsed, running without a cache")
            return cls(None, schema, ttl_seconds=ttl_seconds)
        return cls(client, schema, ttl_seconds=ttl_seconds)

    def key(self, *parts: str) -> str:
        return f"{PREFIX}{self.schema}:{':'.join(parts)}"

    def ping(self) -> bool:
        """Whether Redis answers, for `/health`. A failure is "unavailable", not an error."""
        if self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except (redis.RedisError, OSError) as exc:
            logger.warning("cache ping failed: %s", exc)
            return False

    def get(self, key: str) -> Any | None:
        return self.get_many([key])[0]

    def get_many(self, keys: list[str]) -> list[Any | None]:
        """The values of `keys`, None for a miss, in one round trip (pipeline, not MGET:
        each key is its own command, so this also works on a cluster-mode cache)."""
        if self._client is None or not keys:
            return [None] * len(keys)
        try:
            pipeline = self._client.pipeline(transaction=False)
            for key in keys:
                pipeline.get(key)
            raw_values = pipeline.execute()
        except (redis.RedisError, OSError) as exc:
            logger.warning("cache read failed: %s", exc)
            return [None] * len(keys)
        values = [self._load(key, raw) for key, raw in zip(keys, raw_values, strict=True)]
        for key, value in zip(keys, values, strict=True):
            logger.debug("cache %s %s", "hit" if value is not None else "miss", key)
        return values

    @staticmethod
    def _load(key: str, raw: Any) -> Any | None:
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            logger.warning("cache value for %s is not JSON: %s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        self.set_many({key: value}, ttl_seconds=ttl_seconds)

    def set_many(self, entries: dict[str, Any], ttl_seconds: int | None = None) -> None:
        """Write `entries` in one round trip, each with its own slightly jittered TTL."""
        if self._client is None or not entries:
            return
        try:
            pipeline = self._client.pipeline(transaction=False)
            for key, value in entries.items():
                pipeline.set(
                    key,
                    json.dumps(value, ensure_ascii=False),
                    ex=self._jittered(ttl_seconds),
                )
            pipeline.execute()
        except (redis.RedisError, OSError) as exc:
            logger.warning("cache write failed: %s", exc)

    def _jittered(self, ttl_seconds: int | None) -> int:
        ttl = max(1, self.ttl_seconds if ttl_seconds is None else ttl_seconds)
        # Only ever shorter, so an entry never outlives the configured TTL. Enough to keep
        # entries written together from all expiring on the same second.
        return ttl - random.randint(0, ttl // 10)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def validated[T](adapter: TypeAdapter[T], raw: Any) -> T | None:
    """A cached value read back as `T`, or None (a miss) if it no longer has that shape — the
    one thing a rolling deploy can leave behind is an entry written under the previous model."""
    if raw is None:
        return None
    try:
        return adapter.validate_python(raw)
    except ValidationError as exc:
        logger.warning("cache value does not match its model, treating it as a miss: %s", exc)
        return None
