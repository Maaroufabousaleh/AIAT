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
