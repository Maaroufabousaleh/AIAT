"""Live C-013 allowed/denied Redis ACL matrix without printing credentials."""

from __future__ import annotations

import json
import subprocess


SOURCE = r'''
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


async def main():
    role = os.environ["AIAT_ACL_PROBE_ROLE"]
    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    result = {"role": role, "ping": await client.ping()}
    if role == "router":
        await client.set("dedupe:c013", "ok", ex=30)
        result["own_key_crud"] = await client.get("dedupe:c013") == "ok"
        result["cross_key_denied"] = await denied(client.set("tool_cache:c013", "bad"))
        result["acl_admin_denied"] = await denied(client.execute_command("ACL", "LIST"))
        result["config_admin_denied"] = await denied(client.execute_command("CONFIG", "GET", "maxmemory"))
        await client.delete("dedupe:c013")
    else:
        await client.set("tool_cache:c013", "ok", ex=30)
        result["own_key_crud"] = await client.get("tool_cache:c013") == "ok"
        result["cross_key_denied"] = await denied(client.xadd("stream:c013", {"x": "bad"}))
        result["acl_admin_denied"] = await denied(client.execute_command("ACL", "LIST"))
        result["config_admin_denied"] = await denied(client.execute_command("CONFIG", "GET", "maxmemory"))
        await client.delete("tool_cache:c013")
    await client.aclose()
    assert all(value is True for key, value in result.items() if key != "role")
    print(json.dumps(result))


asyncio.run(main())
'''


def probe(container: str, role: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "exec", "-e", f"AIAT_ACL_PROBE_ROLE={role}", container, "python", "-c", SOURCE],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


router = probe("mas-message-router-1", "router")
toolcache = probe("mas-tool-service-1", "toolcache")
default = subprocess.run(
    ["docker", "exec", "mas-redis-1", "redis-cli", "PING"],
    text=True,
    capture_output=True,
    check=False,
)
assert "NOAUTH" in default.stdout + default.stderr
print(json.dumps({"status": "PASS", "router": router, "toolcache": toolcache, "default_user_noauth": True}))
