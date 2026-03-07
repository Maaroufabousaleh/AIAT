"""
tool-service — FastAPI application.

Responsibilities
----------------
• Expose a single tool-execution endpoint, gated by AgentRole.
• Enforce (sender_role, tool_name) permission matrix from CommunicationPolicy.
• Per-tool-group token-bucket rate limiting (aiolimiter).
• Per-tool asyncio.Semaphore concurrency cap.
• Result cache: hash(tool_name + sorted(kwargs)) → Redis tool_cache:{hash}, 30 s TTL.
• Per-tool circuit breaker: ≥3 failures in 60 s → OPEN for 120 s → HALF_OPEN probe.

Tool groups (Phase 6, canonical 7 groups)
-----------------------------------------
workflow, document, review, sprint_issue, devops, capability, kpi_utility

Endpoints (Phase 6)
-------------------
POST /tools/{tool_name}/run
                        Execute a tool. Body: ToolRequest. Returns ToolResponse.
POST /tools/execute     Backward-compatible execution endpoint.
GET  /tools             List all tools with role requirements.
GET  /health            Service health + circuit-breaker states.
GET  /metrics           Prometheus metrics.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI

from .cache import ToolCache
from .config import Settings, get_settings
from .rate_limiter import RateLimiterPool
from .registry import ToolRegistry
from .routes import router
from .tools.all_tools import get_all_tools

logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    """Set up structlog + stdlib logging."""
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start-up: Redis, cache, rate limiter, registry.  Shutdown: clean up."""
    settings = get_settings()
    _configure_logging(settings.log_level)

    # ── Redis ──────────────────────────────────────────────────────────────
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

    # ── Rate limiter ───────────────────────────────────────────────────────
    rate_limiter = RateLimiterPool()

    # ── Registry ───────────────────────────────────────────────────────────
    registry = ToolRegistry(settings, cache=cache, rate_limiter=rate_limiter)
    registry.register_all(get_all_tools())
    logger.info("Registered %d tools", len(registry.tool_names))

    # ── Stash on app.state ─────────────────────────────────────────────────
    app.state.settings = settings
    app.state.registry = registry
    app.state.cache = cache
    app.state.redis = redis_client

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    if redis_client:
        await redis_client.aclose()
        logger.info("Redis connection closed")


app = FastAPI(
    title="AIAT Tool Service",
    version="0.6.0",
    lifespan=lifespan,
)

app.include_router(router)

# ── Prometheus metrics (optional) ──────────────────────────────────────────
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator().instrument(app).expose(app)
except ImportError:
    logger.debug("prometheus-fastapi-instrumentator not installed — metrics disabled")


