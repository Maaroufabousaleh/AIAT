"""Durable project usage writer owned by the tool-service boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg


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
        event_id = uuid4()
        timestamp = occurred_at or datetime.now(tz=UTC)
        normalized_project_id = (
            project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        )
        try:
            await self._pool.execute(
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
        if idempotency_key:
            existing = await self._pool.fetchrow(
                "SELECT * FROM project_usage_events WHERE idempotency_key = $1",
                idempotency_key,
            )
            if existing is not None:
                return dict(existing)
        return {"id": event_id, "project_id": normalized_project_id, "occurred_at": timestamp}
