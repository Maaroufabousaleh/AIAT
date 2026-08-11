"""Tool-service usage accounting emits bounded native tool spans."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from tool_service.usage import ProjectUsageWriter

PROJECT_ID = UUID("00000000-0000-4000-a000-000000000201")


class FakePool:
    def __init__(self, *, insert_result: str = "INSERT 0 1", existing=None) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.insert_result = insert_result
        self.existing = existing

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append((query, args))
        return self.insert_result

    async def fetchrow(self, _query: str, _key: str):
        return self.existing


@pytest.mark.anyio
async def test_tool_usage_writes_payload_free_native_span() -> None:
    pool = FakePool()
    writer = ProjectUsageWriter(pool)  # type: ignore[arg-type]
    occurred_at = datetime(2026, 8, 10, 19, 0, tzinfo=UTC)

    result = await writer.record_project_usage(
        project_id=PROJECT_ID,
        event_type="tool",
        agent_id="worker-alpha",
        team_id="dept_qa",
        tool_name="time.now",
        status="success",
        duration_ms=12.5,
        trace_id="tool-live-trace-001",
        span_id="caller-span-001",
        occurred_at=occurred_at,
    )

    assert result["native_span_persisted"] is True
    assert len(pool.calls) == 2
    native_query, native_args = pool.calls[1]
    assert "INSERT INTO native_trace_spans" in native_query
    assert native_args[1] == "tool-live-trace-001"
    assert native_args[3] == "caller-span-001"
    assert native_args[4] == "tool"
    assert native_args[5] == "time.now"
    assert native_args[6] == "tool_service"
    assert native_args[7] == "success"
    assert native_args[8] == datetime(2026, 8, 10, 18, 59, 59, 987500, tzinfo=UTC)
    attributes = json.loads(str(native_args[-1]))
    assert attributes == {
        "event_type": "tool",
        "agent_id": "worker-alpha",
        "team_id": "dept_qa",
        "project_id": str(PROJECT_ID),
    }
    assert "payload" not in attributes
    assert "secret" not in attributes


@pytest.mark.anyio
async def test_invalid_trace_does_not_break_usage_accounting() -> None:
    pool = FakePool()
    writer = ProjectUsageWriter(pool)  # type: ignore[arg-type]

    result = await writer.record_project_usage(
        project_id=PROJECT_ID,
        event_type="tool",
        tool_name="time.now",
        trace_id="invalid trace id",
    )

    assert result["native_span_persisted"] is False
    assert len(pool.calls) == 1


@pytest.mark.anyio
async def test_idempotent_replay_does_not_emit_duplicate_native_span() -> None:
    existing = {
        "id": UUID("00000000-0000-4000-a000-000000000202"),
        "project_id": PROJECT_ID,
        "occurred_at": datetime(2026, 8, 10, 19, 1, tzinfo=UTC),
    }
    pool = FakePool(insert_result="INSERT 0 0", existing=existing)
    writer = ProjectUsageWriter(pool)  # type: ignore[arg-type]

    result = await writer.record_project_usage(
        project_id=PROJECT_ID,
        event_type="tool",
        tool_name="time.now",
        trace_id="tool-live-trace-002",
        idempotency_key="run-002",
    )

    assert result == existing
    assert len(pool.calls) == 1
