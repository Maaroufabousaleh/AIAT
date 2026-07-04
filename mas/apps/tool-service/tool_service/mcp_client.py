"""MCP transport client backed by the official Python SDK."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def _session(config: dict[str, Any]) -> AsyncIterator[ClientSession]:
    transport = str(config.get("transport") or "").lower()
    if transport == "stdio":
        command = str(config.get("command") or "").strip()
        args = [str(value) for value in config.get("args", [])]
        if not command:
            raise ValueError("MCP stdio server requires command")
        params = StdioServerParameters(
            command=command,
            args=args,
            env={str(k): str(v) for k, v in dict(config.get("env") or {}).items()} or None,
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session
        return

    if transport in {"http", "streamable_http"}:
        url = str(config.get("url") or "").strip()
        if not url:
            raise ValueError("MCP HTTP server requires url")
        async with (
            streamablehttp_client(url) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session
        return

    raise ValueError(f"Unsupported MCP transport: {transport!r}")


def _serialize_content(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


async def invoke_mcp_tool(
    servers: dict[str, dict[str, Any]],
    kwargs: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """List or invoke one tool on an explicitly registered MCP server."""
    server_name = str(kwargs.get("server") or "").strip()
    if not server_name:
        raise ValueError("server is required")
    config = servers.get(server_name)
    if config is None:
        raise ValueError(f"Unknown MCP server: {server_name}")

    operation = str(kwargs.get("operation") or "call")

    async def execute() -> dict[str, Any]:
        async with _session(config) as session:
            if operation == "list_tools":
                response = await session.list_tools()
                return {
                    "server": server_name,
                    "operation": operation,
                    "tools": [tool.model_dump(mode="json") for tool in response.tools],
                }
            if operation != "call":
                raise ValueError("operation must be call or list_tools")
            tool_name = str(kwargs.get("tool") or "").strip()
            if not tool_name:
                raise ValueError("tool is required for call operation")
            arguments = dict(kwargs.get("arguments") or {})
            response = await session.call_tool(tool_name, arguments=arguments)
            return {
                "server": server_name,
                "operation": operation,
                "tool": tool_name,
                "is_error": bool(response.isError),
                "content": [_serialize_content(item) for item in response.content],
                "structured_content": response.structuredContent,
            }

    try:
        return await asyncio.wait_for(execute(), timeout=timeout)
    except TimeoutError as exc:
        raise RuntimeError(f"MCP server {server_name!r} timed out after {timeout}s") from exc
