import json
import logging
from typing import Any, Self

import redis

logger = logging.getLogger(__name__)

PREFIX = "aws-inventory-discovery:"


class Cache:
    """A JSON cache in Redis. Redis being down is a miss, never an error (it only costs speed)."""

    def __init__(self, client: redis.Redis | None) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str | None) -> Self:
        if url is None:
            return cls(None)
        return cls(redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1))

    def get(self, key: str) -> Any | None:
        if self._client is None:
            return None
        try:
            raw = self._client.get(key)
        except (redis.RedisError, OSError) as exc:
            logger.warning("cache read failed: %s", exc)
            return None
        return None if raw is None else json.loads(raw)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if self._client is None:
            return
        try:
            self._client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        except (redis.RedisError, OSError) as exc:
            logger.warning("cache write failed: %s", exc)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
