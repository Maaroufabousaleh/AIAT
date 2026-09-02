"""Evidence-preserving fallback flow retry coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage


@pytest.mark.anyio
async def test_retry_without_recorded_safe_node_supersedes_history() -> None:
    storage = object.__new__(AgentStorage)
    instance_id = uuid4()
    before = {"id": instance_id, "status": "FAILED", "retry_count": 2}
    after = {**before, "status": "NOT_STARTED", "retry_count": 3}
    storage.get_flow_instance = AsyncMock(side_effect=[before, after])
    storage.update_flow_instance = AsyncMock()
    storage.supersede_flow_node_executions = AsyncMock(return_value=2)
    storage.clear_flow_node_executions = AsyncMock()

    result = await AgentStorage.retry_flow_instance(storage, instance_id)

    assert result == after
    storage.update_flow_instance.assert_awaited_once_with(
        instance_id,
        status="NOT_STARTED",
        active_node_ids=[],
        retry_count=3,
        started_at=None,
        completed_at=None,
    )
    storage.supersede_flow_node_executions.assert_awaited_once_with(instance_id)
    storage.clear_flow_node_executions.assert_not_awaited()
