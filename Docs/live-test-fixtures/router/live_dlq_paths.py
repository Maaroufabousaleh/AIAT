"""Live C-010 probe for retry-exhaustion and TTL-expiry DLQ paths."""

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
CONSUMER = "live-c010"


def _secret() -> str:
    for line in (Path(__file__).parents[3] / ".env").read_text().splitlines():
        if line.startswith("AGENT_TOKEN_SECRET="):
            return line.split("=", 1)[1]
    raise RuntimeError("AGENT_TOKEN_SECRET not found")


def _inside(source: str, stdin: str = "") -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python", "-c", source],
        input=stdin,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _inject(envelope: MessageEnvelope) -> str:
    source = """
import asyncio, os, sys
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    print(await client.xadd('stream:dept_production', {'envelope': sys.stdin.read()}))
    await client.aclose()
asyncio.run(main())
"""
    return _inside(source, envelope.model_dump_json())


def _cleanup_fixture_entries() -> None:
    source = """
import asyncio, os
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    rows = await client.xrange('stream:dept_production', '-', '+')
    ids = [entry_id for entry_id, fields in rows if 'C-010-' in fields.get('envelope', '')]
    if ids:
        await client.xack('stream:dept_production', 'group:dept_production', *ids)
        await client.xdel('stream:dept_production', *ids)
    await client.aclose()
asyncio.run(main())
"""
    _inside(source)


def _age(entry_id: str) -> None:
    source = """
import asyncio, os, sys
import redis.asyncio as redis
async def main():
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    await client.xclaim('stream:dept_production', 'group:dept_production', 'live-c010', 0, [sys.stdin.read()], idle=120001, justid=True)
    await client.aclose()
asyncio.run(main())
"""
    _inside(source, entry_id)


def _state(message_id: str) -> dict[str, object]:
    source = """
import asyncio, json, os, sys
import asyncpg
import redis.asyncio as redis
async def main():
    message_id = sys.stdin.read()
    client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    entries = await client.xrange('stream:dept_production', '-', '+')
    stream = [{'entry_id': entry_id, 'envelope': json.loads(fields['envelope'])} for entry_id, fields in entries if message_id in fields.get('envelope', '')]
    await client.aclose()
    conn = await asyncpg.connect(os.environ['POSTGRES_DSN'], statement_cache_size=0)
    rows = await conn.fetch('SELECT id, message_id, recipient_team, retry_count, failure_reason FROM dead_letters WHERE message_id=$1 ORDER BY id', message_id)
    await conn.close()
    print(json.dumps({'stream': stream, 'dlq': [dict(row) for row in rows]}))
asyncio.run(main())
"""
    return json.loads(_inside(source, message_id))


async def _connect():
    headers = {"Authorization": f"Bearer {CONSUMER}:{_secret()}"}
    url = f"ws://127.0.0.1:8001/ws/subscribe/{TEAM}"
    try:
        return await websockets.connect(url, additional_headers=headers)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers)


async def _consume_without_ack(expected_entry: str) -> None:
    ws = await _connect()
    frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert frame["entry_id"] == expected_entry
    await ws.close()


async def _wait_for_retry(message_id: str, retry: int, old_entry: str) -> str:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state = _state(message_id)
        rows = [row for row in state["stream"] if row["entry_id"] != old_entry and row["envelope"]["retry_count"] == retry]
        if rows:
            return str(rows[0]["entry_id"])
        await asyncio.sleep(2)
    raise AssertionError(f"retry {retry} was not requeued")


async def _wait_for_dlq(message_id: str, reason: str, retry: int) -> dict[str, object]:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state = _state(message_id)
        rows = [row for row in state["dlq"] if row["failure_reason"] == reason and row["retry_count"] == retry]
        if rows:
            assert state["stream"] == []
            return rows[0]
        await asyncio.sleep(2)
    raise AssertionError(f"DLQ row {reason}/{retry} was not written")


def _envelope(fixture: str, ttl_seconds: int = 3600) -> MessageEnvelope:
    return MessageEnvelope(
        msg_type=MessageType.HEARTBEAT,
        sender_id=CONSUMER,
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team=TEAM,
        project_id=None,
        ttl_seconds=ttl_seconds,
        payload={"fixture": fixture},
    )


async def main() -> None:
    _cleanup_fixture_entries()
    exhausted = _envelope("C-010-exhausted")
    entry = _inject(exhausted)
    for retry in (1, 2):
        await _consume_without_ack(entry)
        _age(entry)
        entry = await _wait_for_retry(str(exhausted.message_id), retry, entry)
    await _consume_without_ack(entry)
    _age(entry)
    exhausted_row = await _wait_for_dlq(str(exhausted.message_id), "max_attempts_exceeded", 3)

    expired = _envelope("C-010-expired", ttl_seconds=1)
    expired_entry = _inject(expired)
    await _consume_without_ack(expired_entry)
    await asyncio.sleep(1.2)
    _age(expired_entry)
    expired_row = await _wait_for_dlq(str(expired.message_id), "ttl_expired", 1)

    print(json.dumps({
        "status": "PASS",
        "exhausted": {"message_id": str(exhausted.message_id), "dlq_id": exhausted_row["id"], "retry_count": 3, "stream_entries": 0},
        "expired": {"message_id": str(expired.message_id), "dlq_id": expired_row["id"], "retry_count": 1, "stream_entries": 0},
    }))


if __name__ == "__main__":
    asyncio.run(main())
