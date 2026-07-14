"""Live C-009 probe: age a real pending entry and observe XAUTOCLAIM retry."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import websockets

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.enums import AgentRole, MessageType


CONTAINER = "mas-message-router-1"
TEAM = "dept_production"
CONSUMER = "live-c009"


def _secret() -> str:
    for line in (Path(__file__).parents[3] / ".env").read_text().splitlines():
        if line.startswith("AGENT_TOKEN_SECRET="):
            return line.split("=", 1)[1]
    raise RuntimeError("AGENT_TOKEN_SECRET not found")


def _redis(source: str, stdin: str = "") -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python", "-c", source],
        input=stdin,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _inject(envelope_json: str) -> str:
    source = """
import asyncio, os, sys
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    print(await client.xadd('stream:dept_production', {'envelope': sys.stdin.read()}))
    await client.aclose()
asyncio.run(main())
"""
    return _redis(source, envelope_json)


def _age_pending(entry_id: str) -> None:
    source = """
import asyncio, os, sys
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    await client.xclaim('stream:dept_production', 'group:dept_production', 'live-c009', 0, [sys.stdin.read()], idle=120001, justid=True)
    await client.aclose()
asyncio.run(main())
"""
    _redis(source, entry_id)


def _find(message_id: str) -> list[dict[str, object]]:
    source = """
import asyncio, json, os, sys
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    message_id = sys.stdin.read()
    rows = await client.xrange('stream:dept_production', '-', '+')
    result = [{'entry_id': entry_id, 'envelope': json.loads(fields['envelope'])} for entry_id, fields in rows if message_id in fields.get('envelope', '')]
    await client.aclose()
    print(json.dumps(result))
asyncio.run(main())
"""
    return json.loads(_redis(source, message_id))


async def _connect():
    headers = {"Authorization": f"Bearer {CONSUMER}:{_secret()}"}
    url = f"ws://127.0.0.1:8001/ws/subscribe/{TEAM}"
    try:
        return await websockets.connect(url, additional_headers=headers)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers)


async def main() -> None:
    envelope = MessageEnvelope(
        msg_type=MessageType.HEARTBEAT,
        sender_id=CONSUMER,
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team=TEAM,
        project_id=None,
        payload={"fixture": "C-009"},
    )
    ws = await _connect()
    original = _inject(envelope.model_dump_json())
    frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert frame["entry_id"] == original
    await ws.close()  # Leave the entry in the PEL.
    _age_pending(original)

    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        rows = _find(str(envelope.message_id))
        retried = [row for row in rows if row["entry_id"] != original and row["envelope"]["retry_count"] == 1]
        if retried:
            print(json.dumps({"status": "PASS", "message_id": str(envelope.message_id), "original_entry": original, "requeued_entry": retried[0]["entry_id"], "retry_count": 1}))
            return
        await asyncio.sleep(2)
    raise AssertionError("router did not reclaim and requeue the aged pending entry")


if __name__ == "__main__":
    asyncio.run(main())
