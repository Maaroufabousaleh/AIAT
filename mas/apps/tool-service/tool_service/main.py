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

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import prometheus_client
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response

from mas_core.observability import configure_logging
from mas_core.observability.metrics import TOOL_ERRORS_TOTAL, TOOL_INVOCATIONS_TOTAL

from .cache import ToolCache
from .config import Settings, get_settings
from .rate_limiter import RateLimiterPool
from .registry import ToolRegistry
from .routes import router
from .tools.all_tools import get_all_tools

logger = logging.getLogger(__name__)

_CACHE_RECONNECT_INTERVAL_SECONDS = 5.0
_USAGE_RECONNECT_INTERVAL_SECONDS = 5.0


async def _connect_cache(settings: Settings) -> tuple[aioredis.Redis, ToolCache]:
    """Create and verify the Redis client used by the tool-result cache."""
    redis_client = aioredis.from_url(
        settings.redis_url,
        username=settings.redis_username,
        password=settings.redis_password,
        decode_responses=True,
    )
    try:
        await redis_client.ping()
    except Exception:
        await redis_client.aclose()
        raise
    return redis_client, ToolCache(redis_client)


async def _recover_cache(
    app: FastAPI,
    registry: ToolRegistry,
    settings: Settings,
) -> None:
    """Keep retrying a failed cache connection without restarting the service."""
    while True:
        await asyncio.sleep(_CACHE_RECONNECT_INTERVAL_SECONDS)
        cache = getattr(app.state, "cache", None)
        if cache is not None and await cache.healthcheck():
            continue

        stale_redis = getattr(app.state, "redis", None)
        if stale_redis is not None:
            await stale_redis.aclose()

        app.state.cache = None
        app.state.redis = None
        registry.set_cache(None)

        try:
            redis_client, cache = await _connect_cache(settings)
        except Exception:
            logger.warning("Redis cache reconnect failed", exc_info=True)
            continue

        app.state.redis = redis_client
        app.state.cache = cache
        registry.set_cache(cache)
        logger.info("Redis cache connection recovered")


async def _connect_usage(settings: Settings):  # noqa: ANN202
    """Create the project usage writer without making startup depend on Postgres."""
    from .usage import ProjectUsageWriter

    return await ProjectUsageWriter.connect(settings.pgbouncer_dsn)


async def _recover_usage(
    app: FastAPI,
    registry: ToolRegistry,
    settings: Settings,
) -> None:
    """Reconnect durable usage accounting after startup or runtime DB outages."""
    while True:
        await asyncio.sleep(_USAGE_RECONNECT_INTERVAL_SECONDS)
        current = getattr(app.state, "usage_storage", None)
        if current is not None:
            try:
                if await current.healthcheck():
                    continue
            except Exception:
                logger.warning("Project usage storage healthcheck failed", exc_info=True)
            with suppress(Exception):
                await current.close()
            app.state.usage_storage = None
            registry.set_usage_storage(None)

        try:
            usage_storage = await _connect_usage(settings)
        except Exception:
            logger.warning("Project usage storage reconnect failed", exc_info=True)
            continue

        app.state.usage_storage = usage_storage
        registry.set_usage_storage(usage_storage)
        logger.info("Project usage storage connection recovered")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start-up: Redis, cache, rate limiter, registry.  Shutdown: clean up."""
    settings = get_settings()
    configure_logging("tool-service", json=settings.log_level != "DEBUG")

    redis_client: aioredis.Redis | None = None
    cache: ToolCache | None = None
    usage_storage = None

    try:
        redis_client, cache = await _connect_cache(settings)
        logger.info("Redis connected (tool-cache DB 1)")
    except Exception:
        logger.warning("Redis unavailable — cache disabled", exc_info=True)
        redis_client = None
        cache = None

    rate_limiter = RateLimiterPool()

    if settings.pgbouncer_dsn:
        try:
            usage_storage = await _connect_usage(settings)
            logger.info("Project usage storage connected")
        except Exception:
            logger.warning("Project usage storage unavailable", exc_info=True)
            usage_storage = None

    try:
        from .tools.browser import close_browser_pool
    except ModuleNotFoundError:  # pragma: no cover - optional local dependency

        async def close_browser_pool() -> None:
            return None

    from .tools.infra import close_blob_client
    from .tools.memory import close_shared_memory_redis

    registry = ToolRegistry(
        settings,
        cache=cache,
        rate_limiter=rate_limiter,
        usage_storage=usage_storage,
    )
    registry.register_all(get_all_tools())
    TOOL_INVOCATIONS_TOTAL.labels(tool_name="_startup", status="success").inc(0)
    TOOL_ERRORS_TOTAL.labels(tool_name="_startup", error_code="none").inc(0)
    logger.info("Registered %d tools", len(registry.tool_names))

    app.state.settings = settings
    app.state.registry = registry
    app.state.cache = cache
    app.state.redis = redis_client
    app.state.usage_storage = usage_storage

    cache_recovery_task = asyncio.create_task(
        _recover_cache(app, registry, settings),
        name="tool-cache-recovery",
    )
    usage_recovery_task = None
    if settings.pgbouncer_dsn:
        usage_recovery_task = asyncio.create_task(
            _recover_usage(app, registry, settings),
            name="project-usage-recovery",
        )

    yield

    cache_recovery_task.cancel()
    with suppress(asyncio.CancelledError):
        await cache_recovery_task
    if usage_recovery_task is not None:
        usage_recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await usage_recovery_task

    await close_browser_pool()
    await close_blob_client()
    await close_shared_memory_redis()

    active_usage_storage = getattr(app.state, "usage_storage", None)
    if active_usage_storage is not None:
        await active_usage_storage.close()

    active_redis = getattr(app.state, "redis", None)
    if active_redis:
        await active_redis.aclose()
        logger.info("Redis connection closed")


app = FastAPI(
    title="AIAT Tool Service",
    version="0.6.0",
    lifespan=lifespan,
)

app.include_router(router)

_prom_app = prometheus_client.make_asgi_app()


@app.get("/metrics", tags=["observability"])
async def prometheus_metrics(request: Request) -> Response:
    """Expose Prometheus metrics at /metrics."""
    scope = dict(request.scope)
    scope["path"] = "/"
    body_parts: list[bytes] = []
    status_code = 200
    resp_headers: list[tuple[bytes, bytes]] = []

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b""}

    async def send(msg: dict) -> None:  # noqa: ANN001
        nonlocal status_code, resp_headers
        if msg["type"] == "http.response.start":
            status_code = msg["status"]
            resp_headers = msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            body_parts.append(msg.get("body", b""))

    await _prom_app(scope, receive, send)
    return Response(
        content=b"".join(body_parts),
        status_code=status_code,
        headers={k.decode(): v.decode() for k, v in resp_headers},
    )
