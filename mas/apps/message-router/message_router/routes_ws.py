"""WebSocket subscribe endpoint — agent message delivery.

Protocol
--------
1. Agent connects to ``WS /ws/subscribe/{team_id}``
   with ``Authorization: Bearer {agent_id}:{secret}`` header.
2. Router authenticates and creates (or joins) the consumer group.
3. Router first delivers **pending PEL entries** (XREADGROUP … 0) so that
   messages in-flight before the previous disconnect are replayed.
4. Router then enters the live **new-message loop** (XREADGROUP … >).
5. Each message is sent as a ``WSMessageFrame`` JSON text frame.
6. Agent replies with ``WSAckFrame`` (router calls XACK) or ``WSNackFrame``
   (message stays in PEL for XAUTOCLAIM reclaim after 120 s).
7. Router sends a ``WSPingFrame`` every 15 s.  If no ``WSPongFrame`` arrives
   within 10 s the connection is closed and the agent must reconnect.

Authentication
--------------
Agents present ``Bearer {agent_id}:{secret}`` in the ``Authorization``
WebSocket header.  The router validates the secret against
``settings.agent_token_secret``.  For v1 all agents share the same secret;
in a future version each agent would have a unique credential.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState

from mas_core.protocols.ws import (
    WSAckFrame,
    WSMessageFrame,
    WSNackFrame,
    WSPingFrame,
    WSPongFrame,
    parse_agent_frame,
)

from .config import settings
from .redis_client import (
    ensure_consumer_group,
    get_redis,
    group_name,
    stream_key,
    xack,
    xreadgroup_new,
    xreadgroup_pending,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _authenticate(ws: WebSocket) -> str | None:
    """Extract and validate the agent_id from the Authorization header.

    Returns the *agent_id* string on success, ``None`` on failure.
    Expected header: ``Authorization: Bearer <agent_id>:<secret>``
    """
    auth_header: str | None = ws.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.removeprefix("Bearer ").strip()
    if ":" not in token:
        return None
    agent_id, secret = token.split(":", 1)
    if secret != settings.agent_token_secret:
        return None
    return agent_id if agent_id else None


# ---------------------------------------------------------------------------
# WebSocket subscribe handler
# ---------------------------------------------------------------------------


@router.websocket("/ws/subscribe/{team_id}")
async def ws_subscribe(ws: WebSocket, team_id: str) -> None:
    """Agent WebSocket subscription — stream messages from ``stream:{team_id}``."""

    # ── 1. Auth ────────────────────────────────────────────────────────────────
    agent_id = _authenticate(ws)
    if agent_id is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if team_id not in settings.known_teams:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    logger.info("WS connected: agent=%s team=%s", agent_id, team_id)

    # ── 2. Ensure consumer group exists ───────────────────────────────────────
    try:
        await ensure_consumer_group(team_id)
    except Exception:
        logger.exception("Failed to ensure consumer group for team=%s", team_id)
        await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    redis = get_redis()
    consumer_id = agent_id  # Use agent_id as the Redis consumer name

    # Track pending PING ids so we can validate PONGs
    pending_pings: dict[str, asyncio.Task[None]] = {}

    # ── 3. Deliver pending PEL entries (replay after reconnect) ───────────────
    try:
        pending_entries = await xreadgroup_pending(team_id, consumer_id, redis)
        for entry_id, fields in pending_entries:
            delivered = await _deliver_entry(
                ws, team_id, entry_id, fields, consumer_id, redis, pending_pings
            )
            if not delivered:
                break
    except Exception:
        logger.exception("Error delivering PEL entries: agent=%s team=%s", agent_id, team_id)

    # ── 4. Live new-message loop ───────────────────────────────────────────────
    ping_task: asyncio.Task[None] | None = None
    try:
        # Start ping sender
        ping_task = asyncio.create_task(
            _ping_loop(ws, pending_pings), name=f"ping-{agent_id}"
        )

        # Start receive task (handles ACK/NACK/PONG from agent)
        receive_task = asyncio.create_task(
            _receive_loop(ws, team_id, consumer_id, redis, pending_pings),
            name=f"recv-{agent_id}",
        )

        # Main read loop
        while ws.client_state == WebSocketState.CONNECTED:
            try:
                entries = await xreadgroup_new(
                    team_id, consumer_id, block_ms=settings.read_block_ms, redis=redis
                )
            except Exception:
                logger.exception(
                    "XREADGROUP error: agent=%s team=%s", agent_id, team_id
                )
                break

            for entry_id, fields in entries:
                if ws.client_state != WebSocketState.CONNECTED:
                    break
                delivered = await _deliver_entry(
                    ws, team_id, entry_id, fields, consumer_id, redis, pending_pings
                )
                if not delivered:
                    break

    except WebSocketDisconnect:
        logger.info("WS disconnected: agent=%s team=%s", agent_id, team_id)
    except Exception:
        logger.exception("WS error: agent=%s team=%s", agent_id, team_id)
    finally:
        if ping_task and not ping_task.done():
            ping_task.cancel()
        if "receive_task" in dir() and not receive_task.done():  # type: ignore[possibly-undefined]
            receive_task.cancel()
        logger.info("WS closed: agent=%s team=%s", agent_id, team_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _deliver_entry(
    ws: WebSocket,
    team_id: str,
    entry_id: str,
    fields: dict[str, str],
    consumer_id: str,
    redis,
    pending_pings: dict[str, asyncio.Task[None]],
) -> bool:
    """Parse and send one stream entry to the agent.

    Returns False if the WS is no longer usable (connection should stop).
    """
    from mas_core.protocols.envelope import MessageEnvelope

    envelope_json = fields.get("envelope", "")
    if not envelope_json:
        logger.warning(
            "Stream entry has no 'envelope' field: team=%s entry_id=%s",
            team_id, entry_id,
        )
        await xack(team_id, entry_id, redis)
        return True

    try:
        envelope = MessageEnvelope.model_validate_json(envelope_json)
    except Exception:
        logger.exception(
            "Failed to parse envelope from stream entry: team=%s entry_id=%s",
            team_id, entry_id,
        )
        await xack(team_id, entry_id, redis)
        return True

    frame = WSMessageFrame(
        entry_id=entry_id,
        envelope=envelope,
        stream=stream_key(team_id),
        retry_count=envelope.retry_count,
    )

    try:
        await ws.send_text(frame.model_dump_json())
        return True
    except Exception:
        logger.warning(
            "Failed to send MESSAGE frame: team=%s entry_id=%s", team_id, entry_id
        )
        return False


async def _ping_loop(
    ws: WebSocket,
    pending_pings: dict[str, asyncio.Task[None]],
) -> None:
    """Send a WSPingFrame every ``settings.ws_ping_interval_seconds``."""
    while True:
        await asyncio.sleep(settings.ws_ping_interval_seconds)
        if ws.client_state != WebSocketState.CONNECTED:
            break
        ping = WSPingFrame()
        try:
            await ws.send_text(ping.model_dump_json())
        except Exception:
            break

        # Schedule a pong-timeout watchdog
        pong_timeout_task = asyncio.create_task(
            _pong_timeout(ws, ping.ping_id, settings.ws_pong_timeout_seconds),
            name=f"pong-timeout-{ping.ping_id}",
        )
        pending_pings[ping.ping_id] = pong_timeout_task


async def _pong_timeout(ws: WebSocket, ping_id: str, timeout: int) -> None:
    """Close the WS if PONG does not arrive within *timeout* seconds."""
    await asyncio.sleep(timeout)
    logger.warning("PONG timeout for ping_id=%s — closing connection.", ping_id)
    try:
        await ws.close(code=status.WS_1001_GOING_AWAY)
    except Exception:
        pass


async def _receive_loop(
    ws: WebSocket,
    team_id: str,
    consumer_id: str,
    redis,
    pending_pings: dict[str, asyncio.Task[None]],
) -> None:
    """Continuously receive frames sent by the agent (ACK / NACK / PONG)."""
    while True:
        try:
            raw = await ws.receive_text()
        except WebSocketDisconnect:
            break
        except Exception:
            break

        try:
            data: dict[str, Any] = json.loads(raw)
            frame = parse_agent_frame(data)
        except Exception as exc:
            logger.warning("Failed to parse agent frame: %s — raw=%r", exc, raw[:200])
            continue

        if isinstance(frame, WSAckFrame):
            try:
                await xack(team_id, frame.entry_id, redis)
                logger.debug(
                    "ACK: team=%s entry_id=%s msg_id=%s",
                    team_id, frame.entry_id, frame.message_id,
                )
            except Exception:
                logger.exception(
                    "XACK failed: team=%s entry_id=%s", team_id, frame.entry_id
                )

        elif isinstance(frame, WSNackFrame):
            # Message stays in PEL — XAUTOCLAIM will reclaim after idle timeout.
            logger.debug(
                "NACK: team=%s entry_id=%s reason=%s retry_after=%s",
                team_id, frame.entry_id, frame.reason, frame.retry_after_seconds,
            )

        elif isinstance(frame, WSPongFrame):
            # Cancel the pong-timeout watchdog for this ping
            ping_id = frame.ping_id
            timeout_task = pending_pings.pop(ping_id, None)
            if timeout_task and not timeout_task.done():
                timeout_task.cancel()
            logger.debug("PONG received: ping_id=%s agent=%s", ping_id, frame.agent_id)
