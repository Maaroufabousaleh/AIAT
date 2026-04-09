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

Endpoints
---------
POST /messages/publish          Publish a MessageEnvelope to a team stream.
                                Returns { entry_id } or { deduplicated: true }.
POST /messages/broadcast        Fan-out to ALL 11 team streams (SHUTDOWN, etc.).
WS   /ws/subscribe/{team_id}   Agent WebSocket subscription endpoint.
                                Auth: Bearer {agent_id}:{secret}.
GET  /health                    Redis ping + internal state.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import prometheus_client
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import Response

from mas_core.observability import configure_logging

from .config import settings
from .dlq import close_pool
from .redis_client import close_redis, connect_redis, ensure_all_consumer_groups
from .routes_publish import router as publish_router
from .routes_ws import router as ws_router
from .tasks import reclaim_loop, trim_loop

configure_logging("message-router", json=os.environ.get("LOG_FORMAT") != "console")

logger = structlog.stdlib.get_logger(__name__)

_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    """Startup: connect Redis, ensure consumer groups, launch background tasks.
    Shutdown: cancel tasks, close connections.
    """
    logger.info("message-router starting up…")

    await connect_redis()
    await ensure_all_consumer_groups()

    reclaim_task = asyncio.create_task(reclaim_loop(), name="reclaim-loop")
    trim_task = asyncio.create_task(trim_loop(), name="trim-loop")
    _background_tasks.extend([reclaim_task, trim_task])

    logger.info("message-router ready.")

    yield

    logger.info("message-router shutting down…")

    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

    await close_redis()
    await close_pool()

    logger.info("message-router stopped.")


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

app.include_router(publish_router, tags=["publish"])
app.include_router(ws_router, tags=["subscribe"])

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


@app.get("/health", tags=["health"])
async def health() -> dict[str, object]:
    """Redis ping + internal state."""
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
        "background_tasks_running": sum(1 for t in _background_tasks if not t.done()),
    }
