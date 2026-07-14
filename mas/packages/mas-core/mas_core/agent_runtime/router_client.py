"""RouterClient — HTTP + WebSocket client for agent ↔ message-router communication.

Agents use this instead of touching Redis directly. The message-router holds all
Redis credentials; agents only know ``ROUTER_URL``.

HTTP API
--------
``publish(envelope)``
    POST /messages/publish — returns the Redis stream entry ID.
``broadcast(envelope)``
    POST /messages/broadcast — fan-out to all 11 team streams.

WebSocket API
-------------
``subscribe(team_id, handler)``
    Opens WS /ws/subscribe/{team_id} with Bearer auth, then runs a read loop:
    for each WSMessageFrame received the handler coroutine is scheduled. On
    success RouterClient sends ACK; on exception it sends NACK. Ping/Pong
    keepalives are handled transparently while handlers are running.

    The caller is responsible for running this as a long-lived asyncio Task and
    cancelling it on shutdown.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..protocols.envelope import MessageEnvelope
from ..protocols.ws import WSAckFrame, WSMessageFrame, WSNackFrame, WSPingFrame, WSPongFrame

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RouterError(Exception):
    """Raised when the router returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Router error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class RouterDuplicateMessage(RouterError):
    """409 — message_id already processed (publish-side dedupe)."""


# ---------------------------------------------------------------------------
# Type alias for the message handler coroutine
# ---------------------------------------------------------------------------

MessageHandler = Callable[[WSMessageFrame], Awaitable[None]]


# ---------------------------------------------------------------------------
# RouterClient
# ---------------------------------------------------------------------------


class RouterClient:
    """Async HTTP + WebSocket client for the message-router service.

    Parameters
    ----------
    router_url:
        Base URL of the message-router, e.g. ``http://message-router:8000``.
    agent_id:
        The agent's unique identifier.
    agent_secret:
        Shared secret used to authenticate the WS subscription.
    timeout_s:
        HTTP request timeout in seconds.
    ws_reconnect_delay_s:
        Seconds to wait before reconnecting after a WS disconnect.
    """

    def __init__(
        self,
        *,
        router_url: str,
        agent_id: str,
        agent_secret: str,
        timeout_s: float = 30.0,
        ws_reconnect_delay_s: float = 5.0,
    ) -> None:
        self._router_url = router_url.rstrip("/")
        self._agent_id = agent_id
        self._agent_secret = agent_secret
        self._timeout_s = timeout_s
        self._ws_reconnect_delay_s = ws_reconnect_delay_s
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the shared HTTP client. Call once before using publish/broadcast."""
        self._http = httpx.AsyncClient(
            base_url=self._router_url,
            timeout=self._timeout_s,
        )

    async def stop(self) -> None:
        """Close the shared HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # HTTP publish
    # ------------------------------------------------------------------

    async def publish(self, envelope: MessageEnvelope) -> str:
        """POST /messages/publish — returns the Redis stream entry ID.

        Raises
        ------
        RouterDuplicateMessage
            If the router returns 409 (message_id already seen in dedupe window).
        RouterError
            For any other non-2xx response.
        """
        client = self._require_http()
        response = await client.post(
            "/messages/publish",
            content=envelope.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code == 409:
            raise RouterDuplicateMessage(409, response.text)
        if response.status_code not in (200, 201):
            raise RouterError(response.status_code, response.text)
        data = response.json()
        return data.get("entry_id", "")

    async def broadcast(self, envelope: MessageEnvelope) -> dict[str, Any]:
        """POST /messages/broadcast — fan-out to all 11 team streams.

        Returns a dict mapping team_id → entry_id.
        """
        client = self._require_http()
        response = await client.post(
            "/messages/broadcast",
            content=envelope.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code not in (200, 201):
            raise RouterError(response.status_code, response.text)
        return response.json()

    # ------------------------------------------------------------------
    # WebSocket subscribe
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        team_id: str,
        handler: MessageHandler,
        *,
        project_id: str | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run a WebSocket read loop against ``/ws/subscribe/{team_id}``.

        This method runs forever (reconnecting on disconnect) until
        ``stop_event`` is set or the task is cancelled.

        For each ``WSMessageFrame`` received:
        - If ``handler(frame)`` succeeds → send ACK to router.
        - If ``handler(frame)`` raises → send NACK, log the error.

        PING frames are answered with PONG automatically.

        Parameters
        ----------
        team_id:
            The team stream to subscribe to.
        handler:
            Async callable invoked for each delivered message.
        stop_event:
            Optional event; when set, the loop exits cleanly after the current
            message completes.
        project_id:
            Optional project scope. When supplied, the router suppresses
            messages for other projects without acknowledging them.
        """
        auth_header = f"Bearer {self._agent_id}:{self._agent_secret}"
        ws_url = self._router_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/subscribe/{team_id}"
        if project_id is not None:
            from urllib.parse import quote

            ws_url = f"{ws_url}?project_id={quote(project_id, safe='')}"

        while True:
            if stop_event and stop_event.is_set():
                return

            # --- Use websockets library for the actual WS connection ---
            try:
                await self._ws_loop(ws_url, auth_header, handler, stop_event)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "WS subscribe disconnected, reconnecting in %ss: %s",
                    self._ws_reconnect_delay_s,
                    exc,
                )
                if stop_event and stop_event.is_set():
                    return
                await asyncio.sleep(self._ws_reconnect_delay_s)

    async def _ws_loop(
        self,
        ws_url: str,
        auth_header: str,
        handler: MessageHandler,
        stop_event: asyncio.Event | None,
    ) -> None:
        """Inner WS read loop. Uses the ``websockets`` library.

        We import ``websockets`` lazily so that the module can be imported
        without it installed in dev environments (LSP won't error on the
        import itself).
        """
        try:
            import websockets  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'websockets' package is required for RouterClient.subscribe(). "
                "Install it with: pip install websockets"
            ) from exc

        connect_kwargs = self._ws_connect_headers_kwargs(websockets, auth_header)
        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            logger.info("WS connected: %s", ws_url)
            send_lock = asyncio.Lock()
            handler_lock = asyncio.Lock()
            pending: set[asyncio.Task[None]] = set()

            async def send_frame(frame: Any) -> None:
                async with send_lock:
                    await ws.send(frame.model_dump_json())

            async def run_handler(frame: WSMessageFrame) -> None:
                async with handler_lock:
                    try:
                        await handler(frame)
                    except Exception as exc:
                        logger.error("Handler raised for entry %s: %s", frame.entry_id, exc)
                        nack = WSNackFrame(entry_id=frame.entry_id, reason=str(exc))
                        try:
                            await send_frame(nack)
                        except Exception:
                            logger.warning("Failed to send NACK for entry %s", frame.entry_id)
                        return

                    ack = WSAckFrame(entry_id=frame.entry_id, message_id=frame.envelope.message_id)
                    try:
                        await send_frame(ack)
                    except Exception:
                        logger.warning("Failed to send ACK for entry %s", frame.entry_id)

            try:
                async for raw_message in ws:
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        data: dict[str, Any] = json.loads(raw_message)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON WS frame, ignoring.")
                        continue

                    frame_type = data.get("type")

                    if frame_type == "PING":
                        ping = WSPingFrame(**data)
                        pong = WSPongFrame(ping_id=ping.ping_id, agent_id=self._agent_id)
                        await send_frame(pong)
                        continue

                    if frame_type == "MESSAGE":
                        frame = WSMessageFrame(**data)
                        task = asyncio.create_task(
                            run_handler(frame),
                            name=f"router-handler:{frame.entry_id}",
                        )
                        pending.add(task)
                        task.add_done_callback(pending.discard)
                        continue

                    logger.debug("Unknown WS frame type %r, ignoring.", frame_type)
            finally:
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def _ws_connect_headers_kwargs(websockets_module: Any, auth_header: str) -> dict[str, Any]:
        """Build auth header kwargs compatible with multiple websockets versions."""
        try:
            params = inspect.signature(websockets_module.connect).parameters
        except (TypeError, ValueError):
            params = {}
        if "additional_headers" in params:
            return {"additional_headers": {"Authorization": auth_header}}
        return {"extra_headers": {"Authorization": auth_header}}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "RouterClient not started. Call 'await client.start()' first."
            )
        return self._http

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> RouterClient:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
