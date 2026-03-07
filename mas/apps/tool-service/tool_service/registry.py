"""ToolRegistry — discovers, wraps, and dispatches tools.

Responsibilities:
1. Discover ``BaseTool`` subclasses and build ``tool_name → instance`` map.
2. Gate access via ``CommunicationPolicy.can_use_tool()``.
3. Check circuit breakers, rate limiters, and cache before dispatching.
4. Return ``ToolResponse`` with metadata.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from mas_core.policy.engine import CommunicationPolicy
from mas_core.protocols.tool import ToolRequest, ToolResponse
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.manifest import all_manifest_entries, resolve_tool_name

from .cache import ToolCache
from .circuit_breaker import CircuitBreaker
from .config import Settings
from .rate_limiter import RateLimiterPool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central registry that holds tool instances and dispatch logic.

    Parameters
    ----------
    settings : Settings
        Service configuration.
    cache : ToolCache | None
        Redis cache (optional — no caching if None).
    rate_limiter : RateLimiterPool | None
        Rate limiter (optional — no limiting if None).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        cache: ToolCache | None = None,
        rate_limiter: RateLimiterPool | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._rate_limiter = rate_limiter or RateLimiterPool()
        self._policy = CommunicationPolicy()

        # tool_name → BaseTool instance
        self._tools: dict[str, BaseTool] = {}
        # tool_name → CircuitBreaker
        self._breakers: dict[str, CircuitBreaker] = {}
        # tool_name → asyncio.Semaphore (concurrency limit)
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a single tool instance."""
        self._tools[tool.name] = tool
        self._breakers[tool.name] = CircuitBreaker(
            tool.name,
            failure_threshold=self._settings.cb_failure_threshold,
            failure_window=self._settings.cb_failure_window_seconds,
            open_duration=self._settings.cb_open_duration_seconds,
        )
        if tool.max_concurrency > 0:
            self._semaphores[tool.name] = asyncio.Semaphore(tool.max_concurrency)
        logger.info("tool_registered", extra={"tool": tool.name, "group": tool.group.value})

    def register_all(self, tools: list[BaseTool]) -> None:
        """Batch-register tools."""
        for t in tools:
            self.register(t)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_manifest(self) -> list[dict[str, Any]]:
        """Return the full tool manifest (for GET /tools)."""
        return all_manifest_entries(include_aliases=True)

    def get_breaker_states(self) -> list[dict]:
        """Return breaker snapshots (for /health)."""
        return [b.to_dict() for b in self._breakers.values()]

    async def execute(self, request: ToolRequest) -> ToolResponse:
        """Execute a tool request through the full pipeline.

        Pipeline: policy check → circuit breaker → rate limiter → cache → execute → cache set.
        """
        tool_name = request.tool_name
        resolved_tool_name = resolve_tool_name(tool_name)
        if resolved_tool_name is None:
            return self._error_response(
                request, error=f"Tool '{tool_name}' not found.", error_code="TOOL_NOT_FOUND"
            )
        is_alias = resolved_tool_name != tool_name
        t0 = time.monotonic()

        # 1. Tool exists?
        tool = self._tools.get(resolved_tool_name)
        if tool is None:
            return self._error_response(
                request, error=f"Tool '{tool_name}' not found.", error_code="TOOL_NOT_FOUND"
            )

        # 2. Policy gating
        result = self._policy.can_use_tool(
            request.caller_role,
            resolved_tool_name,
            sender_team=request.caller_team,
        )
        if result is not True:
            return self._error_response(
                request,
                error=f"Access denied: {result}",
                error_code="FORBIDDEN",
                circuit_state=self._breakers[resolved_tool_name].state,
            )

        # 3. Circuit breaker
        breaker = self._breakers[resolved_tool_name]
        if not await breaker.allow_request():
            return self._error_response(
                request,
                error=f"Circuit breaker OPEN for '{tool_name}'.",
                error_code="CIRCUIT_OPEN",
                circuit_state=breaker.state,
            )

        # 4. Rate limiter
        group = tool.group
        if self._rate_limiter:
            allowed, remaining, reset_at = await self._rate_limiter.acquire(group)
            if not allowed:
                return self._error_response(
                    request,
                    error=f"Rate limit exceeded for group '{group.value}'.",
                    error_code="RATE_LIMITED",
                    circuit_state=breaker.state,
                    rate_limit_remaining=0,
                    rate_limit_reset_at=reset_at,
                )
        else:
            remaining, reset_at = None, None

        # 5. Cache lookup (only for idempotent tools)
        kwargs = request.tool_kwargs
        if tool.idempotent and tool.cache_ttl_seconds > 0 and self._cache:
            cached = await self._cache.get(resolved_tool_name, kwargs)
            if cached is not None:
                duration = (time.monotonic() - t0) * 1000
                return ToolResponse(
                    tool_name=tool_name,
                    idempotency_key=request.idempotency_key,
                    success=True,
                    result=cached,
                    cached=True,
                    circuit_state=breaker.state,
                    rate_limit_remaining=remaining,
                    rate_limit_reset_at=reset_at,
                    duration_ms=round(duration, 2),
                    trace_id=request.trace_id,
                    span_id=request.span_id,
                )

        # 6. Execute (with semaphore if configured)
        try:
            if resolved_tool_name in self._semaphores:
                async with self._semaphores[resolved_tool_name]:
                    result_val = await self._execute_tool(tool, resolved_tool_name, kwargs)
            else:
                result_val = await self._execute_tool(tool, resolved_tool_name, kwargs)
        except Exception as exc:
            await breaker.record_failure()
            duration = (time.monotonic() - t0) * 1000
            logger.error(
                "tool_execution_error",
                extra={"tool": resolved_tool_name, "requested_tool": tool_name, "error": str(exc)},
                exc_info=True,
            )
            return self._error_response(
                request,
                error=str(exc),
                error_code="TOOL_ERROR",
                circuit_state=breaker.state,
                duration_ms=round(duration, 2),
                rate_limit_remaining=remaining,
                rate_limit_reset_at=reset_at,
            )

        # 7. Record success + cache
        await breaker.record_success()
        duration = (time.monotonic() - t0) * 1000

        if tool.idempotent and tool.cache_ttl_seconds > 0 and self._cache:
            await self._cache.set(resolved_tool_name, kwargs, result_val, ttl=tool.cache_ttl_seconds)

        if is_alias and isinstance(result_val, dict):
            result_val = dict(result_val)
            result_val.setdefault("_canonical_tool", resolved_tool_name)

        return ToolResponse(
            tool_name=tool_name,
            idempotency_key=request.idempotency_key,
            success=True,
            result=result_val,
            cached=False,
            circuit_state=breaker.state,
            rate_limit_remaining=remaining,
            rate_limit_reset_at=reset_at,
            duration_ms=round(duration, 2),
            trace_id=request.trace_id,
            span_id=request.span_id,
        )

    async def _execute_tool(self, tool: BaseTool, tool_name: str, kwargs: dict[str, Any]) -> Any:
        """Dispatch execution using the configured transport adapter."""
        transport = getattr(tool, "transport", "internal")
        if transport == "internal":
            return await tool.execute(**kwargs)
        if transport == "http":
            return await self._execute_http_transport(tool_name, kwargs)
        if transport == "mcp":
            return await self._execute_mcp_transport(tool_name, kwargs)
        if transport == "process":
            return await self._execute_process_transport(tool_name, kwargs)
        raise ValueError(f"Unsupported tool transport '{transport}' for tool '{tool_name}'")

    async def _execute_http_transport(self, tool_name: str, kwargs: dict[str, Any]) -> Any:
        endpoint = self._settings.http_transport_endpoints.get(tool_name)
        if not endpoint:
            raise ValueError(f"No HTTP transport endpoint configured for tool '{tool_name}'")
        timeout = self._settings.transport_request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json={"tool_name": tool_name, "kwargs": kwargs})
            resp.raise_for_status()
            return resp.json()

    async def _execute_mcp_transport(self, tool_name: str, kwargs: dict[str, Any]) -> Any:
        endpoint = self._settings.mcp_transport_endpoints.get(tool_name)
        if not endpoint:
            raise ValueError(f"No MCP transport endpoint configured for tool '{tool_name}'")
        timeout = self._settings.transport_request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                endpoint,
                json={"method": "tool.run", "params": {"tool_name": tool_name, "kwargs": kwargs}},
            )
            resp.raise_for_status()
            return resp.json()

    async def _execute_process_transport(self, tool_name: str, kwargs: dict[str, Any]) -> Any:
        command = self._settings.process_transport_commands.get(tool_name)
        if not command:
            raise ValueError(f"No process transport command configured for tool '{tool_name}'")
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = json.dumps({"tool_name": tool_name, "kwargs": kwargs}).encode("utf-8")
        stdout, stderr = await proc.communicate(payload)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Process transport failed for '{tool_name}': {stderr.decode('utf-8', errors='replace')}"
            )
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"output": text}

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _error_response(
        request: ToolRequest,
        *,
        error: str,
        error_code: str,
        circuit_state: Any = None,
        duration_ms: float | None = None,
        rate_limit_remaining: int | None = None,
        rate_limit_reset_at: datetime | None = None,
    ) -> ToolResponse:
        from mas_core.protocols.tool import CircuitState

        return ToolResponse(
            tool_name=request.tool_name,
            idempotency_key=request.idempotency_key,
            success=False,
            error=error,
            error_code=error_code,
            circuit_state=circuit_state or CircuitState.CLOSED,
            duration_ms=duration_ms,
            rate_limit_remaining=rate_limit_remaining,
            rate_limit_reset_at=rate_limit_reset_at,
            trace_id=request.trace_id,
            span_id=request.span_id,
        )
