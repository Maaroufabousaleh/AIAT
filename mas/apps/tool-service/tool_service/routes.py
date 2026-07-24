"""FastAPI routes for the tool-service.

Endpoints
---------
POST /tools/execute   Execute a tool (body: ToolRequest → ToolResponse).
GET  /tools           List all registered tools with their manifest metadata.
GET  /health          Service health + circuit-breaker states.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from mas_core.protocols.tool import ToolRequest, ToolResponse

from .caller_auth import verify_signed_caller
from .config import Settings

logger = logging.getLogger(__name__)

class SignedBodyRoute(APIRoute):
    """Cache one authoritative body for signature and model validation."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def signed_body_route_handler(request: Request):
            request.scope["aiat.tool.raw_body"] = await request.body()
            return await route_handler(request)

        return signed_body_route_handler


router = APIRouter(route_class=SignedBodyRoute)


class ToolRunRequest(BaseModel):
    """Path-driven execution body for ``POST /tools/{tool_name}/run``."""

    model_config = ConfigDict(populate_by_name=True)

    caller_id: str = Field(..., alias="agent_id", serialization_alias="agent_id")
    caller_role: str = Field(..., alias="sender_role", serialization_alias="sender_role")
    caller_team: str | None = Field(
        default=None,
        alias="sender_team",
        serialization_alias="sender_team",
    )
    project_id: str | None = None
    worker_run_id: str | None = None
    permission_scope: list[str] = Field(default_factory=list)
    budget_snapshot: dict[str, Any] | None = None
    audit_context: dict[str, Any] = Field(default_factory=dict)
    tool_name: str | None = None
    tool_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        alias="kwargs",
        serialization_alias="kwargs",
    )
    idempotency_key: str | None = None
    trace_id: str | None = None
    span_id: str | None = None


async def _verify_secret(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Verify the Bearer token matches a configured TOOL_SECRET.

    Missing deployment credentials must fail closed; an unauthenticated tool
    runner is never a safe fallback.
    """
    settings: Settings = request.app.state.settings
    if not settings.tool_secret:
        raise HTTPException(status_code=503, detail="Tool authentication is not configured.")
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != settings.tool_secret:
        raise HTTPException(status_code=403, detail="Invalid tool secret.")
    if settings.environment_is_production:
        request.state.signed_caller_id = await verify_signed_caller(
            request, settings.tool_caller_public_keys,
            getattr(request.app.state, "tool_grant_store", None),
        )


def _assert_signed_body_caller(request: Request, caller_id: str, settings: Settings) -> None:
    """A production signature must bind to the request's asserted caller."""
    signed_caller = getattr(request.state, "signed_caller_id", None)
    if signed_caller is not None and signed_caller != caller_id and signed_caller not in settings.tool_delegate_client_ids:
        raise HTTPException(403, "Signed caller does not match tool request caller")


async def _execute_via_registry(request: Request, body: ToolRequest) -> ToolResponse | JSONResponse:
    """Execute a tool through the registry pipeline."""
    from .registry import ToolRegistry

    registry: ToolRegistry = request.app.state.registry
    response = await registry.execute(body)

    if not response.success and response.error_code == "RATE_LIMITED":
        return JSONResponse(
            status_code=429,
            content=response.model_dump(mode="json"),
            headers={
                "Retry-After": str(5),
            },
        )

    return response


@router.post("/tools/execute", response_model=ToolResponse)
async def execute_tool(
    request: Request,
    body: ToolRequest,
    _auth: None = Depends(_verify_secret),
) -> ToolResponse | JSONResponse:
    """Execute a tool through the registry pipeline.

    Pipeline: auth → registry.execute (policy → breaker → rate limit → cache → run).
    """
    _assert_signed_body_caller(request, body.caller_id, request.app.state.settings)
    return await _execute_via_registry(request, body)


@router.post("/tools/{tool_name}/run", response_model=ToolResponse)
async def run_tool(
    tool_name: str,
    request: Request,
    body: ToolRunRequest,
    _auth: None = Depends(_verify_secret),
) -> ToolResponse | JSONResponse:
    """Plan-aligned tool execution endpoint.

    The path parameter is canonical. If the body also includes ``tool_name``,
    it must match the path value.
    """
    if body.tool_name and body.tool_name != tool_name:
        raise HTTPException(
            status_code=400,
            detail=f"Body tool_name {body.tool_name!r} does not match path {tool_name!r}.",
        )

    _assert_signed_body_caller(request, body.caller_id, request.app.state.settings)

    effective_body = ToolRequest.model_validate(
        body.model_dump(mode="json", by_alias=True) | {"tool_name": tool_name}
    )
    return await _execute_via_registry(request, effective_body)


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    """Return the tool manifest — all registered tools with metadata."""
    from .registry import ToolRegistry

    registry: ToolRegistry = request.app.state.registry
    manifest = registry.get_manifest()
    return {"tools": manifest, "count": len(manifest)}


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Service health including cache status and circuit-breaker states."""
    from .cache import ToolCache
    from .registry import ToolRegistry

    registry: ToolRegistry = request.app.state.registry
    cache: ToolCache | None = getattr(request.app.state, "cache", None)

    cache_ok = False
    if cache:
        cache_ok = await cache.healthcheck()

    return {
        "status": "ok",
        "tools_registered": len(registry.tool_names),
        "cache_connected": cache_ok,
        "circuit_breakers": registry.get_breaker_states(),
    }


# ---------------------------------------------------------------------------
# Per-worker tool grants
# ---------------------------------------------------------------------------


class WorkerGrantBody(BaseModel):
    tool_name: str = Field(..., description="Tool name to grant to the worker.")


class WorkerIdentityRevokeBody(BaseModel):
    retired: bool = False


def _grant_store_or_raise(request: Request):  # noqa: ANN202
    """Require durable storage outside a local development environment."""
    store = getattr(request.app.state, "tool_grant_store", None)
    settings: Settings = request.app.state.settings
    if store is None and settings.environment_is_production:
        raise HTTPException(503, "Durable tool-grant storage is unavailable")
    return store


def _assert_grant_admin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    signed_caller = getattr(request.state, "signed_caller_id", None)
    if settings.environment_is_production and signed_caller not in settings.tool_delegate_client_ids:
        raise HTTPException(403, "signed tool-grant administrator is required")


@router.post("/tools/workers/{worker_id}/grants", status_code=201)
async def grant_worker_tool(
    worker_id: str,
    request: Request,
    body: WorkerGrantBody,
    _auth: None = Depends(_verify_secret),
) -> dict[str, Any]:
    """Grant *worker_id* explicit access to a specific tool.

    Once at least one grant exists for a worker, they may ONLY call
    tools in their explicit grant set (in addition to passing role policy).
    """
    from .registry import ToolRegistry

    _assert_grant_admin(request)
    registry: ToolRegistry = request.app.state.registry
    store = _grant_store_or_raise(request)
    if store is not None:
        await store.grant(worker_id, body.tool_name)
    registry.grant_tool(worker_id, body.tool_name)
    return {
        "worker_id": worker_id,
        "grants": registry.get_worker_grants(worker_id),
    }


@router.delete("/tools/workers/{worker_id}/grants/{tool_name}", status_code=200)
async def revoke_worker_tool(
    worker_id: str,
    tool_name: str,
    request: Request,
    _auth: None = Depends(_verify_secret),
) -> dict[str, Any]:
    """Revoke a specific tool grant from *worker_id*."""
    from .registry import ToolRegistry

    _assert_grant_admin(request)
    registry: ToolRegistry = request.app.state.registry
    store = _grant_store_or_raise(request)
    # Persist first; a failed storage operation must not make an in-memory
    # revocation look successful only to be undone by a service restart.
    if store is not None and not await store.revoke(worker_id, tool_name):
        raise HTTPException(
            status_code=404, detail=f"No grant for tool '{tool_name}' on worker '{worker_id}'."
        )
    removed = registry.revoke_tool(worker_id, tool_name)
    if not removed:
        raise HTTPException(
            status_code=404, detail=f"No grant for tool '{tool_name}' on worker '{worker_id}'."
        )
    return {
        "worker_id": worker_id,
        "revoked_tool": tool_name,
        "grants": registry.get_worker_grants(worker_id),
    }


@router.post("/tools/workers/{worker_id}/browser-identity", status_code=201)
async def provision_worker_browser_identity(
    worker_id: str,
    request: Request,
    _auth: None = Depends(_verify_secret),
) -> dict[str, Any]:
    """Persist the local browser namespace before worker activation."""
    _assert_grant_admin(request)
    store = _grant_store_or_raise(request)
    if store is None:
        return {"worker_id": worker_id, "state": "DEVELOPMENT_ONLY"}
    return await store.ensure_browser_identity(worker_id)


@router.post("/tools/workers/{worker_id}/identity-access/revoke")
async def revoke_worker_identity_access(
    worker_id: str,
    body: WorkerIdentityRevokeBody,
    request: Request,
    _auth: None = Depends(_verify_secret),
) -> dict[str, Any]:
    """Revoke local grants and live contexts during suspension/retirement."""
    from .registry import ToolRegistry
    from .tools.browser import revoke_worker_browser_sessions

    _assert_grant_admin(request)
    registry: ToolRegistry = request.app.state.registry
    store = _grant_store_or_raise(request)
    durable_removed = 0
    browser_identity = None
    if store is not None:
        durable_removed = await store.revoke_identity_grants(worker_id)
        browser_identity = await store.revoke_browser_identity(
            worker_id, retired=body.retired
        )
    live_removed = registry.revoke_identity_tools(worker_id)
    closed_sessions = await revoke_worker_browser_sessions(worker_id)
    return {
        "worker_id": worker_id,
        "durable_grants_revoked": durable_removed,
        "live_grants_revoked": live_removed,
        "browser_sessions_closed": closed_sessions,
        "browser_identity_state": (
            browser_identity.get("state") if browser_identity else "ABSENT"
        ),
    }


@router.get("/tools/workers/{worker_id}/grants")
async def get_worker_grants(
    worker_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the explicit tool grant list for *worker_id*."""
    from .registry import ToolRegistry

    registry: ToolRegistry = request.app.state.registry
    return {
        "worker_id": worker_id,
        "grants": registry.get_worker_grants(worker_id),
    }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/tools/audit")
async def get_audit_log(
    request: Request,
    worker_id: str | None = None,
    tool_name: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return recent tool-call audit records.

    Each record contains: actor, project_id, tool_name, timestamp, status, error.
    """
    from .registry import ToolRegistry

    registry: ToolRegistry = request.app.state.registry
    records = registry.get_audit_log(
        worker_id=worker_id,
        tool_name=tool_name,
        project_id=project_id,
        limit=limit,
    )
    return {"records": records, "count": len(records)}
