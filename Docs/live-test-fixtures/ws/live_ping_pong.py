"""Live C-006 probe for router WebSocket PING/PONG liveness behavior."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed


def _secret() -> str:
    for line in (Path(__file__).parents[3] / ".env").read_text().splitlines():
        if line.startswith("AGENT_TOKEN_SECRET="):
            return line.split("=", 1)[1]
    raise RuntimeError("AGENT_TOKEN_SECRET not found")


async def _connect(agent_id: str):
    headers = {"Authorization": f"Bearer {agent_id}:{_secret()}"}
    url = "ws://127.0.0.1:8001/ws/subscribe/dept_production"
    try:
        return await websockets.connect(url, additional_headers=headers)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers)


async def _next_ping(ws, timeout: float = 20) -> dict[str, object]:
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if frame.get("type") == "PING":
            return frame


async def respond() -> None:
    ws = await _connect("live-c006-respond")
    first = await _next_ping(ws)
    await ws.send(json.dumps({"type": "PONG", "ping_id": first["ping_id"], "agent_id": "live-c006-respond"}))
    second = await _next_ping(ws)
    assert second["ping_id"] != first["ping_id"]
    await ws.send(json.dumps({"type": "PONG", "ping_id": second["ping_id"], "agent_id": "live-c006-respond"}))
    await ws.close()
    print(json.dumps({"status": "PASS", "mode": "respond", "pings_answered": 2, "connection_survived": True}))


async def ignore() -> None:
    ws = await _connect("live-c006-ignore")
    await _next_ping(ws)
    started = time.monotonic()
    try:
        while True:
            await ws.recv()
    except ConnectionClosed as exc:
        elapsed = time.monotonic() - started
        assert exc.code == 1001, exc.code
        assert 8 <= elapsed <= 15, elapsed
        print(json.dumps({"status": "PASS", "mode": "ignore", "close_code": exc.code, "seconds_after_ping": round(elapsed, 2)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("respond", "ignore"))
    args = parser.parse_args()
    asyncio.run(respond() if args.mode == "respond" else ignore())
