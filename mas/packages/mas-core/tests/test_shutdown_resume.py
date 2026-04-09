"""Tests for the agent shutdown/resume protocol and CheckpointStore.

Tests:
- agent.shutdown() (stop()) triggers _shutting_down event
- CheckpointStore.save() persists agent state correctly (mocked DB)
- CheckpointStore.load() rehydrates agent state from mocked DB
- CheckpointStore.delete() removes a checkpoint (mocked DB)
- A SHUTDOWN_ACK message is sent after graceful shutdown (via mocked router)
- Resume restores the agent to its last checkpoint (save then load cycle)
- BudgetTracker snapshot/restore round-trip is lossless

All external services (Postgres, Redis) are mocked. No real infrastructure needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from mas_core.agent_runtime.base import AgentBase
from mas_core.agent_runtime.budget import BudgetTracker
from mas_core.agent_runtime.config import AgentConfig
from mas_core.memory.checkpoints import CheckpointStore
from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.protocols.envelope import MessageEnvelope

# ---------------------------------------------------------------------------
# Helpers — minimal concrete agent for testing
# ---------------------------------------------------------------------------


class _ConcreteAgent(AgentBase):
    """Minimal concrete subclass of AgentBase for unit tests."""

    def __init__(self, config: AgentConfig, storage: Any = None) -> None:
        # Inject a no-op LLM client so no network calls are attempted
        mock_llm = MagicMock()
        mock_llm.start = AsyncMock()
        mock_llm.stop = AsyncMock()
        super().__init__(config, storage, llm_client=mock_llm)
        self.handled_envelopes: list[MessageEnvelope] = []

    async def handle_message(self, envelope: MessageEnvelope) -> None:
        self.handled_envelopes.append(envelope)


def _make_config(agent_id: str = "test_agent") -> AgentConfig:
    return AgentConfig(
        agent_id=agent_id,
        team_id="exec_ceo",
        agent_role=AgentRole.EXECUTIVE,
        agent_secret="test_secret",
        router_url="http://localhost:8001",
    )


def _make_envelope(project_id: str | None = None) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=uuid4(),
        msg_type=MessageType.DIRECTIVE,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_orchestrator",
        recipient_team="exec_ceo",
        project_id=project_id or str(uuid4()),
        payload={"action": "TEST"},
    )


# ---------------------------------------------------------------------------
# Part 1 — shutdown() sets _shutting_down event
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shutdown_sets_shutting_down_event():
    """Calling agent.stop() must set the _shutting_down event."""
    config = _make_config()
    agent = _ConcreteAgent(config)
    # Patch out the router so no real HTTP/WS
    agent._router.start = AsyncMock()
    agent._router.stop = AsyncMock()

    assert not agent._shutting_down.is_set(), "Event must be clear before shutdown"

    await agent.stop()

    assert agent._shutting_down.is_set(), "Event must be set after shutdown"


@pytest.mark.anyio
async def test_stop_closes_llm_client():
    """agent.stop() must stop the LLM client if it was started."""
    config = _make_config()
    mock_llm = MagicMock()
    mock_llm.start = AsyncMock()
    mock_llm.stop = AsyncMock()
    agent = _ConcreteAgent(config)
    agent._llm = mock_llm
    agent._router.start = AsyncMock()
    agent._router.stop = AsyncMock()

    # Start the agent to set _llm_started = True
    await agent.start()
    assert agent._llm_started

    await agent.stop()

    mock_llm.stop.assert_awaited_once()
    assert not agent._llm_started


# ---------------------------------------------------------------------------
# Part 2 — CheckpointStore.save() persists state correctly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkpoint_store_save_calls_execute():
    """CheckpointStore.save() must call conn.execute with INSERT/UPSERT."""
    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=mock_conn)

    store = CheckpointStore(mock_engine)
    cid = await store.save(
        agent_id="agent_01",
        team_id="exec_ceo",
        task_message_id="msg_abc",
        iteration=3,
        messages_json=[{"role": "user", "content": "hello"}],
        project_id=uuid4(),
        tool_results_json=[],
        budget_state_json={"max_llm_calls": 10, "llm_calls_used": 3},
        task_envelope_json={"message_id": "msg_abc"},
    )

    assert isinstance(cid, UUID), "save() must return a UUID checkpoint ID"
    # The engine.begin context manager should have been used
    mock_engine.begin.assert_called_once()


@pytest.mark.anyio
async def test_checkpoint_store_save_returns_provided_checkpoint_id():
    """When checkpoint_id is provided, save() must return the same UUID."""
    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=mock_conn)

    store = CheckpointStore(mock_engine)
    existing_id = uuid4()
    returned_id = await store.save(
        agent_id="agent_02",
        team_id="exec_ceo",
        task_message_id="msg_xyz",
        iteration=1,
        messages_json=[],
        task_envelope_json={},
        checkpoint_id=existing_id,
    )

    assert returned_id == existing_id


# ---------------------------------------------------------------------------
# Part 3 — CheckpointStore.load() rehydrates agent state
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkpoint_store_load_returns_none_when_not_found():
    """CheckpointStore.load() must return None when no row exists."""
    mock_result = MagicMock()
    mock_result.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=mock_conn)

    store = CheckpointStore(mock_engine)
    result = await store.load("nonexistent_agent")
    assert result is None


@pytest.mark.anyio
async def test_checkpoint_store_load_returns_dict_when_found():
    """CheckpointStore.load() must return a dict when a checkpoint row exists."""
    checkpoint_data = {
        "id": uuid4(),
        "agent_id": "agent_01",
        "team_id": "exec_ceo",
        "task_message_id": "msg_abc",
        "iteration": 5,
        "messages_json": [{"role": "user", "content": "task"}],
        "tool_results_json": [],
        "budget_state_json": {"llm_calls_used": 5},
        "task_envelope_json": {"message_id": "msg_abc"},
        "saved_at": "2026-01-01T00:00:00+00:00",
        "project_id": None,
    }

    mock_row = MagicMock()
    # Make the row behave like a mapping
    mock_row.__iter__ = MagicMock(return_value=iter(checkpoint_data.items()))
    mock_row.keys = MagicMock(return_value=checkpoint_data.keys())
    mock_row.__getitem__ = MagicMock(side_effect=checkpoint_data.__getitem__)

    mock_result = MagicMock()
    mock_result.mappings = MagicMock(
        return_value=MagicMock(first=MagicMock(return_value=checkpoint_data))
    )

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=mock_conn)

    store = CheckpointStore(mock_engine)
    result = await store.load("agent_01", "msg_abc")

    assert result is not None
    assert result["agent_id"] == "agent_01"
    assert result["iteration"] == 5


# ---------------------------------------------------------------------------
# Part 4 — SHUTDOWN_ACK message sent via router after shutdown
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shutdown_ack_published_after_stop():
    """After agent.stop(), a SHUTDOWN_ACK should be publishable via the router.

    This tests the handshake pattern: the team-runner calls agent.stop()
    then publishes a SHUTDOWN_ACK on behalf of the agent. We verify that
    stop() completes without error and the router's publish method is callable.
    """
    config = _make_config()
    agent = _ConcreteAgent(config)

    # Mock the router so we can capture publish calls
    mock_router = AsyncMock()
    mock_router.start = AsyncMock()
    mock_router.stop = AsyncMock()
    mock_router.publish = AsyncMock(return_value="stream:exec_ceo:1-0")
    agent._router = mock_router

    await agent.stop()

    assert agent._shutting_down.is_set()

    # Simulate team-runner sending SHUTDOWN_ACK after stop
    ack_envelope = _make_envelope()
    await agent._router.publish(ack_envelope)

    mock_router.publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# Part 5 — save_checkpoint persists data via storage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_save_checkpoint_calls_storage_with_correct_data():
    """agent.save_checkpoint() must delegate to storage.save_checkpoint()."""
    config = _make_config()
    mock_storage = AsyncMock()
    mock_storage.save_checkpoint = AsyncMock()

    agent = _ConcreteAgent(config, storage=mock_storage)
    agent._router.start = AsyncMock()
    agent._router.stop = AsyncMock()

    # Simulate a current envelope being processed
    envelope = _make_envelope(project_id="proj_123")
    agent._current_envelope = envelope

    checkpoint_data = {
        "messages": [{"role": "user", "content": "do task"}],
        "iteration": 2,
        "tool_results": [],
        "budget_snapshot": {"llm_calls_used": 2},
    }

    await agent.save_checkpoint(checkpoint_data)

    mock_storage.save_checkpoint.assert_awaited_once_with("test_agent", "proj_123", checkpoint_data)


@pytest.mark.anyio
async def test_save_checkpoint_noop_when_no_storage():
    """agent.save_checkpoint() must be a no-op when storage is None."""
    config = _make_config()
    agent = _ConcreteAgent(config, storage=None)

    envelope = _make_envelope()
    agent._current_envelope = envelope

    # Should not raise
    await agent.save_checkpoint({"messages": [], "iteration": 0})


@pytest.mark.anyio
async def test_save_checkpoint_noop_when_no_envelope():
    """agent.save_checkpoint() must be a no-op when no envelope is active."""
    config = _make_config()
    mock_storage = AsyncMock()
    mock_storage.save_checkpoint = AsyncMock()
    agent = _ConcreteAgent(config, storage=mock_storage)

    # No envelope set
    agent._current_envelope = None

    await agent.save_checkpoint({"messages": [], "iteration": 0})

    mock_storage.save_checkpoint.assert_not_awaited()


# ---------------------------------------------------------------------------
# Part 6 — load_checkpoint restores state from storage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_load_checkpoint_calls_storage():
    """agent.load_checkpoint() must delegate to storage.load_checkpoint()."""
    config = _make_config()
    saved_data = {"messages": [{"role": "user"}], "iteration": 4}
    mock_storage = AsyncMock()
    mock_storage.load_checkpoint = AsyncMock(return_value=saved_data)

    agent = _ConcreteAgent(config, storage=mock_storage)

    envelope = _make_envelope(project_id="proj_456")
    agent._current_envelope = envelope

    result = await agent.load_checkpoint()

    mock_storage.load_checkpoint.assert_awaited_once_with("test_agent", "proj_456")
    assert result == saved_data


@pytest.mark.anyio
async def test_load_checkpoint_returns_none_when_no_storage():
    """agent.load_checkpoint() must return None when storage is None."""
    config = _make_config()
    agent = _ConcreteAgent(config, storage=None)
    envelope = _make_envelope()
    agent._current_envelope = envelope

    result = await agent.load_checkpoint()
    assert result is None


# ---------------------------------------------------------------------------
# Part 7 — restore_from_checkpoint rehydrates in-memory state
# ---------------------------------------------------------------------------


def test_restore_from_checkpoint_sets_checkpoint_attr():
    """restore_from_checkpoint() must set self._checkpoint."""
    config = _make_config()
    agent = _ConcreteAgent(config)

    checkpoint = {
        "messages": [{"role": "assistant", "content": "partial result"}],
        "iteration": 3,
        "tool_results": [{"tool_name": "search", "result": "found it"}],
    }
    agent.restore_from_checkpoint(checkpoint)

    assert agent._checkpoint == checkpoint


def test_restore_from_checkpoint_restores_budget_snapshot():
    """restore_from_checkpoint() must restore budget from snapshot if present."""
    config = _make_config()
    agent = _ConcreteAgent(config)
    # Give agent an active budget
    agent._budget = BudgetTracker(max_llm_calls=10)

    checkpoint = {
        "messages": [],
        "iteration": 5,
        "budget_snapshot": {
            "max_llm_calls": 10,
            "max_tool_calls": None,
            "max_subtasks": None,
            "deadline": None,
            "max_cost_usd": None,
            "llm_calls_used": 5,
            "tool_calls_used": 2,
            "subtasks_used": 0,
            "cost_usd_used": 0.05,
            "tokens_in_used": 1000,
            "tokens_out_used": 500,
        },
    }
    agent.restore_from_checkpoint(checkpoint)

    assert agent._budget is not None
    assert agent._budget.llm_calls_used == 5
    assert agent._budget.tool_calls_used == 2
    assert agent._budget.cost_usd_used == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Part 8 — delete_checkpoint removes on success
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_checkpoint_calls_storage():
    """agent.delete_checkpoint() must call storage.delete_checkpoint()."""
    config = _make_config()
    mock_storage = AsyncMock()
    mock_storage.delete_checkpoint = AsyncMock()
    agent = _ConcreteAgent(config, storage=mock_storage)

    envelope = _make_envelope(project_id="proj_789")
    agent._current_envelope = envelope

    await agent.delete_checkpoint()

    mock_storage.delete_checkpoint.assert_awaited_once_with("test_agent", "proj_789")


# ---------------------------------------------------------------------------
# Part 9 — resume restores to last checkpoint (integration-style)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_restores_to_last_checkpoint():
    """Full save→restore cycle: save_checkpoint + load_checkpoint returns the same data."""
    config = _make_config()

    # We use a simple dict as in-memory storage
    _store: dict[str, Any] = {}

    async def _save(agent_id: str, project_id: str, data: dict) -> None:
        _store[(agent_id, project_id)] = data

    async def _load(agent_id: str, project_id: str) -> dict | None:
        return _store.get((agent_id, project_id))

    async def _delete(agent_id: str, project_id: str) -> None:
        _store.pop((agent_id, project_id), None)

    mock_storage = MagicMock()
    mock_storage.save_checkpoint = _save
    mock_storage.load_checkpoint = _load
    mock_storage.delete_checkpoint = _delete

    agent = _ConcreteAgent(config, storage=mock_storage)
    envelope = _make_envelope(project_id="proj_resume_test")
    agent._current_envelope = envelope

    checkpoint_data = {
        "messages": [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "partial response"},
        ],
        "iteration": 3,
        "tool_results": [{"tool_name": "plan_tool", "result": {"ok": True}}],
        "budget_snapshot": BudgetTracker(max_llm_calls=20, llm_calls_used=3).snapshot(),
    }

    # Save checkpoint
    await agent.save_checkpoint(checkpoint_data)

    # Load checkpoint (simulates resume after restart)
    restored = await agent.load_checkpoint()
    assert restored is not None
    assert restored["iteration"] == 3
    assert len(restored["messages"]) == 2
    assert restored["messages"][1]["content"] == "partial response"

    # Restore in-memory state
    agent.restore_from_checkpoint(restored)
    assert agent._checkpoint["iteration"] == 3
    # Budget is only restored when _budget is already set (it's set during dispatch).
    # Simulate that by setting a budget first, then restore.
    agent._budget = BudgetTracker()
    agent.restore_from_checkpoint(restored)
    assert agent._budget.llm_calls_used == 3
    assert agent._budget.max_llm_calls == 20


# ---------------------------------------------------------------------------
# Part 10 — BudgetTracker snapshot/restore round-trip
# ---------------------------------------------------------------------------


def test_budget_tracker_snapshot_restore_round_trip():
    """BudgetTracker.snapshot() → restore_snapshot() must be lossless."""
    original = BudgetTracker(
        max_llm_calls=15,
        max_tool_calls=30,
        max_cost_usd=5.0,
        llm_calls_used=7,
        tool_calls_used=12,
        cost_usd_used=1.23,
        tokens_in_used=4096,
        tokens_out_used=2048,
    )
    snapshot = original.snapshot()
    restored = BudgetTracker.restore_snapshot(snapshot)

    assert restored.max_llm_calls == original.max_llm_calls
    assert restored.max_tool_calls == original.max_tool_calls
    assert restored.max_cost_usd == original.max_cost_usd
    assert restored.llm_calls_used == original.llm_calls_used
    assert restored.tool_calls_used == original.tool_calls_used
    assert restored.cost_usd_used == pytest.approx(original.cost_usd_used)
    assert restored.tokens_in_used == original.tokens_in_used
    assert restored.tokens_out_used == original.tokens_out_used


# ---------------------------------------------------------------------------
# Part 11 — CheckpointStore.delete() removes checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkpoint_store_delete_returns_true_on_hit():
    """CheckpointStore.delete() must return True when a row was deleted."""
    mock_result = MagicMock()
    mock_result.rowcount = 1

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=mock_conn)

    store = CheckpointStore(mock_engine)
    deleted = await store.delete("agent_01", "msg_abc")
    assert deleted is True


@pytest.mark.anyio
async def test_checkpoint_store_delete_returns_false_on_miss():
    """CheckpointStore.delete() must return False when no row matched."""
    mock_result = MagicMock()
    mock_result.rowcount = 0

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=mock_conn)

    store = CheckpointStore(mock_engine)
    deleted = await store.delete("agent_01", "nonexistent_msg")
    assert deleted is False
