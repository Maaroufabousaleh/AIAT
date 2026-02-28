"""ToolRegistry — discovers, wraps, and dispatches tools.

Responsibilities:
1. Discover ``BaseTool`` subclasses and build ``tool_name → instance`` map.
2. Gate access via ``CommunicationPolicy.can_use_tool()``.
3. Check circuit breakers, rate limiters, and cache before dispatching.
4. Return ``ToolResponse`` with metadata.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from mas_core.policy.engine import CommunicationPolicy
from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest, ToolResponse
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup
from mas_tools_sdk.manifest import TOOL_MANIFEST

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
        entries = []
        for tool in self._tools.values():
            entries.append(tool.to_manifest_entry())
        return entries

    def get_breaker_states(self) -> list[dict]:
        """Return breaker snapshots (for /health)."""
        return [b.to_dict() for b in self._breakers.values()]

    async def execute(self, request: ToolRequest) -> ToolResponse:
        """Execute a tool request through the full pipeline.

        Pipeline: policy check → circuit breaker → rate limiter → cache → execute → cache set.
        """
        tool_name = request.tool_name
        t0 = time.monotonic()

        # 1. Tool exists?
        tool = self._tools.get(tool_name)
        if tool is None:
            return self._error_response(
                request, error=f"Tool '{tool_name}' not found.", error_code="TOOL_NOT_FOUND"
            )

        # 2. Policy gating
        result = self._policy.can_use_tool(
            request.caller_role,
            tool_name,
            sender_team=request.caller_team,
        )
        if result is not True:
            return self._error_response(
                request,
                error=f"Access denied: {result}",
                error_code="FORBIDDEN",
                circuit_state=self._breakers[tool_name].state,
            )

        # 3. Circuit breaker
        breaker = self._breakers[tool_name]
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
            cached = await self._cache.get(tool_name, kwargs)
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
            if tool_name in self._semaphores:
                async with self._semaphores[tool_name]:
                    result_val = await tool.execute(**kwargs)
            else:
                result_val = await tool.execute(**kwargs)
        except Exception as exc:
            await breaker.record_failure()
            duration = (time.monotonic() - t0) * 1000
            logger.error(
                "tool_execution_error",
                extra={"tool": tool_name, "error": str(exc)},
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
            await self._cache.set(tool_name, kwargs, result_val, ttl=tool.cache_ttl_seconds)

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
