"""ToolRegistry — discovers, wraps, and dispatches tools.

Pipeline: policy check → circuit breaker → rate limiter → cache → execute → cache set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx

from mas_core.observability.metrics import (
    MAS_TOOL_CALLS_TOTAL,
    TOOL_ERRORS_TOTAL,
    TOOL_INVOCATIONS_TOTAL,
    set_tool_circuit_state,
)
from mas_core.policy.engine import CommunicationPolicy
from mas_core.policy.tool_access import can_use_tool_with_metadata
from mas_core.protocols.tool import ToolRequest, ToolResponse
from mas_tools_sdk.manifest import TOOL_ALIASES, resolve_tool_name

from .circuit_breaker import CircuitBreaker
from .rate_limiter import RateLimiterPool

if TYPE_CHECKING:
    from mas_tools_sdk.base import BaseTool

    from .cache import ToolCache
    from .config import Settings
    from .usage import ProjectUsageWriter

logger = logging.getLogger(__name__)

# Maximum number of audit records to keep in the ring buffer.
_AUDIT_RING_SIZE = 1000


class ToolRegistry:
    """Central registry that holds tool instances and dispatch logic."""

    def __init__(
        self,
        settings: Settings,
        *,
        cache: ToolCache | None = None,
        rate_limiter: RateLimiterPool | None = None,
        usage_storage: ProjectUsageWriter | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._rate_limiter = rate_limiter or RateLimiterPool()
        self._policy = CommunicationPolicy()
        self._usage_storage = usage_storage

        self._tools: dict[str, BaseTool] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

        # Per-worker explicit tool-grant allowlist.
        # If a worker_id has an entry here, they may ONLY call the tools in
        # that set (in addition to passing role-based policy).
        # If a worker_id is NOT in this dict, only role-based policy applies.
        self._worker_grants: dict[str, set[str]] = {}

        # In-memory ring buffer for tool-call audit records.
        self._audit_log: deque[dict[str, Any]] = deque(maxlen=_AUDIT_RING_SIZE)

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

    def set_cache(self, cache: ToolCache | None) -> None:
        """Replace the cache backend after Redis reconnects or disconnects."""
        self._cache = cache

    def set_usage_storage(self, usage_storage: ProjectUsageWriter | None) -> None:
        """Replace the durable usage writer after Postgres recovery."""
        self._usage_storage = usage_storage

    # ------------------------------------------------------------------
    # Per-worker tool grants
    # ------------------------------------------------------------------

    def grant_tool(self, worker_id: str, tool_name: str) -> None:
        """Grant *worker_id* explicit access to *tool_name*.

        Once a worker has at least one explicit grant, they may ONLY call
        tools in their grant set (subject to role-based policy as well).
        """
        self._worker_grants.setdefault(worker_id, set()).add(tool_name)

    def revoke_tool(self, worker_id: str, tool_name: str) -> bool:
        """Remove *tool_name* from *worker_id*'s grant set.

        Returns True if the grant existed and was removed.
        """
        grants = self._worker_grants.get(worker_id)
        if grants is None or tool_name not in grants:
            return False
        grants.discard(tool_name)
        return True

    def get_worker_grants(self, worker_id: str) -> list[str]:
        """Return the explicit grant list for *worker_id* (empty = role-only)."""
        return sorted(self._worker_grants.get(worker_id, set()))

    def _check_worker_grant(self, worker_id: str, tool_name: str) -> bool:
        """Return True if the worker passes the per-worker grant gate.

        If the worker has no explicit grants, this gate is open.
        If the worker has explicit grants, the tool must be in the set.
        """
        grants = self._worker_grants.get(worker_id)
        if grants is None:
            return True
        return tool_name in grants

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def _record_audit(
        self,
        *,
        actor: str,
        project_id: str | None,
        tool_name: str,
        status: str,
        team_id: str | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        """Append to the live ring and durably account project-scoped usage."""
        timestamp = datetime.now(tz=UTC)
        self._audit_log.append(
            {
                "actor": actor,
                "project_id": project_id,
                "tool_name": tool_name,
                "timestamp": timestamp.isoformat(),
                "status": status,
                "error": error,
                "duration_ms": duration_ms,
            }
        )
        if not project_id or self._usage_storage is None:
            return
        try:
            parsed_project_id = UUID(str(project_id))
        except (TypeError, ValueError):
            logger.warning(
                "tool_usage_invalid_project_id",
                extra={"project_id": project_id, "tool": tool_name},
            )
            return
        try:
            await self._usage_storage.record_project_usage(
                project_id=parsed_project_id,
                event_type="tool",
                agent_id=actor,
                team_id=team_id,
                tool_name=tool_name,
                status=status,
                duration_ms=duration_ms,
                trace_id=trace_id,
                span_id=span_id,
                details={"error": error} if error else None,
                occurred_at=timestamp,
            )
        except Exception:
            # Usage persistence must never turn an otherwise valid tool result
            # into a failed business operation.
            logger.exception(
                "tool_usage_persistence_failed",
                extra={"project_id": project_id, "tool": tool_name},
            )

    def get_audit_log(
        self,
        *,
        worker_id: str | None = None,
        tool_name: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent audit records, optionally filtered."""
        records: list[dict[str, Any]] = list(self._audit_log)
        if worker_id:
            records = [r for r in records if r["actor"] == worker_id]
        if tool_name:
            records = [r for r in records if r["tool_name"] == tool_name]
        if project_id:
            records = [r for r in records if r["project_id"] == project_id]
        return records[-limit:]

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_manifest(self) -> list[dict[str, Any]]:
        """Return registered tools and valid aliases for GET /tools."""
        from .readiness import tool_readiness

        entries = []
        for tool_name, tool in sorted(self._tools.items()):
            entry = tool.to_manifest_entry()
            entry.update(tool_readiness(tool_name, self._settings))
            entries.append(entry)
        for alias, canonical in sorted(TOOL_ALIASES.items()):
            if canonical not in self._tools:
                continue
            base = self._tools[canonical].to_manifest_entry()
            base["tool_name"] = alias
            base["canonical_tool_name"] = canonical
            base["deprecated_alias_of"] = canonical
            base["alias"] = True
            base.update(tool_readiness(canonical, self._settings))
            entries.append(base)
        return entries

    def get_breaker_states(self) -> list[dict]:
        """Return breaker snapshots (for /health)."""
        return [b.to_dict() for b in self._breakers.values()]

    async def execute(self, request: ToolRequest) -> ToolResponse:
        """Execute a tool request through the full pipeline."""
        tool_name = request.tool_name
        resolved_tool_name = resolve_tool_name(tool_name)
        if resolved_tool_name is None and tool_name in self._tools:
            resolved_tool_name = tool_name
        if resolved_tool_name is None:
            logger.warning(
                "tool_not_found",
                extra={
                    "tool_name": tool_name,
                    "caller_role": request.caller_role.value if request.caller_role else None,
                    "caller_id": request.caller_id,
                },
            )
            return self._error_response(
                request, error=f"Tool '{tool_name}' not found.", error_code="TOOL_NOT_FOUND"
            )
        is_alias = resolved_tool_name != tool_name
        t0 = time.monotonic()

        tool = self._tools.get(resolved_tool_name)
        if tool is None:
            logger.warning(
                "tool_not_registered",
                extra={
                    "tool_name": tool_name,
                    "resolved_tool_name": resolved_tool_name,
                    "caller_role": request.caller_role.value if request.caller_role else None,
                },
            )
            return self._error_response(
                request, error=f"Tool '{tool_name}' not found.", error_code="TOOL_NOT_FOUND"
            )

        logger.info(
            "tool_call_start",
            extra={
                "tool": resolved_tool_name,
                "caller_role": request.caller_role.value if request.caller_role else None,
                "caller_id": request.caller_id,
                "caller_team": request.caller_team,
                "project_id": request.project_id,
            },
        )

        result = can_use_tool_with_metadata(
            role=request.caller_role,
            tool_name=resolved_tool_name,
            sender_team=request.caller_team,
            allowed_roles=tool.allowed_roles,
            blocked_roles=tool.blocked_roles,
            policy=self._policy,
        )
        if result is not True:
            logger.warning(
                "tool_access_denied",
                extra={
                    "tool": resolved_tool_name,
                    "caller_role": request.caller_role.value if request.caller_role else None,
                    "reason": result,
                },
            )
            await self._record_audit(
                actor=request.caller_id,
                project_id=request.project_id,
                tool_name=tool_name,
                status="forbidden",
                team_id=request.caller_team,
                error=f"Access denied: {result}",
                trace_id=request.trace_id,
                span_id=request.span_id,
            )
            return self._error_response(
                request,
                error=f"Access denied: {result}",
                error_code="FORBIDDEN",
                circuit_state=self._breakers[resolved_tool_name].state,
            )

        # Per-worker grant gate (additional check on top of role policy)
        if not self._check_worker_grant(request.caller_id, resolved_tool_name):
            deny_msg = (
                f"Worker '{request.caller_id}' does not have an explicit grant for '{tool_name}'."
            )
            logger.warning(
                "tool_worker_grant_denied",
                extra={"tool": resolved_tool_name, "caller_id": request.caller_id},
            )
            await self._record_audit(
                actor=request.caller_id,
                project_id=request.project_id,
                tool_name=tool_name,
                status="forbidden",
                team_id=request.caller_team,
                error=deny_msg,
                trace_id=request.trace_id,
                span_id=request.span_id,
            )
            return self._error_response(
                request,
                error=deny_msg,
                error_code="FORBIDDEN",
                circuit_state=self._breakers[resolved_tool_name].state,
            )

        breaker = self._breakers[resolved_tool_name]
        if not await breaker.allow_request():
            logger.warning(
                "tool_circuit_open",
                extra={
                    "tool": resolved_tool_name,
                    "circuit_state": breaker.state.value,
                },
            )
            await self._record_audit(
                actor=request.caller_id,
                project_id=request.project_id,
                tool_name=tool_name,
                status="circuit_open",
                team_id=request.caller_team,
                error=f"Circuit breaker OPEN for '{tool_name}'.",
                trace_id=request.trace_id,
                span_id=request.span_id,
            )
            return self._error_response(
                request,
                error=f"Circuit breaker OPEN for '{tool_name}'.",
                error_code="CIRCUIT_OPEN",
                circuit_state=breaker.state,
            )

        group = tool.group
        if self._rate_limiter:
            allowed, remaining, reset_at = await self._rate_limiter.acquire(group)
            if not allowed:
                logger.warning(
                    "tool_rate_limited",
                    extra={
                        "tool": resolved_tool_name,
                        "group": group.value,
                    },
                )
                await self._record_audit(
                    actor=request.caller_id,
                    project_id=request.project_id,
                    tool_name=tool_name,
                    status="rate_limited",
                    team_id=request.caller_team,
                    error=f"Rate limit exceeded for group '{group.value}'.",
                    trace_id=request.trace_id,
                    span_id=request.span_id,
                )
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

        # ``project_id`` is carried in the protocol envelope for audit and
        # tracing, but older callers did not duplicate it inside tool kwargs.
        # Make that context available to tools that operate on project-scoped
        # storage (for example ``blob.download``) without changing the caller
        # contract.  An explicit tool argument remains authoritative.
        kwargs = dict(request.tool_kwargs)
        if request.project_id and "project_id" not in kwargs:
            kwargs["project_id"] = request.project_id
        if tool.idempotent and tool.cache_ttl_seconds > 0 and self._cache:
            cached = await self._cache.get(resolved_tool_name, kwargs)
            if cached is not None:
                duration = (time.monotonic() - t0) * 1000
                logger.info(
                    "tool_call_cache_hit",
                    extra={
                        "tool": resolved_tool_name,
                        "duration_ms": round(duration, 2),
                    },
                )
                await self._record_audit(
                    actor=request.caller_id,
                    project_id=request.project_id,
                    tool_name=tool_name,
                    status="success",
                    team_id=request.caller_team,
                    duration_ms=round(duration, 2),
                    trace_id=request.trace_id,
                    span_id=request.span_id,
                )
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

        try:
            if resolved_tool_name in self._semaphores:
                async with self._semaphores[resolved_tool_name]:
                    result_val = await self._execute_tool(tool, resolved_tool_name, kwargs)
            else:
                result_val = await self._execute_tool(tool, resolved_tool_name, kwargs)
        except Exception as exc:
            await breaker.record_failure()
            duration = (time.monotonic() - t0) * 1000
            MAS_TOOL_CALLS_TOTAL.labels(tool_name=resolved_tool_name, status="error").inc()
            TOOL_INVOCATIONS_TOTAL.labels(tool_name=resolved_tool_name, status="error").inc()
            TOOL_ERRORS_TOTAL.labels(tool_name=resolved_tool_name, error_code="TOOL_ERROR").inc()
            set_tool_circuit_state(resolved_tool_name, breaker.state.value)
            logger.error(
                "tool_execution_error",
                extra={
                    "tool": resolved_tool_name,
                    "requested_tool": tool_name,
                    "caller_role": request.caller_role.value if request.caller_role else None,
                    "caller_id": request.caller_id,
                    "caller_team": request.caller_team,
                    "project_id": request.project_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            await self._record_audit(
                actor=request.caller_id,
                project_id=request.project_id,
                tool_name=tool_name,
                status="error",
                team_id=request.caller_team,
                error=str(exc),
                duration_ms=round(duration, 2),
                trace_id=request.trace_id,
                span_id=request.span_id,
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

        await breaker.record_success()
        duration = (time.monotonic() - t0) * 1000

        MAS_TOOL_CALLS_TOTAL.labels(tool_name=resolved_tool_name, status="success").inc()
        TOOL_INVOCATIONS_TOTAL.labels(tool_name=resolved_tool_name, status="success").inc()
        set_tool_circuit_state(resolved_tool_name, breaker.state.value)

        if tool.idempotent and tool.cache_ttl_seconds > 0 and self._cache:
            await self._cache.set(
                resolved_tool_name, kwargs, result_val, ttl=tool.cache_ttl_seconds
            )

        if is_alias and isinstance(result_val, dict):
            result_val = dict(result_val)
            result_val.setdefault("_canonical_tool", resolved_tool_name)

        logger.info(
            "tool_call_success",
            extra={
                "tool": resolved_tool_name,
                "duration_ms": round(duration, 2),
                "caller_role": request.caller_role.value if request.caller_role else None,
                "caller_id": request.caller_id,
            },
        )

        await self._record_audit(
            actor=request.caller_id,
            project_id=request.project_id,
            tool_name=tool_name,
            status="success",
            team_id=request.caller_team,
            duration_ms=round(duration, 2),
            trace_id=request.trace_id,
            span_id=request.span_id,
        )

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
        if self._settings.mcp_servers:
            from .mcp_client import invoke_mcp_tool

            return await invoke_mcp_tool(
                self._settings.mcp_servers,
                kwargs,
                timeout=self._settings.transport_request_timeout_seconds,
            )
        endpoint = self._settings.mcp_transport_endpoints.get(tool_name)
        if not endpoint:
            return {
                "available": False,
                "configured": False,
                "reason": f"MCP transport endpoint not configured for {tool_name}",
            }
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
        timeout = self._settings.transport_request_timeout_seconds
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = json.dumps({"tool_name": tool_name, "kwargs": kwargs}).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"Process transport timed out after {timeout}s for tool '{tool_name}'"
            ) from exc
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
