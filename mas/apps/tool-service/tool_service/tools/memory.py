"""MEMORY group tools: shared_memory_read, shared_memory_write."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ..config import get_settings

logger = logging.getLogger(__name__)

_shared_redis: aioredis.Redis | None = None


async def get_shared_memory_redis() -> aioredis.Redis:
    """Get or create shared memory Redis connection."""
    global _shared_redis
    if _shared_redis is None:
        settings = get_settings()
        redis_url = settings.redis_url.rsplit("/", 1)[0]
        shared_url = f"{redis_url}/{settings.redis_db_shared_memory}"
        _shared_redis = aioredis.from_url(
            shared_url,
            username=settings.redis_username,
            password=settings.redis_password,
            encoding="utf-8",
            decode_responses=True,
        )
    return _shared_redis


async def close_shared_memory_redis() -> None:
    """Close shared memory Redis connection."""
    global _shared_redis
    if _shared_redis:
        await _shared_redis.aclose()
        _shared_redis = None


class SharedMemoryReadTool(BaseTool):
    name = "shared_memory_read"
    group = ToolGroup.KPI_UTILITY
    description = "Read a value from the shared agent memory store."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = True
    max_concurrency = 10

    async def execute(self, **kwargs: Any) -> Any:
        key = kwargs.get("key", "")
        namespace = kwargs.get("namespace", "default")

        if not key:
            raise ValueError("key is required")

        full_key = f"shared:{namespace}:{key}"
        redis_client = await get_shared_memory_redis()

        try:
            value = await redis_client.get(full_key)
            if value is None:
                return {"key": key, "namespace": namespace, "value": None, "found": False}

            try:
                parsed = json.loads(value)
                return {"key": key, "namespace": namespace, "value": parsed, "found": True}
            except json.JSONDecodeError:
                return {"key": key, "namespace": namespace, "value": value, "found": True}
        except Exception as e:
            logger.error(
                "shared_memory_read_error", extra={"key": key, "error": str(e)}, exc_info=True
            )
            raise RuntimeError(f"Failed to read from shared memory: {e}") from e


class SharedMemoryWriteTool(BaseTool):
    name = "shared_memory_write"
    group = ToolGroup.KPI_UTILITY
    description = "Write a value to the shared agent memory store."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 5

    async def execute(self, **kwargs: Any) -> Any:
        key = kwargs.get("key", "")
        value = kwargs.get("value")
        namespace = kwargs.get("namespace", "default")
        ttl_seconds = kwargs.get("ttl_seconds", 3600)

        if not key:
            raise ValueError("key is required")
        if value is None:
            raise ValueError("value is required")

        full_key = f"shared:{namespace}:{key}"

        serialized = json.dumps(value) if isinstance(value, (dict, list)) else str(value)

        redis_client = await get_shared_memory_redis()

        try:
            if ttl_seconds > 0:
                await redis_client.setex(full_key, ttl_seconds, serialized)
            else:
                await redis_client.set(full_key, serialized)

            return {"key": key, "namespace": namespace, "written": True}
        except Exception as e:
            logger.error(
                "shared_memory_write_error", extra={"key": key, "error": str(e)}, exc_info=True
            )
            raise RuntimeError(f"Failed to write to shared memory: {e}") from e
