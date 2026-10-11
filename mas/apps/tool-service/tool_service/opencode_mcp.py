"""The only OpenCode-to-tool-service bridge.

OpenCode receives a per-run, HMAC-signed capability token in its internal MCP
configuration.  This ASGI boundary verifies that token before MCP negotiation
or tool execution and routes every allowed call through ``ToolRegistry``.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest, ToolResponse
from mas_core.worker_contract.models import WorkerToolRequest
from mas_core.worker_contract.opencode_bridge import (
    OpenCodeToolGrant,
    OpenCodeToolGrantError,
    verify_opencode_tool_grant,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


_grant_context: ContextVar[OpenCodeToolGrant | None] = ContextVar("opencode_mcp_grant", default=None)
_GRANT_HEADER = b"x-aiat-opencode-grant"
_MAX_MCP_SESSION_ID_LENGTH = 512
_MAX_MCP_REQUEST_ID_LENGTH = 256


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
        ctx: Context,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one explicitly granted AIAT tool through the policy gateway."""
        grant = _grant_context.get()
        if grant is None or tool_name not in grant.tool_names:
            return {"success": False, "error_code": "FORBIDDEN", "error": "AIAT tool grant denied"}

        try:
            request_id = _mcp_effect_request_id(grant.run_id, ctx)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_REQUEST_IDENTITY_UNAVAILABLE",
                error="AIAT could not establish a stable MCP request identity",
            )

        effective_arguments = dict(arguments or {})
        if tool_name.startswith("opencode.workspace_"):
            effective_arguments["workspace_run_id"] = str(grant.run_id)

        effect_request = WorkerToolRequest(
            request_id=request_id,
            run_id=grant.run_id,
            tool_name=tool_name,
            arguments=effective_arguments,
            permission_scope=sorted(grant.tool_names),
            approval_required=False,
            idempotency_key=str(request_id),
        )
        request_sha256 = _effect_request_sha256(effect_request)
        request = ToolRequest(
            caller_id=grant.worker_id,
            caller_role=AgentRole.WORKER,
            project_id=str(grant.project_id) if grant.project_id else None,
            worker_run_id=grant.run_id,
            permission_scope=sorted(grant.tool_names),
            audit_context={
                "bridge": "opencode_mcp",
                "worker_run_id": str(grant.run_id),
                "grant_id": grant.grant_id,
            },
            tool_name=tool_name,
            tool_kwargs=effective_arguments,
            idempotency_key=request_id,
        )
        effect_repository = _effect_repository(parent)
        begin_effect = getattr(effect_repository, "begin_worker_tool_effect", None)
        complete_effect = getattr(effect_repository, "complete_worker_tool_effect", None)
        if not callable(begin_effect) or not callable(complete_effect):
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_LEDGER_UNAVAILABLE",
                error="AIAT durable worker-tool effect storage is unavailable",
                idempotency_key=request_id,
            )

        try:
            effect = await begin_effect(
                run_id=grant.run_id,
                request_id=request_id,
                idempotency_key=str(request_id),
                tool_name=tool_name,
                request_sha256=request_sha256,
                claim_owner=grant.claim_owner,
                attempt_count=grant.attempt_count,
            )
        except Exception:
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_LEDGER_UNAVAILABLE",
                error="AIAT could not reserve a durable worker-tool effect",
                idempotency_key=request_id,
            )

        if not isinstance(effect, dict):
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_LEDGER_UNAVAILABLE",
                error="AIAT returned an invalid worker-tool effect reservation",
                idempotency_key=request_id,
            )
        disposition = str(effect.get("disposition", "")).upper()
        if disposition == "CLAIM_LOST":
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="WORKER_RUN_CLAIM_LOST",
                error="Worker Run claim is no longer current; tool dispatch was denied",
                idempotency_key=request_id,
            )
        if disposition == "REPLAY":
            replay = _completed_replay(
                effect.get("response_json"),
                grant.run_id,
                tool_name,
                request_id,
            )
            if replay is not None:
                return replay
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_AMBIGUOUS",
                error="AIAT found an invalid completed worker-tool effect record",
                idempotency_key=request_id,
            )
        if disposition == "CONFLICT":
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_IDEMPOTENCY_CONFLICT",
                error="AIAT request identity was reused with different tool input",
                idempotency_key=request_id,
            )
        if disposition != "NEW":
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_AMBIGUOUS",
                error="AIAT cannot safely replay this worker-tool effect",
                idempotency_key=request_id,
            )
        if not isinstance(effect.get("id"), UUID):
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_LEDGER_UNAVAILABLE",
                error="AIAT returned an invalid worker-tool effect reservation",
                idempotency_key=request_id,
            )

        validate_claim = getattr(effect_repository, "validate_worker_run_claim", None)
        if not callable(validate_claim):
            denied = _error_response_model(
                tool_name,
                grant.run_id,
                error_code="WORKER_RUN_CLAIM_VALIDATION_UNAVAILABLE",
                error="AIAT cannot verify the current Worker Run claim before tool dispatch",
                idempotency_key=request_id,
            )
            try:
                settled = await complete_effect(
                    effect["id"],
                    state="COMPLETED",
                    response_json=denied.model_dump(mode="json", exclude_none=True),
                )
            except Exception:
                settled = None
            if settled is None:
                return _error_response(
                    tool_name,
                    grant.run_id,
                    error_code="TOOL_EFFECT_COMPLETION_UNAVAILABLE",
                    error="AIAT could not persist the denied worker-tool outcome",
                    idempotency_key=request_id,
                )
            return denied.model_dump(mode="json", exclude_none=True)
        try:
            claim_current = await validate_claim(
                run_id=grant.run_id,
                claim_owner=grant.claim_owner,
                attempt_count=grant.attempt_count,
            )
        except Exception:
            claim_current = False
        if not claim_current:
            denied = _error_response_model(
                tool_name,
                grant.run_id,
                error_code="WORKER_RUN_CLAIM_LOST",
                error="Worker Run claim expired or was reassigned before tool dispatch",
                idempotency_key=request_id,
            )
            try:
                settled = await complete_effect(
                    effect["id"],
                    state="COMPLETED",
                    response_json=denied.model_dump(mode="json", exclude_none=True),
                )
            except Exception:
                settled = None
            if settled is None:
                return _error_response(
                    tool_name,
                    grant.run_id,
                    error_code="TOOL_EFFECT_COMPLETION_UNAVAILABLE",
                    error="AIAT could not persist the denied worker-tool outcome",
                    idempotency_key=request_id,
                )
            return denied.model_dump(mode="json", exclude_none=True)

        try:
            registry = parent.state.registry
            response = await registry.execute(request)
            if (
                response.worker_run_id != grant.run_id
                or response.tool_name != tool_name
                or response.idempotency_key != request_id
            ):
                raise ValueError("ToolRegistry returned a mismatched worker response")
            response_json = response.model_dump(mode="json", exclude_none=True)
        except Exception:
            ambiguous = _error_response_model(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_AMBIGUOUS",
                error="AIAT tool execution ended without a durable result",
                idempotency_key=request_id,
            )
            try:
                settled = await complete_effect(
                    effect["id"],
                    state="AMBIGUOUS",
                    response_json=ambiguous.model_dump(mode="json", exclude_none=True),
                )
            except Exception:
                settled = None
            if settled is None:
                return _error_response(
                    tool_name,
                    grant.run_id,
                    error_code="TOOL_EFFECT_COMPLETION_UNAVAILABLE",
                    error="AIAT could not persist the ambiguous worker-tool outcome",
                    idempotency_key=request_id,
                )
            return ambiguous.model_dump(mode="json", exclude_none=True)

        try:
            settled = await complete_effect(
                effect["id"],
                state="COMPLETED",
                response_json=response_json,
            )
        except Exception:
            settled = None
        if settled is None or str(settled.get("state", "")).upper() != "COMPLETED":
            return _error_response(
                tool_name,
                grant.run_id,
                error_code="TOOL_EFFECT_COMPLETION_UNAVAILABLE",
                error="AIAT could not persist the worker-tool result",
                idempotency_key=request_id,
            )
        return response_json

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


def _mcp_effect_request_id(run_id: UUID, ctx: Context) -> UUID:
    """Derive a durable identity from the supported MCP session/request context.

    JSON-RPC request IDs are scoped to an MCP session. Including the session ID
    prevents an ID reset after a runtime restart from colliding with an earlier
    request; it deliberately does not claim cross-session replay protection.
    """

    request_context = ctx.request_context
    request_id = request_context.request_id
    request = request_context.request
    headers = getattr(request, "headers", None)
    session_id = headers.get("mcp-session-id") if headers is not None else None
    if type(request_id) is int:
        if request_id < 0:
            raise ValueError("MCP request ID is out of range")
        request_identity = {"type": "integer", "value": request_id}
    elif type(request_id) is str:
        if not request_id or len(request_id) > _MAX_MCP_REQUEST_ID_LENGTH:
            raise ValueError("MCP request ID is invalid")
        request_identity = {"type": "string", "value": request_id}
    else:
        raise TypeError("MCP request ID must be an integer or string")
    if (
        type(session_id) is not str
        or not session_id.strip()
        or len(session_id) > _MAX_MCP_SESSION_ID_LENGTH
    ):
        raise ValueError("MCP session ID is invalid")
    name = json.dumps(
        {"session_id": session_id, "request_id": request_identity},
        sort_keys=True,
        separators=(",", ":"),
    )
    return uuid5(run_id, name)


def _effect_request_sha256(request: WorkerToolRequest) -> str:
    payload = request.model_dump(mode="json")
    payload.pop("request_id", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _effect_repository(parent: FastAPI) -> Any | None:
    """Find the durable effect repository without using Redis or usage writes."""

    repository = getattr(parent.state, "worker_tool_effects", None)
    if repository is not None:
        return repository
    storage = getattr(parent.state, "usage_storage", None)
    return getattr(storage, "worker_tool_effects", None)


def _completed_replay(
    raw_response: Any,
    run_id: UUID,
    tool_name: str,
    request_id: UUID,
) -> dict[str, Any] | None:
    if isinstance(raw_response, str):
        try:
            raw_response = json.loads(raw_response)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_response, dict):
        return None
    try:
        response = ToolResponse.model_validate(raw_response)
    except (TypeError, ValueError):
        return None
    if (
        response.worker_run_id != run_id
        or response.tool_name != tool_name
        or response.idempotency_key != request_id
    ):
        return None
    return response.model_dump(mode="json", exclude_none=True)


def _error_response_model(
    tool_name: str,
    run_id: UUID,
    *,
    error_code: str,
    error: str,
    idempotency_key: UUID | None = None,
) -> ToolResponse:
    return ToolResponse(
        tool_name=tool_name,
        worker_run_id=run_id,
        idempotency_key=idempotency_key,
        success=False,
        error_code=error_code,
        error=error,
    )


def _error_response(
    tool_name: str,
    run_id: UUID,
    *,
    error_code: str,
    error: str,
    idempotency_key: UUID | None = None,
) -> dict[str, Any]:
    return _error_response_model(
        tool_name,
        run_id,
        error_code=error_code,
        error=error,
        idempotency_key=idempotency_key,
    ).model_dump(mode="json", exclude_none=True)


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
