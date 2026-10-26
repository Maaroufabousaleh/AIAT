"""Evidence-preserving fallback flow retry coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage


class _Result:
    def __init__(self, *, first=None, rows=None):
        self._first = first
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._rows


def _storage_with_results(results):
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=results)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=conn)
    tx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = tx
    storage = AgentStorage("postgresql+asyncpg://unused:unused@localhost/unused")
    storage._engine = engine
    return storage, conn.execute


@pytest.mark.anyio
async def test_retry_without_recorded_safe_node_supersedes_history() -> None:
    instance_id = uuid4()
    project_id = uuid4()
    before = {
        "id": instance_id,
        "project_id": project_id,
        "status": "FAILED",
        "active_node_ids": [],
        "retry_count": 2,
        "updated_at": "before",
    }
    after = {**before, "status": "NOT_STARTED", "retry_count": 3}
    storage, execute = _storage_with_results(
        [
            _Result(first={"project_id": project_id}),
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(rows=[(10,), (11,)]),
            _Result(rows=[]),
            _Result(rows=[{"id": 10}, {"id": 11}]),
            _Result(first=before),
            _Result(),
            _Result(),
            _Result(first=after),
        ]
    )

    result = await storage.retry_flow_instance(
        instance_id,
        expected_instance_snapshot={
            "status": "FAILED",
            "active_node_ids": [],
            "retry_count": 2,
            "updated_at": "before",
        },
    )

    assert result == after
    assert execute.await_count == 9


@pytest.mark.anyio
async def test_overlapping_no_safe_node_retry_loses_after_instance_leaves_terminal_state() -> None:
    instance_id = uuid4()
    project_id = uuid4()
    running = {
        "id": instance_id,
        "project_id": project_id,
        "status": "RUNNING",
        "active_node_ids": ["approval"],
        "retry_count": 3,
    }
    storage, execute = _storage_with_results(
        [
            _Result(first={"project_id": project_id}),
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(rows=[]),
            _Result(first=running),
        ]
    )

    result = await storage.retry_flow_instance(
        instance_id,
        expected_instance_snapshot={"status": "FAILED", "retry_count": 2},
    )

    assert result is None
    assert execute.await_count == 4
