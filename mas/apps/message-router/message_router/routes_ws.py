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
from collections import OrderedDict
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
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
    stream_key,
    xack,
    xreadgroup_new,
    xreadgroup_pending,
)

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


def _authenticate_token(auth_header: str | None) -> str | None:
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.removeprefix("Bearer ").strip()
    if ":" not in token:
        return None
    agent_id, secret = token.split(":", 1)
    if secret != settings.agent_token_secret:
        return None
    return agent_id if agent_id else None


def _authenticate(ws: WebSocket) -> str | None:
    """Extract and validate the agent_id from the Authorization header.

    Returns the *agent_id* string on success, ``None`` on failure.
    Expected header: ``Authorization: Bearer <agent_id>:<secret>``
    """
    return _authenticate_token(ws.headers.get("authorization"))


@router.get("/streams/{team_id}/recent")
async def recent_stream_entries(
    request: Request,
    team_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    after: str | None = Query(default=None, pattern=r"^\d+-\d+$"),
) -> dict[str, object]:
    """Return retained Redis stream entries, optionally after a stream cursor."""
    if _authenticate_token(request.headers.get("authorization")) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    if team_id not in settings.known_teams:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown team_id: {team_id!r}")

    redis = get_redis()
    if after:
        entries = await redis.xrange(
            stream_key(team_id),
            min=f"({after}",
            max="+",
            count=limit,
        )
    else:
        entries = list(reversed(await redis.xrevrange(stream_key(team_id), count=limit)))
    normalized = []
    for entry_id, fields in entries:
        normalized_fields = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in fields.items()
        }
        normalized.append({
            "entry_id": entry_id.decode() if isinstance(entry_id, bytes) else entry_id,
            "envelope": normalized_fields.get("envelope", ""),
        })
    return {"team_id": team_id, "entries": normalized}


@router.websocket("/ws/subscribe/{team_id}")
async def ws_subscribe(ws: WebSocket, team_id: str) -> None:
    """Agent WebSocket subscription — stream messages from ``stream:{team_id}``."""
    agent_id = _authenticate(ws)
    if agent_id is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if team_id not in settings.known_teams:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    logger.info("WS connected: agent=%s team=%s", agent_id, team_id)

    try:
        await ensure_consumer_group(team_id)
    except Exception:
        logger.exception("Failed to ensure consumer group for team=%s", team_id)
        await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    redis = get_redis()
    consumer_id = agent_id

    pending_pings: dict[str, asyncio.Task[None]] = {}

    delivered_ids: OrderedDict[str, None] = OrderedDict()
    max_delivered_ids = 1000

    try:
        pending_entries = await xreadgroup_pending(team_id, consumer_id, redis)
        for entry_id, fields in pending_entries:
            delivered = await _deliver_entry(
                ws,
                team_id,
                entry_id,
                fields,
                consumer_id,
                redis,
                pending_pings,
                delivered_ids,
                max_delivered_ids,
            )
            if not delivered:
                break
    except Exception:
        logger.exception("Error delivering PEL entries: agent=%s team=%s", agent_id, team_id)

    ping_task: asyncio.Task[None] | None = None
    try:
        ping_task = asyncio.create_task(_ping_loop(ws, pending_pings), name=f"ping-{agent_id}")

        receive_task = asyncio.create_task(
            _receive_loop(ws, team_id, consumer_id, redis, pending_pings),
            name=f"recv-{agent_id}",
        )

        while ws.client_state == WebSocketState.CONNECTED:
            try:
                entries = await xreadgroup_new(
                    team_id, consumer_id, block_ms=settings.read_block_ms, redis=redis
                )
            except Exception:
                logger.exception("XREADGROUP error: agent=%s team=%s", agent_id, team_id)
                break

            for entry_id, fields in entries:
                if ws.client_state != WebSocketState.CONNECTED:
                    break
                delivered = await _deliver_entry(
                    ws,
                    team_id,
                    entry_id,
                    fields,
                    consumer_id,
                    redis,
                    pending_pings,
                    delivered_ids,
                    max_delivered_ids,
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


@router.websocket("/subscribe")
async def ws_subscribe_compat(
    ws: WebSocket,
    agent_id: str | None = None,
    team_id: str | None = None,
) -> None:
    """Compatibility alias for the query-param subscribe route from the plan."""
    if team_id is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if agent_id is not None:
        authed_agent = _authenticate(ws)
        if authed_agent is None or authed_agent != agent_id:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await ws_subscribe(ws, team_id)


async def _deliver_entry(
    ws: WebSocket,
    team_id: str,
    entry_id: str,
    fields: dict[str, str],
    consumer_id: str,
    redis,
    pending_pings: dict[str, asyncio.Task[None]],
    delivered_ids: OrderedDict[str, None],
    max_delivered_ids: int,
) -> bool:
    """Parse and send one stream entry to the agent.

    Returns False if the WS is no longer usable (connection should stop).
    Performs consume-side idempotency: if the envelope's ``message_id`` was
    already delivered during this connection, the entry is silently ACKed.
    """
    from mas_core.protocols.envelope import MessageEnvelope

    envelope_json = fields.get("envelope", "")
    if not envelope_json:
        logger.warning(
            "Stream entry has no 'envelope' field: team=%s entry_id=%s",
            team_id,
            entry_id,
        )
        await xack(team_id, entry_id, redis)
        return True

    try:
        envelope = MessageEnvelope.model_validate_json(envelope_json)
    except Exception:
        logger.exception(
            "Failed to parse envelope from stream entry: team=%s entry_id=%s",
            team_id,
            entry_id,
        )
        await xack(team_id, entry_id, redis)
        return True

    msg_id = envelope.message_id
    if msg_id in delivered_ids:
        logger.debug(
            "Duplicate message_id suppressed (LRU): team=%s msg_id=%s entry_id=%s",
            team_id,
            msg_id,
            entry_id,
        )
        await xack(team_id, entry_id, redis)
        return True

    delivered_ids[msg_id] = None
    if len(delivered_ids) > max_delivered_ids:
        delivered_ids.popitem(last=False)

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
        logger.warning("Failed to send MESSAGE frame: team=%s entry_id=%s", team_id, entry_id)
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
                    team_id,
                    frame.entry_id,
                    frame.message_id,
                )
            except Exception:
                logger.exception("XACK failed: team=%s entry_id=%s", team_id, frame.entry_id)

        elif isinstance(frame, WSNackFrame):
            logger.debug(
                "NACK: team=%s entry_id=%s reason=%s retry_after=%s",
                team_id,
                frame.entry_id,
                frame.reason,
                frame.retry_after_seconds,
            )

        elif isinstance(frame, WSPongFrame):
            ping_id = frame.ping_id
            timeout_task = pending_pings.pop(ping_id, None)
            if timeout_task and not timeout_task.done():
                timeout_task.cancel()
            logger.debug("PONG received: ping_id=%s agent=%s", ping_id, frame.agent_id)
