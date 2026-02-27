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
WS   /ws/subscribe/{team_id}   Agent WebSocket subscription endpoint.
                                Auth: Bearer token (agent_id:secret).
GET  /health                    Redis ping + internal state.
GET  /metrics                   Prometheus metrics.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    # TODO (Phase 3): connect to Redis, create consumer groups, start reclaim + trim tasks
    yield
    # TODO (Phase 3): graceful shutdown — flush pending


app = FastAPI(
    title="AIAT Message Router",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    # TODO (Phase 3): ping Redis, return connection state
    return {"status": "ok"}
