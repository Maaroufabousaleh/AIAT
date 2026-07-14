"""Inner half of C-013, executed inside a service container."""

from __future__ import annotations

import asyncio
import json
import os

import redis.asyncio as redis
from redis.exceptions import AuthenticationError, NoPermissionError, ResponseError


async def denied(awaitable):
    try:
        await awaitable
    except (AuthenticationError, NoPermissionError, ResponseError):
        return True
    return False


async def main() -> None:
    role = os.environ["AIAT_ACL_PROBE_ROLE"]
    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    result: dict[str, object] = {"role": role, "ping": await client.ping()}
    if role == "router":
        await client.set("dedupe:c013", "ok", ex=30)
        result["own_key_crud"] = await client.get("dedupe:c013") == "ok"
        result["cross_key_denied"] = await denied(client.set("tool_cache:c013", "bad"))
    else:
        await client.set("tool_cache:c013", "ok", ex=30)
        result["own_key_crud"] = await client.get("tool_cache:c013") == "ok"
        result["cross_key_denied"] = await denied(client.xadd("stream:c013", {"x": "bad"}))
    result["acl_admin_denied"] = await denied(client.execute_command("ACL", "LIST"))
    result["config_admin_denied"] = await denied(client.execute_command("CONFIG", "GET", "maxmemory"))
    await client.delete("dedupe:c013" if role == "router" else "tool_cache:c013")
    await client.aclose()
    assert all(value is True for key, value in result.items() if key != "role")
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
