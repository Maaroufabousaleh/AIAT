"""
message-router — FastAPI application.

Responsibilities
----------------
• Validate and route MessageEnvelope messages between agents.
• Enforce CommunicationPolicy (role-based + chain-of-command rules).
• Back all message delivery on Redis Streams (one stream per team).
• Maintain consumer groups; run XAUTOCLAIM reclaim loop (120 s idle).
• Publish-side idempotency: dedupe:{message_id} Redis key, 300 s TTL.
• Move exhausted-retry messages to Postgres dead_letters table (DLQ).
• Trim each stream to MAXLEN ~ 50 000 every 60 s.
• Deliver messages to agents over WebSocket (WS Subscribe Protocol).

Endpoints (Phase 3)
-------------------
POST /messages/publish          Publish a MessageEnvelope to a team stream.
                                Returns { entry_id } or { deduplicated: true }.
POST /messages/broadcast        Fan-out to ALL 11 team streams (SHUTDOWN, etc.).
WS   /ws/subscribe/{team_id}   Agent WebSocket subscription endpoint.
                                Auth: Bearer {agent_id}:{secret}.
GET  /health                    Redis ping + internal state.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from .config import settings
from .dlq import close_pool
from .redis_client import close_redis, connect_redis, ensure_all_consumer_groups
from .routes_publish import router as publish_router
from .routes_ws import router as ws_router
from .tasks import reclaim_loop, trim_loop

# ---------------------------------------------------------------------------
# Logging — structlog with stdlib integration
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Background task handles
# ---------------------------------------------------------------------------

_background_tasks: list[asyncio.Task] = []


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    """Startup: connect Redis, ensure consumer groups, launch background tasks.
    Shutdown: cancel tasks, close connections.
    """
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("message-router starting up…")

    # Connect to Redis
    await connect_redis()

    # Ensure consumer groups for all 11 teams
    await ensure_all_consumer_groups()

    # Launch background tasks
    reclaim_task = asyncio.create_task(reclaim_loop(), name="reclaim-loop")
    trim_task = asyncio.create_task(trim_loop(), name="trim-loop")
    _background_tasks.extend([reclaim_task, trim_task])

    logger.info("message-router ready.")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("message-router shutting down…")

    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

    # Close connections
    await close_redis()
    await close_pool()

    logger.info("message-router stopped.")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AIAT Message Router",
    version="0.3.0",
    lifespan=lifespan,
    description=(
        "HTTP + WebSocket message router with Redis Streams backend, "
        "CommunicationPolicy enforcement, publish-side idempotency, "
        "XAUTOCLAIM reclaim, DLQ→Postgres, and stream trimming."
    ),
)

# Register routers
app.include_router(publish_router, tags=["publish"])
app.include_router(ws_router, tags=["subscribe"])


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict[str, object]:
    """Redis ping + internal state."""
    from redis.exceptions import RedisError

    from .redis_client import get_redis

    redis_ok = False
    redis_error: str | None = None
    try:
        r = get_redis()
        await r.ping()
        redis_ok = True
    except Exception as exc:
        redis_error = str(exc)

    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": "ok" if redis_ok else f"error: {redis_error}",
        "known_teams": len(settings.known_teams),
        "background_tasks": len(_background_tasks),
        "background_tasks_running": sum(
            1 for t in _background_tasks if not t.done()
        ),
    }
