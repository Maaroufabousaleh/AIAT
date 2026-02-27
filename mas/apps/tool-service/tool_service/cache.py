"""Redis-backed tool result cache.

Cache key format: ``tool_cache:{sha256(tool_name + sorted(kwargs))}``

The ``toolcache_user`` Redis ACL user is restricted to the ``tool_cache:*``
key pattern on DB 1.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


def _cache_key(tool_name: str, kwargs: dict[str, Any]) -> str:
    """Derive a deterministic cache key from tool name + kwargs."""
    payload = json.dumps({"t": tool_name, "k": kwargs}, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"tool_cache:{digest}"


class ToolCache:
    """Async Redis cache for tool results.

    Parameters
    ----------
    redis : aioredis.Redis
        Connected Redis client (DB 1, toolcache_user).
    default_ttl : int
        Default TTL in seconds for cached results (overridden per-tool).
    """

    def __init__(self, redis_client: aioredis.Redis, *, default_ttl: int = 30) -> None:
        self._redis = redis_client
        self._default_ttl = default_ttl

    async def get(self, tool_name: str, kwargs: dict[str, Any]) -> Any | None:
        """Return cached result or ``None`` if miss."""
        key = _cache_key(tool_name, kwargs)
        try:
            raw = await self._redis.get(key)
        except Exception:
            logger.warning("cache_get_error", extra={"key": key}, exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        result: Any,
        ttl: int | None = None,
    ) -> None:
        """Store a result in the cache with a TTL."""
        ttl = ttl if ttl is not None else self._default_ttl
        if ttl <= 0:
            return
        key = _cache_key(tool_name, kwargs)
        try:
            raw = json.dumps(result, default=str)
            await self._redis.setex(key, ttl, raw)
        except Exception:
            logger.warning("cache_set_error", extra={"key": key}, exc_info=True)

    async def invalidate(self, tool_name: str, kwargs: dict[str, Any]) -> None:
        """Remove a specific entry from the cache."""
        key = _cache_key(tool_name, kwargs)
        try:
            await self._redis.delete(key)
        except Exception:
            logger.warning("cache_invalidate_error", extra={"key": key}, exc_info=True)

    async def healthcheck(self) -> bool:
        """Return ``True`` if Redis is reachable."""
        try:
            return await self._redis.ping()
        except Exception:
            return False
