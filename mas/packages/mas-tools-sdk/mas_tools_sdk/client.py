"""ToolServiceClient — async HTTP client that agents use to call tools.

Wraps ``POST /tools/execute`` on the tool-service. Handles:
- 429 rate-limit back-off (exponential, 3 retries)
- Circuit-breaker detection from ``ToolResponse.circuit_state``
- Trace/span propagation
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest, ToolResponse

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0
_BACKOFF_FACTOR = 2.0


class ToolServiceClient:
    """Async HTTP client for calling tools via the tool-service.

    Parameters
    ----------
    base_url : str
        Tool-service URL (e.g. ``"http://tool-service:8002"``).
    secret : str | None
        Shared secret for ``Authorization: Bearer <secret>`` header.
    timeout : float
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        *,
        secret: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )
        self._base_url = base_url

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def execute(
        self,
        *,
        tool_name: str,
        caller_id: str,
        caller_role: AgentRole,
        caller_team: str | None = None,
        kwargs: dict[str, Any] | None = None,
        project_id: str | None = None,
        idempotency_key: UUID | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> ToolResponse:
        """Call a tool on the tool-service.

        Retries on 429 with exponential back-off.

        Returns
        -------
        ToolResponse
            Always returns a ToolResponse — errors are encoded inside it.
        """
        request = ToolRequest(
            caller_id=caller_id,
            caller_role=caller_role,
            caller_team=caller_team,
            project_id=project_id,
            tool_name=tool_name,
            tool_kwargs=kwargs or {},
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            span_id=span_id,
        )

        payload = request.model_dump(mode="json", by_alias=True)
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.post(f"/tools/{tool_name}/run", json=payload)

                if resp.status_code == 404:
                    resp = await self._client.post("/tools/execute", json=payload)

                if resp.status_code == 429:
                    # Rate limited — back off and retry
                    retry_after = float(
                        resp.headers.get("Retry-After", _BACKOFF_BASE * (_BACKOFF_FACTOR ** attempt))
                    )
                    logger.warning(
                        "tool_service_rate_limited",
                        extra={
                            "tool_name": tool_name,
                            "attempt": attempt + 1,
                            "retry_after": retry_after,
                        },
                    )
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()
                return ToolResponse.model_validate(resp.json())

            except httpx.HTTPStatusError as exc:
                # Non-429 HTTP error — wrap in ToolResponse
                return ToolResponse(
                    tool_name=tool_name,
                    idempotency_key=idempotency_key,
                    success=False,
                    error=f"HTTP {exc.response.status_code}: {exc.response.text[:500]}",
                    error_code="TOOL_ERROR",
                    trace_id=trace_id,
                    span_id=span_id,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exc = exc
                wait = _BACKOFF_BASE * (_BACKOFF_FACTOR ** attempt)
                logger.warning(
                    "tool_service_connection_error",
                    extra={
                        "tool_name": tool_name,
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "retry_in": wait,
                    },
                )
                await asyncio.sleep(wait)

        # Exhausted retries
        return ToolResponse(
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            success=False,
            error=f"Tool service unreachable after {_MAX_RETRIES} attempts: {last_exc}",
            error_code="TOOL_ERROR",
            trace_id=trace_id,
            span_id=span_id,
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        """Fetch the tool manifest from GET /tools."""
        resp = await self._client.get("/tools")
        resp.raise_for_status()
        data = resp.json()
        return data.get("tools", [])

    async def health(self) -> dict[str, Any]:
        """Check tool-service health."""
        resp = await self._client.get("/health")
        resp.raise_for_status()
        return resp.json()
