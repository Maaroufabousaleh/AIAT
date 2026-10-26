from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from mas_core.memory.models import approval_gates
from mas_core.memory.storage import AgentStorage


def _storage_with_mock_connection() -> tuple[AgentStorage, MagicMock]:
    storage = AgentStorage(dsn="postgresql+asyncpg://test:test@localhost/test")
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=result)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = context
    storage._engine = engine
    return storage, connection


@pytest.mark.asyncio
async def test_pending_project_decisions_use_bounded_indexable_join_and_projection():
    storage, connection = _storage_with_mock_connection()

    rows = await storage.list_pending_project_decisions(limit=101)

    assert rows == []
    connection.execute.assert_awaited_once()
    query = connection.execute.await_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "JOIN projects ON projects.id = approval_gates.project_id" in sql
    assert "approval_gates.status =" in sql
    assert "projects.state NOT IN" in sql
    assert "ORDER BY approval_gates.created_at DESC, approval_gates.id DESC" in sql
    assert "LIMIT" in sql
    assert "human_input" not in sql
    assert "justification" not in sql


@pytest.mark.asyncio
async def test_pending_project_decisions_use_composite_keyset_cursor():
    storage, connection = _storage_with_mock_connection()
    created_at = datetime(2026, 10, 7, tzinfo=UTC)
    gate_id = uuid4()

    await storage.list_pending_project_decisions(
        limit=11,
        cursor=(created_at, gate_id),
    )

    query = connection.execute.await_args.args[0]
    compiled = query.compile(dialect=postgresql.dialect())
    assert "(approval_gates.created_at, approval_gates.id) < (" in str(compiled)
    assert created_at in compiled.params.values()
    assert gate_id in compiled.params.values()


@pytest.mark.asyncio
async def test_pending_project_decisions_reject_unbounded_or_naive_cursor_inputs():
    storage, connection = _storage_with_mock_connection()

    with pytest.raises(ValueError, match="between 1 and 101"):
        await storage.list_pending_project_decisions(limit=102)
    with pytest.raises(ValueError, match="timezone"):
        await storage.list_pending_project_decisions(
            limit=1,
            cursor=(datetime(2026, 10, 7), uuid4()),
        )

    connection.execute.assert_not_awaited()


def test_approval_gate_metadata_declares_supporting_inbox_index():
    index = next(
        item
        for item in approval_gates.indexes
        if item.name == "ix_approval_gates_status_created_id"
    )
    assert [column.name for column in index.columns] == ["status", "created_at", "id"]
