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

from mas_core.protocols.tool import ToolRequest, ToolResponse

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.post("/tools/execute", response_model=ToolResponse)
async def execute_tool(
    request: Request,
    body: ToolRequest,
    _auth: None = Depends(_verify_secret),
) -> ToolResponse | JSONResponse:
    """Execute a tool through the registry pipeline.

    Pipeline: auth → registry.execute (policy → breaker → rate limit → cache → run).
    """
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
