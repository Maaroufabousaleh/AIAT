"""Durable project usage writer owned by the tool-service boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from mas_core.observability import build_native_trace_span, current_trace_id


class ProjectUsageWriter:
    """Small asyncpg writer that keeps tool accounting independent of ORM releases."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> ProjectUsageWriter:
        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        pool = await asyncpg.create_pool(
            normalized,
            min_size=1,
            max_size=3,
            statement_cache_size=0,
        )
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def healthcheck(self) -> bool:
        """Return whether the backing Postgres pool can execute queries."""
        return bool(await self._pool.fetchval("SELECT 1"))

    async def _record_native_tool_span(
        self,
        *,
        trace_id: str | None,
        parent_span_id: str | None,
        tool_name: str | None,
        status: str,
        agent_id: str | None,
        team_id: str | None,
        project_id: UUID,
        occurred_at: datetime,
        duration_ms: float | None,
    ) -> None:
        """Persist one payload-free native tool span after usage accounting.

        The tool service intentionally keeps its accounting writer independent
        from the SQLAlchemy storage owner.  Native spans therefore use the
        same normalized contract through a small asyncpg insert.  A trace span
        is telemetry only: callers may provide a parent span, but the writer
        always creates its own bounded span ID so retries cannot overwrite a
        transport/model span with the same caller ID.
        """

        if not trace_id:
            return
        try:
            bounded_duration = max(0.0, min(float(duration_ms or 0.0), 86_400_000.0))
        except (TypeError, ValueError):
            bounded_duration = 0.0
        native_status = "success" if status == "success" else (
            "failure"
            if status in {"error", "forbidden", "validation_error", "rate_limited", "circuit_open"}
            else "unknown"
        )
        values = build_native_trace_span(
            trace_id=trace_id,
            span_id=None,
            parent_span_id=parent_span_id,
            source_kind="tool",
            operation=tool_name or "tool",
            service="tool_service",
            status=native_status,
            started_at=occurred_at - timedelta(milliseconds=bounded_duration),
            ended_at=occurred_at,
            duration_ms=bounded_duration,
            attributes={
                "event_type": "tool",
                "agent_id": agent_id,
                "team_id": team_id,
                "project_id": str(project_id),
            },
        )
        attributes_json = json.dumps(values.pop("attributes", {}), separators=(",", ":"))
        await self._pool.execute(
            """
            INSERT INTO native_trace_spans (
                id, trace_id, span_id, parent_span_id, source_kind, operation,
                service, status, started_at, ended_at, duration_ms, sampled,
                retention_until, attributes_json
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb
            )
            ON CONFLICT (trace_id, span_id) DO NOTHING
            """,
            values["id"],
            values["trace_id"],
            values["span_id"],
            values["parent_span_id"],
            values["source_kind"],
            values["operation"],
            values["service"],
            values["status"],
            values["started_at"],
            values["ended_at"],
            values["duration_ms"],
            values["sampled"],
            values["retention_until"],
            attributes_json,
        )

    async def record_project_usage(
        self,
        *,
        project_id: UUID | str,
        event_type: str,
        agent_id: str | None = None,
        team_id: str | None = None,
        model: str | None = None,
        tool_name: str | None = None,
        status: str = "success",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: float | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        company_id: UUID | None = None,
        run_id: UUID | None = None,
        worker_id: UUID | None = None,
        provider_id: str | None = None,
        billing_code: str | None = None,
        pricing_snapshot: dict[str, Any] | None = None,
        resource_json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        trace_id = trace_id or current_trace_id()
        event_id = uuid4()
        timestamp = occurred_at or datetime.now(tz=UTC)
        normalized_project_id = (
            project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        )
        try:
            insert_result = await self._pool.execute(
                """
                INSERT INTO project_usage_events (
                    id, project_id, company_id, run_id, worker_id, event_type,
                    agent_id, team_id, model, provider_id, tool_name,
                    billing_code, pricing_snapshot, resource_json,
                    idempotency_key, status, prompt_tokens, completion_tokens,
                    cost_usd, duration_ms, trace_id, span_id, details, occurred_at
                ) VALUES (
                    $1, $2,
                    COALESCE($3, (SELECT company_id FROM projects WHERE id = $2)),
                    $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13::jsonb, $14::jsonb, $15, $16, $17, $18, $19,
                    $20, $21, $22, $23::jsonb, $24
                )
                ON CONFLICT DO NOTHING
                """,
                event_id,
                normalized_project_id,
                company_id,
                run_id,
                worker_id,
                event_type,
                agent_id,
                team_id,
                model,
                provider_id,
                tool_name,
                billing_code,
                json.dumps(pricing_snapshot) if pricing_snapshot is not None else None,
                json.dumps(resource_json) if resource_json is not None else None,
                idempotency_key,
                status,
                max(0, int(prompt_tokens or 0)),
                max(0, int(completion_tokens or 0)),
                max(0.0, float(cost_usd or 0.0)),
                duration_ms,
                trace_id,
                span_id,
                json.dumps(details) if details is not None else None,
                timestamp,
            )
        except asyncpg.ForeignKeyViolationError as exc:
            # Usage is best-effort telemetry. A project may be deleted between
            # the tool call and this asynchronous write; do not turn that
            # expected lifecycle race into a service-level error. Other
            # database failures still propagate to the registry's error path.
            if "project_usage_events_project_id_fkey" not in str(exc):
                raise
            return {
                "id": event_id,
                "project_id": normalized_project_id,
                "occurred_at": timestamp,
                "persisted": False,
                "drop_reason": "project_deleted_before_persistence",
            }
        inserted = str(insert_result).endswith(" 1")
        existing: dict[str, Any] | None = None
        if idempotency_key:
            existing = await self._pool.fetchrow(
                "SELECT * FROM project_usage_events WHERE idempotency_key = $1",
                idempotency_key,
            )
            if existing is not None:
                if not inserted:
                    return dict(existing)
                # Keep replayed accounting idempotent even when the database
                # reports a successful insert for a non-idempotency conflict.
                event_id = existing.get("id", event_id)
                timestamp = existing.get("occurred_at", timestamp)
        try:
            await self._record_native_tool_span(
                trace_id=trace_id,
                parent_span_id=span_id,
                tool_name=tool_name,
                status=status,
                agent_id=agent_id,
                team_id=team_id,
                project_id=normalized_project_id,
                occurred_at=timestamp,
                duration_ms=duration_ms,
            )
        except Exception:
            # Native trace evidence is deliberately best effort at this
            # boundary.  A migration lag or telemetry outage must not turn a
            # successful tool result into a failed tool operation.
            return {
                "id": event_id,
                "project_id": normalized_project_id,
                "occurred_at": timestamp,
                "native_span_persisted": False,
            }
        if idempotency_key and existing is not None:
            return dict(existing)
        return {
            "id": event_id,
            "project_id": normalized_project_id,
            "occurred_at": timestamp,
            "native_span_persisted": bool(trace_id),
        }
