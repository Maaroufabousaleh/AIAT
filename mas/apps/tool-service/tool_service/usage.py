"""Durable project usage writer owned by the tool-service boundary."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from mas_core.observability import build_native_trace_span, current_trace_id


class WorkerToolEffectRepository:
    """Durable effect journal for tool calls that bypass WorkerRunController.

    OpenCode's signed MCP bridge executes inside Tool Service, so it must
    reserve and settle the same worker_tool_effects rows used by the worker
    controller before returning a result to the runtime.  This repository is
    intentionally separate from best-effort usage and trace accounting.
    """

    _SELECT_EFFECT = """
        SELECT id, run_id, request_id, idempotency_key, tool_name,
               request_sha256, state, response_json
        FROM worker_tool_effects
        WHERE run_id = $1 AND idempotency_key = $2
        FOR UPDATE
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @staticmethod
    def _claim_matches(
        row: Any,
        *,
        claim_owner: str | None,
        attempt_count: int,
        now: datetime,
    ) -> bool:
        if row is None:
            return False
        current = dict(row)
        if current.get("cancel_requested_at") is not None:
            return False
        current_owner = current.get("claim_owner")
        current_attempt = int(current.get("attempt_count") or 0)
        if str(current.get("state") or "") != "RUNNING":
            return False
        if current_owner is None and current_attempt == 0:
            return claim_owner is None and attempt_count == 0
        lease_expires_at = current.get("lease_expires_at")
        return (
            claim_owner is not None
            and claim_owner == current_owner
            and attempt_count == current_attempt
            and attempt_count > 0
            and lease_expires_at is not None
            and lease_expires_at > now
        )

    async def _lock_and_check_worker_run_claim(
        self,
        connection: asyncpg.Connection,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
    ) -> bool:
        row = await connection.fetchrow(
            """
                SELECT id, state, claim_owner, attempt_count, lease_expires_at,
                       cancel_requested_at
                FROM worker_runs
                WHERE id = $1
                FOR UPDATE
                """,
            run_id,
        )
        return self._claim_matches(
            row,
            claim_owner=claim_owner,
            attempt_count=attempt_count,
            now=datetime.now(tz=UTC),
        )

    async def validate_worker_run_claim(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
    ) -> bool:
        """Revalidate a run fence immediately before Tool Service dispatch."""
        async with self._pool.acquire() as connection, connection.transaction():
            return await self._lock_and_check_worker_run_claim(
                connection,
                run_id=run_id,
                claim_owner=claim_owner,
                attempt_count=attempt_count,
            )

    async def begin_worker_tool_effect(
        self,
        *,
        run_id: UUID,
        request_id: UUID,
        idempotency_key: str,
        tool_name: str,
        request_sha256: str,
        claim_owner: str | None = None,
        attempt_count: int = 0,
    ) -> dict[str, Any]:
        """Reserve an effect before dispatch and classify any existing row."""

        if not isinstance(run_id, UUID) or not isinstance(request_id, UUID):
            raise ValueError("worker tool effect IDs must be UUIDs")
        if not idempotency_key or len(idempotency_key) > 256:
            raise ValueError("worker tool effect idempotency key is invalid")
        if not tool_name or len(tool_name) > 256:
            raise ValueError("worker tool effect tool name is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
            raise ValueError("worker tool effect request hash is invalid")

        effect_id = uuid4()
        async with self._pool.acquire() as connection, connection.transaction():
            current_claim = await self._lock_and_check_worker_run_claim(
                connection,
                run_id=run_id,
                claim_owner=claim_owner,
                attempt_count=attempt_count,
            )
            if not current_claim:
                return {"disposition": "CLAIM_LOST"}
            await connection.execute(
                """
                    INSERT INTO worker_tool_effects (
                        id, run_id, request_id, idempotency_key, tool_name,
                        request_sha256, state
                    ) VALUES ($1, $2, $3, $4, $5, $6, 'IN_FLIGHT')
                    ON CONFLICT (run_id, idempotency_key) DO NOTHING
                    """,
                effect_id,
                run_id,
                request_id,
                idempotency_key,
                tool_name,
                request_sha256,
            )
            row = await connection.fetchrow(
                self._SELECT_EFFECT,
                run_id,
                idempotency_key,
            )

        if row is None:
            raise RuntimeError("worker tool effect reservation was not persisted")
        result = dict(row)
        if result.get("request_sha256") != request_sha256:
            result["disposition"] = "CONFLICT"
        elif result.get("state") == "COMPLETED" and result.get("response_json") is not None:
            result["disposition"] = "REPLAY"
        elif result.get("state") == "IN_FLIGHT" and result.get("id") == effect_id:
            result["disposition"] = "NEW"
        else:
            # Both existing IN_FLIGHT and AMBIGUOUS rows can represent a
            # consequential action whose response was lost. Never re-dispatch.
            result["disposition"] = "AMBIGUOUS"
        return result

    async def complete_worker_tool_effect(
        self,
        effect_id: UUID,
        *,
        state: str,
        response_json: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Settle only an IN_FLIGHT row; never overwrite a prior disposition."""

        normalized_state = str(state).strip().upper()
        if normalized_state not in {"COMPLETED", "AMBIGUOUS"}:
            raise ValueError("worker tool effect completion state is invalid")
        if not isinstance(effect_id, UUID) or not isinstance(response_json, dict):
            raise ValueError("worker tool effect completion payload is invalid")
        now = datetime.now(tz=UTC)
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                    UPDATE worker_tool_effects
                    SET state = $2,
                        response_json = $3::jsonb,
                        updated_at = $4,
                        completed_at = CASE WHEN $2 = 'COMPLETED' THEN $4 ELSE NULL END
                    WHERE id = $1 AND state = 'IN_FLIGHT'
                    RETURNING id, run_id, request_id, idempotency_key, tool_name,
                              request_sha256, state, response_json
                    """,
                effect_id,
                normalized_state,
                json.dumps(response_json, separators=(",", ":")),
                now,
            )
        return dict(row) if row is not None else None


class ProjectUsageWriter:
    """Small asyncpg writer that keeps tool accounting independent of ORM releases."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.worker_tool_effects = WorkerToolEffectRepository(pool)

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
