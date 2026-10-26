"""The fixed AIAT-to-OpenHands MCP bridge.

OpenHands receives one run-scoped grant through its operator-provisioned
profile.  The mounted app exposes exactly one generic ``aiat_tool`` operation;
there is deliberately no endpoint for registering or discovering arbitrary
external MCP servers.  Every call is reconstructed as an AIAT ``ToolRequest``
so the caller identity, worker run, grants, policy, rate limits, and audit path
come from the control plane rather than OpenHands request fields.
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

from mas_core.policy.privileged_ops import PRIVILEGED_ACTIONS
from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest, ToolResponse
from mas_core.worker_contract.models import WorkerToolRequest
from mas_core.worker_contract.openhands_bridge import (
    OpenHandsToolGrant,
    OpenHandsToolGrantError,
    verify_openhands_tool_grant,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


_grant_context: ContextVar[OpenHandsToolGrant | None] = ContextVar("openhands_mcp_grant", default=None)
_GRANT_HEADER = b"x-aiat-openhands-grant"
_MAX_MCP_REQUEST_ID_LENGTH = 256
_MAX_MCP_SESSION_ID_LENGTH = 512
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


async def _execute_granted_tool(
    parent: FastAPI,
    grant: OpenHandsToolGrant,
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    request_id: UUID,
) -> dict[str, Any]:
    """Dispatch one run-scoped tool only after durable effect admission."""

    registry = parent.state.registry
    if tool_name not in grant.tool_names:
        return _error_response(tool_name, grant.run_id, request_id, "FORBIDDEN", "AIAT tool grant denied")
    if _requires_approval(registry, tool_name):
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "APPROVAL_REQUIRED",
            "approval-required AIAT tools cannot execute through the worker bridge",
        )
    if not grant.claim_owner or grant.attempt_count < 1:
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "WORKER_RUN_CLAIM_LOST",
            "OpenHands tool grant is not bound to a claimed Worker Run attempt",
        )

    effect_repository = _effect_repository(parent)
    begin_effect = getattr(effect_repository, "begin_worker_tool_effect", None)
    complete_effect = getattr(effect_repository, "complete_worker_tool_effect", None)
    validate_claim = getattr(effect_repository, "validate_worker_run_claim", None)
    if not callable(begin_effect) or not callable(complete_effect) or not callable(validate_claim):
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "TOOL_EFFECT_LEDGER_UNAVAILABLE",
            "AIAT durable worker-tool effect storage is unavailable",
        )

    tool_arguments = dict(arguments or {})
    effect_request = WorkerToolRequest(
        request_id=request_id,
        run_id=grant.run_id,
        tool_name=tool_name,
        arguments=tool_arguments,
        permission_scope=sorted(grant.tool_names),
        approval_required=False,
        idempotency_key=str(request_id),
    )
    request_sha256 = _effect_request_sha256(effect_request)
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
            request_id,
            "TOOL_EFFECT_LEDGER_UNAVAILABLE",
            "AIAT could not reserve a durable worker-tool effect",
        )
    if not isinstance(effect, dict):
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "TOOL_EFFECT_LEDGER_UNAVAILABLE",
            "AIAT returned an invalid worker-tool effect reservation",
        )
    disposition = str(effect.get("disposition") or "").upper()
    if disposition == "CLAIM_LOST":
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "WORKER_RUN_CLAIM_LOST",
            "Worker Run claim is no longer current; tool dispatch was denied",
        )
    if disposition == "CONFLICT":
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "TOOL_IDEMPOTENCY_CONFLICT",
            "OpenHands reused a tool request identity with different arguments",
        )
    if disposition == "REPLAY":
        if (
            not isinstance(effect.get("id"), UUID)
            or str(effect.get("run_id") or "") != str(grant.run_id)
            or effect.get("idempotency_key") != str(request_id)
            or effect.get("tool_name") != tool_name
            or effect.get("request_sha256") != request_sha256
            or str(effect.get("state") or "").upper() != "COMPLETED"
        ):
            return _error_response(
                tool_name,
                grant.run_id,
                request_id,
                "TOOL_EFFECT_AMBIGUOUS",
                "AIAT found an invalid completed worker-tool effect record",
            )
        replay = _completed_replay(effect.get("response_json"), grant.run_id, tool_name, request_id)
        return replay or _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "TOOL_EFFECT_AMBIGUOUS",
            "AIAT found an invalid completed worker-tool effect record",
        )
    if disposition != "NEW":
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "TOOL_EFFECT_AMBIGUOUS",
            "AIAT cannot safely replay this worker-tool effect",
        )
    if (
        not isinstance(effect.get("id"), UUID)
        or str(effect.get("run_id") or "") != str(grant.run_id)
        or effect.get("idempotency_key") != str(request_id)
        or effect.get("tool_name") != tool_name
        or effect.get("request_sha256") != request_sha256
        or str(effect.get("state") or "").upper() != "IN_FLIGHT"
    ):
        return _error_response(
            tool_name,
            grant.run_id,
            request_id,
            "TOOL_EFFECT_LEDGER_UNAVAILABLE",
            "AIAT returned an invalid worker-tool effect reservation",
        )

    try:
        claim_current = await validate_claim(
            run_id=grant.run_id,
            claim_owner=grant.claim_owner,
            attempt_count=grant.attempt_count,
        )
    except Exception:
        claim_current = False
    request = ToolRequest(
        caller_id=grant.worker_id,
        caller_role=AgentRole.WORKER,
        # ``OpenHandsToolGrant`` keeps the project as a UUID for signed
        # capability validation, while the cross-service ToolRequest
        # protocol carries the audit project identifier as text.
        project_id=str(grant.project_id) if grant.project_id else None,
        worker_run_id=grant.run_id,
        permission_scope=sorted(grant.tool_names),
        audit_context={
            "bridge": "openhands_mcp",
            "worker_run_id": str(grant.run_id),
            "grant_id": grant.grant_id,
        },
        tool_name=tool_name,
        tool_kwargs=tool_arguments,
        idempotency_key=request_id,
    )
    if not claim_current:
        response = _error_response_model(
            tool_name,
            grant.run_id,
            request_id,
            "WORKER_RUN_CLAIM_LOST",
            "Worker Run claim expired or was reassigned before tool dispatch",
        )
        return await _complete_effect_or_error(
            complete_effect,
            effect,
            grant.run_id,
            tool_name,
            request_id,
            claim_owner=grant.claim_owner,
            attempt_count=grant.attempt_count,
            state="AMBIGUOUS",
            response=response,
        )

    try:
        response = await registry.execute(request)
        if (
            response.worker_run_id != grant.run_id
            or response.tool_name != tool_name
            or response.idempotency_key != request_id
        ):
            raise ValueError("ToolRegistry returned a mismatched worker response")
    except Exception:
        response = _error_response_model(
            tool_name,
            grant.run_id,
            request_id,
            "TOOL_EFFECT_AMBIGUOUS",
            "AIAT tool execution ended without a durable result",
        )
        return await _complete_effect_or_error(
            complete_effect,
            effect,
            grant.run_id,
            tool_name,
            request_id,
            claim_owner=grant.claim_owner,
            attempt_count=grant.attempt_count,
            state="AMBIGUOUS",
            response=response,
        )
    return await _complete_effect_or_error(
        complete_effect,
        effect,
        grant.run_id,
        tool_name,
        request_id,
        claim_owner=grant.claim_owner,
        attempt_count=grant.attempt_count,
        state="COMPLETED",
        response=response,
    )


async def _complete_effect_or_error(
    complete_effect: Any,
    effect: dict[str, Any],
    run_id: UUID,
    tool_name: str,
    request_id: UUID,
    *,
    claim_owner: str | None,
    attempt_count: int,
    state: str,
    response: ToolResponse,
) -> dict[str, Any]:
    response_json = response.model_dump(mode="json", exclude_none=True)
    try:
        persisted = await complete_effect(
            effect["id"],
            run_id=run_id,
            claim_owner=claim_owner,
            attempt_count=attempt_count,
            state=state,
            response_json=response_json,
        )
    except Exception:
        persisted = None
    if (
        not isinstance(persisted, dict)
        or str(persisted.get("id") or "") != str(effect.get("id") or "")
        or str(persisted.get("run_id") or "") != str(run_id)
        or persisted.get("idempotency_key") != str(request_id)
        or persisted.get("tool_name") != tool_name
        or persisted.get("request_sha256") != effect.get("request_sha256")
        or str(persisted.get("state") or "").upper() != state
        or persisted.get("response_json") != response_json
    ):
        return _error_response(
            tool_name,
            run_id,
            request_id,
            "TOOL_EFFECT_COMPLETION_UNAVAILABLE",
            "AIAT could not persist the worker-tool result",
        )
    return response_json


def _effect_repository(parent: FastAPI) -> Any | None:
    repository = getattr(parent.state, "worker_tool_effects", None)
    if repository is not None:
        return repository
    storage = getattr(parent.state, "usage_storage", None)
    return getattr(storage, "worker_tool_effects", None)


def _effect_request_sha256(tool_request: WorkerToolRequest) -> str:
    payload = tool_request.model_dump(mode="json")
    payload.pop("request_id", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _completed_replay(raw_response: Any, run_id: UUID, tool_name: str, request_id: UUID) -> dict[str, Any] | None:
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
    request_id: UUID,
    error_code: str,
    error: str,
) -> ToolResponse:
    return ToolResponse(
        tool_name=tool_name,
        worker_run_id=run_id,
        idempotency_key=request_id,
        success=False,
        error_code=error_code,
        error=error,
    )


def _error_response(
    tool_name: str,
    run_id: UUID,
    request_id: UUID,
    error_code: str,
    error: str,
) -> dict[str, Any]:
    return _error_response_model(tool_name, run_id, request_id, error_code, error).model_dump(
        mode="json", exclude_none=True
    )


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
        ctx: Context,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one explicitly granted, non-privileged AIAT tool."""

        grant = _grant_context.get()
        if grant is None:
            return {"success": False, "error_code": "FORBIDDEN", "error": "AIAT tool grant denied"}
        try:
            request_id = _mcp_effect_request_id(grant.run_id, ctx)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return _error_response(
                tool_name,
                grant.run_id,
                uuid5(grant.run_id, "invalid-mcp-request-identity"),
                "TOOL_REQUEST_IDENTITY_UNAVAILABLE",
                "AIAT could not establish a stable MCP request identity",
            )
        return await _execute_granted_tool(
            parent,
            grant,
            tool_name,
            arguments,
            request_id=request_id,
        )

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


def _mcp_effect_request_id(run_id: UUID, ctx: Context) -> UUID:
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
    if type(session_id) is not str or not session_id.strip() or len(session_id) > _MAX_MCP_SESSION_ID_LENGTH:
        raise ValueError("MCP session ID is invalid")
    identity = json.dumps(
        {"session_id": session_id, "request_id": request_identity},
        sort_keys=True,
        separators=(",", ":"),
    )
    return uuid5(run_id, identity)


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
