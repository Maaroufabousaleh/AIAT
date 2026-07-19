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
        **_: Any,
    ) -> dict[str, Any]:
        event_id = uuid4()
        timestamp = occurred_at or datetime.now(tz=UTC)
        normalized_project_id = (
            project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        )
        await self._pool.execute(
            """
            INSERT INTO project_usage_events (
                id, project_id, event_type, agent_id, team_id, model,
                tool_name, status, prompt_tokens, completion_tokens,
                cost_usd, duration_ms, trace_id, span_id, details, occurred_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15::jsonb, $16
            )
            """,
            event_id,
            normalized_project_id,
            event_type,
            agent_id,
            team_id,
            model,
            tool_name,
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
        return {
            "id": event_id,
            "project_id": normalized_project_id,
            "occurred_at": timestamp,
        }
