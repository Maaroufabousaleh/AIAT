"""
tool-service — FastAPI application.

Responsibilities
----------------
• Expose a single tool-execution endpoint, gated by AgentRole.
• Enforce (sender_role, tool_name) permission matrix from CommunicationPolicy.
• Per-tool-group token-bucket rate limiting.
• Per-tool asyncio.Semaphore concurrency cap.
• Result cache: hash(tool_name + sorted(kwargs)) → Redis tool_cache:{hash}, 30 s TTL.
• Per-tool circuit breaker: failures in window → OPEN → HALF_OPEN probe.

Tool groups (canonical 7)
-------------------------
workflow, document, review, sprint_issue, devops, capability, kpi_utility

Endpoints
---------
POST /tools/{tool_name}/run   Execute a tool. Body: ToolRequest. Returns ToolResponse.
POST /tools/execute           Backward-compatible execution endpoint.
GET  /tools                   List all tools with role requirements.
GET  /health                  Service health + circuit-breaker states.
GET  /metrics                 Prometheus metrics.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI

from mas_core.observability import configure_logging
from mas_core.observability.metrics import TOOL_ERRORS_TOTAL, TOOL_INVOCATIONS_TOTAL

from .cache import ToolCache
from .config import get_settings
from .rate_limiter import RateLimiterPool
from .registry import ToolRegistry
from .routes import router
from .tools.all_tools import get_all_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start-up: Redis, cache, rate limiter, registry.  Shutdown: clean up."""
    settings = get_settings()
    configure_logging("tool-service", json=settings.log_level != "DEBUG")

    redis_client: aioredis.Redis | None = None
    cache: ToolCache | None = None

    try:
        redis_client = aioredis.from_url(
            settings.redis_url,
            username=settings.redis_username,
            password=settings.redis_password,
            decode_responses=True,
        )
        await redis_client.ping()
        cache = ToolCache(redis_client)
        logger.info("Redis connected (tool-cache DB 1)")
    except Exception:
        logger.warning("Redis unavailable — cache disabled", exc_info=True)
        redis_client = None
        cache = None

    rate_limiter = RateLimiterPool()

    try:
        from .tools.browser import close_browser_pool
    except ModuleNotFoundError:  # pragma: no cover - optional local dependency

        async def close_browser_pool() -> None:
            return None

    from .tools.infra import close_blob_client
    from .tools.memory import close_shared_memory_redis

    registry = ToolRegistry(settings, cache=cache, rate_limiter=rate_limiter)
    registry.register_all(get_all_tools())
    TOOL_INVOCATIONS_TOTAL.labels(tool_name="_startup", status="success").inc(0)
    TOOL_ERRORS_TOTAL.labels(tool_name="_startup", error_code="none").inc(0)
    logger.info("Registered %d tools", len(registry.tool_names))

    app.state.settings = settings
    app.state.registry = registry
    app.state.cache = cache
    app.state.redis = redis_client

    yield

    await close_browser_pool()
    await close_blob_client()
    await close_shared_memory_redis()

    if redis_client:
        await redis_client.aclose()
        logger.info("Redis connection closed")


app = FastAPI(
    title="AIAT Tool Service",
    version="0.6.0",
    lifespan=lifespan,
)

app.include_router(router)

try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator().instrument(app).expose(app)
except ImportError:
    logger.debug("prometheus-fastapi-instrumentator not installed — metrics disabled")
