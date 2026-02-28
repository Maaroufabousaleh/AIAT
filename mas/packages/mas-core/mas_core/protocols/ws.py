"""WebSocket frame models for the Message Router ↔ Agent subscribe protocol.

See org-architecture plan §3.3 for the full WS protocol specification.

Frame flow
----------
Router  →  Agent : WSMessageFrame  (delivers a MessageEnvelope from a team stream)
Agent   →  Router: WSAckFrame      (XACK — marks message delivered, removes from PEL)
Agent   →  Router: WSNackFrame     (keep in PEL, redeliver later)
Router  →  Agent : WSPingFrame     (keepalive every 15 s)
Agent   →  Router: WSPongFrame     (must arrive within 10 s or connection is dead)

All frames are JSON-serialised and sent as WebSocket text messages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field

from .envelope import MessageEnvelope


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _WSFrameBase(BaseModel):
    """Common base — every frame has a ``type`` discriminator."""

    type: str  # Overridden by each subclass as a Literal


# ---------------------------------------------------------------------------
# Router → Agent frames
# ---------------------------------------------------------------------------


class WSMessageFrame(_WSFrameBase):
    """Router delivers a MessageEnvelope to the subscribing agent.

    The ``entry_id`` is the Redis Stream entry ID; the agent echoes it back in
    WSAckFrame / WSNackFrame so the router knows which PEL entry to XACK.
    """

    type: Literal["MESSAGE"] = "MESSAGE"

    entry_id: str = Field(
        ...,
        description=(
            "Redis Stream entry ID (e.g. '1708900000000-0'). "
            "Must be echoed back verbatim in the ACK/NACK frame."
        ),
    )
    envelope: MessageEnvelope = Field(..., description="The canonical message payload.")
    stream: str = Field(
        ...,
        description="Source stream name (e.g. 'stream:exec_ceo').",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Delivery attempt count (same as envelope.retry_count; convenience copy).",
    )


class WSPingFrame(_WSFrameBase):
    """Router keepalive ping — sent every 15 s to all connected agents.

    The agent must reply with a WSPongFrame within 10 s, otherwise the router
    considers the connection dead and closes it. Pending PEL entries will be
    reclaimed by XAUTOCLAIM after 120 s idle.
    """

    type: Literal["PING"] = "PING"

    ping_id: str = Field(
        default_factory=lambda: str(int(datetime.now(tz=timezone.utc).timestamp() * 1000)),
        description="Monotonic ping ID (ms timestamp). Echoed back in PONG.",
    )
    sent_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Agent → Router frames
# ---------------------------------------------------------------------------


class WSAckFrame(_WSFrameBase):
    """Agent acknowledges successful processing of a message.

    The router calls ``XACK stream:{team} group:{team} <entry_id>`` to remove
    the entry from the Pending Entries List.
    """

    type: Literal["ACK"] = "ACK"

    entry_id: str = Field(
        ...,
        validation_alias=AliasChoices("entry_id", "stream_entry_id"),
        description="Redis Stream entry ID echoed from WSMessageFrame.entry_id.",
    )
    message_id: UUID | None = Field(
        default=None,
        description="Optional echo of MessageEnvelope.message_id for traceability.",
    )


class WSNackFrame(_WSFrameBase):
    """Agent rejects / defers a message — entry remains in the PEL.

    The router does NOT call XACK. XAUTOCLAIM will redeliver after 120 s idle.
    Agents should NACK only on transient errors (e.g. resource temporarily unavailable).
    Permanent failures should still ACK (to avoid infinite loops) and emit an
    ESCALATION or let the DLQ handle exhausted retries.
    """

    type: Literal["NACK"] = "NACK"

    entry_id: str = Field(
        ...,
        validation_alias=AliasChoices("entry_id", "stream_entry_id"),
        description="Redis Stream entry ID echoed from WSMessageFrame.entry_id.",
    )
    reason: str = Field(..., description="Human-readable reason for rejection.")
    retry_after_seconds: int | None = Field(
        default=None,
        description="Hint to the router about when to redeliver (advisory only).",
    )


class WSPongFrame(_WSFrameBase):
    """Agent responds to a router PING keepalive."""

    type: Literal["PONG"] = "PONG"

    ping_id: str = Field(..., description="Echoed from WSPingFrame.ping_id.")
    agent_id: str | None = Field(
        default=None, description="Optionally identifies which agent is responding."
    )


# ---------------------------------------------------------------------------
# Discriminated union — for router-side parsing
# ---------------------------------------------------------------------------

# Type alias for frames the router receives from agents
AgentFrame = WSAckFrame | WSNackFrame | WSPongFrame

# Type alias for frames the router sends to agents
RouterFrame = WSMessageFrame | WSPingFrame


def parse_agent_frame(data: dict[str, Any]) -> AgentFrame:
    """Parse a raw JSON dict into the appropriate agent → router frame model.

    Raises ``ValueError`` if ``type`` is missing or unknown.
    """
    frame_type = data.get("type")
    if frame_type == "ACK":
        return WSAckFrame(**data)
    elif frame_type == "NACK":
        return WSNackFrame(**data)
    elif frame_type == "PONG":
        return WSPongFrame(**data)
    else:
        raise ValueError(f"Unknown agent frame type: {frame_type!r}")
