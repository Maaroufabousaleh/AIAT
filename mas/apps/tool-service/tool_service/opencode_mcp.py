"""The only OpenCode-to-tool-service bridge.

OpenCode receives a per-run, HMAC-signed capability token in its internal MCP
configuration.  This ASGI boundary verifies that token before MCP negotiation
or tool execution and routes every allowed call through ``ToolRegistry``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest
from mas_core.worker_contract.opencode_bridge import (
    OpenCodeToolGrant,
    OpenCodeToolGrantError,
    verify_opencode_tool_grant,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


_grant_context: ContextVar[OpenCodeToolGrant | None] = ContextVar("opencode_mcp_grant", default=None)
_GRANT_HEADER = b"x-aiat-opencode-grant"


def create_opencode_mcp_app(parent: FastAPI):
    """Build the authenticated ASGI MCP app mounted below ``/opencode``."""
    server = FastMCP(
        "AIAT governed OpenCode bridge",
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["tool-service:8002", "127.0.0.1:8002", "localhost:8002"],
            allowed_origins=[],
        ),
    )

    @server.tool(name="aiat_tool")
    async def aiat_tool(
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one explicitly granted AIAT tool through the policy gateway."""
        grant = _grant_context.get()
        if grant is None or tool_name not in grant.tool_names:
            return {"success": False, "error_code": "FORBIDDEN", "error": "AIAT tool grant denied"}
        effective_arguments = dict(arguments or {})
        if tool_name.startswith("opencode.workspace_"):
            effective_arguments["workspace_run_id"] = str(grant.run_id)
        registry = parent.state.registry
        response = await registry.execute(
            ToolRequest(
                caller_id=grant.worker_id,
                caller_role=AgentRole.WORKER,
                project_id=grant.project_id,
                worker_run_id=grant.run_id,
                permission_scope=sorted(grant.tool_names),
                audit_context={
                    "bridge": "opencode_mcp",
                    "worker_run_id": str(grant.run_id),
                    "grant_id": grant.grant_id,
                },
                tool_name=tool_name,
                tool_kwargs=effective_arguments,
            )
        )
        return response.model_dump(mode="json", exclude_none=True)

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await _unavailable(send)
            return
        headers = dict(scope.get("headers") or [])
        raw_grant = headers.get(_GRANT_HEADER)
        settings = getattr(parent.state, "settings", None)
        if raw_grant is None or settings is None:
            await _forbidden(send)
            return
        try:
            grant = verify_opencode_tool_grant(raw_grant.decode("ascii"), settings.tool_secret)
        except (OpenCodeToolGrantError, UnicodeDecodeError):
            await _forbidden(send)
            return
        marker = _grant_context.set(grant)
        try:
            inner = getattr(parent.state, "opencode_mcp_inner", None)
            if inner is None:
                await _unavailable(send)
                return
            await inner(scope, receive, send)
        finally:
            _grant_context.reset(marker)

    @asynccontextmanager
    async def bridge_lifespan():
        # A FastMCP session manager is intentionally single-use.  TestClient
        # and controlled service restarts enter the parent lifespan more than
        # once, so construct a fresh manager and ASGI route every time.
        server._session_manager = None
        inner = server.streamable_http_app()
        parent.state.opencode_mcp_inner = inner
        try:
            async with server.session_manager.run():
                yield
        finally:
            parent.state.opencode_mcp_inner = None

    # Mounted Starlette applications do not automatically enter their own
    # lifespan in every supported host configuration.  Expose the factory so
    # the parent tool-service lifespan owns it explicitly.
    app.aiat_lifespan = bridge_lifespan
    return app


async def _forbidden(send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"detail":"OpenCode bridge grant denied"}'})


async def _unavailable(send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"detail":"OpenCode bridge is not ready"}'})
