"""The fixed AIAT-to-OpenHands MCP bridge.

OpenHands receives one run-scoped grant through its operator-provisioned
profile.  The mounted app exposes exactly one generic ``aiat_tool`` operation;
there is deliberately no endpoint for registering or discovering arbitrary
external MCP servers.  Every call is reconstructed as an AIAT ``ToolRequest``
so the caller identity, worker run, grants, policy, rate limits, and audit path
come from the control plane rather than OpenHands request fields.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mas_core.policy.privileged_ops import PRIVILEGED_ACTIONS
from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest
from mas_core.worker_contract.openhands_bridge import (
    OpenHandsToolGrant,
    OpenHandsToolGrantError,
    verify_openhands_tool_grant,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


_grant_context: ContextVar[OpenHandsToolGrant | None] = ContextVar("openhands_mcp_grant", default=None)
_GRANT_HEADER = b"x-aiat-openhands-grant"
_APPROVAL_PREFIXES = ("approval.", "credentials.", "policy.", "security.")
_HIGH_RISK_TIERS = frozenset({"high", "critical"})


def _requires_approval(registry: Any, tool_name: str) -> bool:
    """Fail closed for privileged actions before they reach a worker bridge."""

    if tool_name in PRIVILEGED_ACTIONS or tool_name.startswith(_APPROVAL_PREFIXES):
        return True
    tool = getattr(registry, "_tools", {}).get(tool_name)
    if tool is None:
        return False
    return str(getattr(tool, "risk_tier", "standard")).lower() in _HIGH_RISK_TIERS or str(
        getattr(tool, "approval_policy", "role")
    ).lower() in {"human", "approval", "step_up", "required"}


async def _execute_granted_tool(parent: FastAPI, grant: OpenHandsToolGrant, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Apply bridge policy and dispatch with identity derived from ``grant``."""

    registry = parent.state.registry
    if tool_name not in grant.tool_names:
        return {"success": False, "error_code": "FORBIDDEN", "error": "AIAT tool grant denied"}
    if _requires_approval(registry, tool_name):
        return {
            "success": False,
            "error_code": "APPROVAL_REQUIRED",
            "error": "approval-required AIAT tools cannot execute through the worker bridge",
        }
    response = await registry.execute(
        ToolRequest(
            caller_id=grant.worker_id,
            caller_role=AgentRole.WORKER,
            project_id=grant.project_id,
            worker_run_id=grant.run_id,
            permission_scope=sorted(grant.tool_names),
            audit_context={
                "bridge": "openhands_mcp",
                "worker_run_id": str(grant.run_id),
                "grant_id": grant.grant_id,
            },
            tool_name=tool_name,
            tool_kwargs=dict(arguments or {}),
        )
    )
    return response.model_dump(mode="json", exclude_none=True)


def create_openhands_mcp_app(parent: FastAPI):
    """Build the authenticated ASGI MCP app mounted below ``/openhands``."""

    server = FastMCP(
        "AIAT governed OpenHands bridge",
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
        """Execute one explicitly granted, non-privileged AIAT tool."""

        grant = _grant_context.get()
        if grant is None:
            return {"success": False, "error_code": "FORBIDDEN", "error": "AIAT tool grant denied"}
        return await _execute_granted_tool(parent, grant, tool_name, arguments)

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
            grant = verify_openhands_tool_grant(raw_grant.decode("ascii"), settings.tool_secret)
        except (OpenHandsToolGrantError, UnicodeDecodeError):
            await _forbidden(send)
            return
        marker = _grant_context.set(grant)
        try:
            inner = getattr(parent.state, "openhands_mcp_inner", None)
            if inner is None:
                await _unavailable(send)
                return
            await inner(scope, receive, send)
        finally:
            _grant_context.reset(marker)

    @asynccontextmanager
    async def bridge_lifespan():
        # FastMCP's session manager is intentionally single-use.  The parent
        # service owns this lifecycle so restart/tests get a fresh manager.
        server._session_manager = None
        inner = server.streamable_http_app()
        parent.state.openhands_mcp_inner = inner
        try:
            async with server.session_manager.run():
                yield
        finally:
            parent.state.openhands_mcp_inner = None

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
    await send({"type": "http.response.body", "body": b'{"detail":"OpenHands bridge grant denied"}'})


async def _unavailable(send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"detail":"OpenHands bridge is not ready"}'})
