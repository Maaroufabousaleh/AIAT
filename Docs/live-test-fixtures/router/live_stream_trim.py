"""Live C-012 Redis boundary probe with a disposable stream and real router code."""

from __future__ import annotations

import subprocess


SOURCE = r'''
import asyncio
import json

from message_router.config import settings
from message_router.redis_client import connect_redis, close_redis, trim_all_streams

TEAM = "live_c012"
STREAM = f"stream:{TEAM}"
GROUP = f"group:{TEAM}"
LIMIT = 50_000


async def append(client, count):
    ids = []
    for start in range(0, count, 1000):
        pipe = client.pipeline(transaction=False)
        for offset in range(min(1000, count - start)):
            pipe.xadd(STREAM, {"fixture": "C-012", "n": str(start + offset)})
        ids.extend(await pipe.execute())
    return ids


async def main():
    client = await connect_redis()
    settings.known_teams = [TEAM]
    settings.stream_max_len = LIMIT
    await client.delete(STREAM)
    try:
        ids = await append(client, LIMIT + 100)
        await client.xgroup_create(STREAM, GROUP, id=ids[-12])
        delivered = await client.xreadgroup(GROUP, "live-c012", {STREAM: ">"}, count=1)
        pending_id = delivered[0][1][0][0]
        await trim_all_streams(client)
        pending_length = await client.xlen(STREAM)
        pending_rows = await client.xpending_range(STREAM, GROUP, "-", "+", 10)
        assert any(row["message_id"] == pending_id for row in pending_rows)
        assert await client.xrange(STREAM, pending_id, pending_id)
        assert pending_length <= LIMIT

        await client.xack(STREAM, GROUP, pending_id)
        await append(client, LIMIT + 100)
        before_maxlen = await client.xlen(STREAM)
        await trim_all_streams(client)
        after_maxlen = await client.xlen(STREAM)
        assert before_maxlen > LIMIT
        assert after_maxlen <= LIMIT + 100
        print(json.dumps({
            "status": "PASS",
            "configured_maxlen": LIMIT,
            "pending_entry_preserved": pending_id,
            "length_with_pending": pending_length,
            "length_before_maxlen_trim": before_maxlen,
            "length_after_maxlen_trim": after_maxlen,
        }))
    finally:
        await client.delete(STREAM)
        await close_redis()


asyncio.run(main())
'''


result = subprocess.run(
    ["docker", "exec", "mas-message-router-1", "python", "-c", SOURCE],
    text=True,
    capture_output=True,
    check=True,
)
print(result.stdout.strip())
