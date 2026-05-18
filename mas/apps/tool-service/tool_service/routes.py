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
from pydantic import BaseModel, ConfigDict, Field

from mas_core.protocols.tool import ToolRequest, ToolResponse

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


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
    tool_name: str | None = None
    tool_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        alias="kwargs",
        serialization_alias="kwargs",
    )
    idempotency_key: str | None = None
    trace_id: str | None = None
    span_id: str | None = None


def _verify_secret(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Verify the Bearer token matches TOOL_SECRET (if configured)."""
    if not settings.tool_secret:
        return  # No secret configured — allow all
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != settings.tool_secret:
        raise HTTPException(status_code=403, detail="Invalid tool secret.")


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

    registry: ToolRegistry = request.app.state.registry
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

    registry: ToolRegistry = request.app.state.registry
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
    limit: int = 100,
) -> dict[str, Any]:
    """Return recent tool-call audit records.

    Each record contains: actor, project_id, tool_name, timestamp, status, error.
    """
    from .registry import ToolRegistry

    registry: ToolRegistry = request.app.state.registry
    records = registry.get_audit_log(worker_id=worker_id, tool_name=tool_name, limit=limit)
    return {"records": records, "count": len(records)}
