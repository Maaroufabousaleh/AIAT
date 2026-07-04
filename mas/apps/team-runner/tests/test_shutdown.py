"""
Tests for team-runner shutdown handling, checkpoint saving, and NACK support.
"""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _make_agent(agent_id="test_agent"):
    """Create a mock agent with realistic attributes."""
    agent = MagicMock()
    agent.agent_id = agent_id
    agent._current_envelope = None
    agent._checkpoint = None
    agent._budget = None
    agent.start = AsyncMock()
    agent.stop = AsyncMock()
    agent.save_checkpoint = AsyncMock()
    return agent


def _make_envelope(message_id=None, project_id="proj-1"):
    """Create a mock message envelope."""
    envelope = MagicMock()
    envelope.message_id = message_id or uuid4()
    envelope.project_id = project_id
    return envelope


def _make_budget():
    """Create a mock budget with snapshot method."""
    budget = MagicMock()
    budget.snapshot.return_value = {
        "remaining_tokens": 1000,
        "cost_usd": 0.05,
        "remaining_cost_usd": 0.20,
    }
    return budget


@pytest.fixture
def temp_team_yaml(tmp_path):
    """Write a minimal team YAML to a temp file."""
    yaml_content = textwrap.dedent("""\
        team_id: test_team
        admin:
          agent_id: test_admin
          role: admin
          class: AdminAgent
          display_name: Test Admin
          budget_defaults:
            max_llm_calls: 10
            max_tool_calls: 5
            max_subtasks: 3
            max_cost_usd: 0.25
          tools: []
        workers: []
    """)
    config_file = tmp_path / "test_team.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    return config_file


@pytest.fixture
def runner_settings(temp_team_yaml):
    """Create RunnerSettings from a temp team YAML."""
    from team_runner.main import RunnerSettings

    return RunnerSettings(
        team_config_path=temp_team_yaml,
        router_url="http://localhost:9999",
        router_secret="changeme",
        orchestrator_url="http://localhost:8000",
        health_host="127.0.0.1",
        health_port=0,
    )


@pytest.mark.anyio
async def test_runtime_tool_manifest_retries_until_available(runner_settings):
    from team_runner.main import TeamConfig, TeamRuntime

    runner_settings.tool_service_url = "http://tool-service:8002"
    runner_settings.tool_manifest_startup_attempts = 3
    runner_settings.tool_manifest_retry_seconds = 0
    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
            },
        }
    )
    runtime = TeamRuntime(runner_settings, config)
    runtime.tool_client = MagicMock()
    runtime.tool_client.list_tools = AsyncMock(
        side_effect=[OSError("dns unavailable"), [{"tool_name": "time_now"}]]
    )

    await runtime._load_runtime_tool_manifest()

    assert runtime.tool_client.list_tools.await_count == 2
    assert runtime.health_payload()["tool_manifest_loaded"] is True
    assert runtime.health_payload()["runtime_tool_count"] == 1
    assert runtime.health_payload()["runtime_available_tool_count"] == 1


@pytest.mark.anyio
async def test_runtime_tool_manifest_fails_startup_after_retry_budget(runner_settings):
    from team_runner.main import TeamConfig, TeamRuntime

    runner_settings.tool_service_url = "http://tool-service:8002"
    runner_settings.tool_manifest_startup_attempts = 2
    runner_settings.tool_manifest_retry_seconds = 0
    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
            },
        }
    )
    runtime = TeamRuntime(runner_settings, config)
    runtime.tool_client = MagicMock()
    runtime.tool_client.list_tools = AsyncMock(side_effect=OSError("dns unavailable"))

    with pytest.raises(RuntimeError, match="unavailable after 2 attempt"):
        await runtime._load_runtime_tool_manifest()

    assert runtime.health_payload()["tool_manifest_loaded"] is False
    assert runtime.health_payload()["runtime_tool_count"] == 0


# ── TeamRuntime.stop() saves checkpoints correctly ───────────────────────────


@pytest.mark.anyio
async def test_stop_saves_checkpoint_with_correct_data(runner_settings):
    """stop() should save checkpoint using correct attribute names."""
    from team_runner.main import TeamConfig, TeamRuntime

    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
                "budget_defaults": {
                    "max_llm_calls": 10,
                    "max_tool_calls": 5,
                    "max_subtasks": 3,
                    "max_cost_usd": 0.25,
                },
                "tools": [],
            },
            "workers": [],
        }
    )

    runtime = TeamRuntime(runner_settings, config)
    agent = _make_agent("admin-1")
    envelope = _make_envelope()
    agent._current_envelope = envelope
    agent._checkpoint = {"messages": [{"role": "user", "content": "hello"}], "iteration": 3}
    agent._budget = _make_budget()
    runtime.agents_by_id = {"admin-1": agent}
    runtime._resume_tasks = []
    runtime.router = MagicMock()
    runtime.router.stop = AsyncMock()

    await runtime.stop()

    agent.save_checkpoint.assert_called_once()
    saved_data = agent.save_checkpoint.call_args.args[0]
    assert saved_data["messages"] == [{"role": "user", "content": "hello"}]
    assert saved_data["iteration"] == 3
    assert saved_data["budget_snapshot"] is not None
    assert saved_data["reason"] == "graceful_shutdown"


@pytest.mark.anyio
async def test_stop_skips_agents_without_envelope(runner_settings):
    """stop() should skip agents that have no _current_envelope."""
    from team_runner.main import TeamConfig, TeamRuntime

    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
                "budget_defaults": {
                    "max_llm_calls": 10,
                    "max_tool_calls": 5,
                    "max_subtasks": 3,
                    "max_cost_usd": 0.25,
                },
                "tools": [],
            },
            "workers": [],
        }
    )

    runtime = TeamRuntime(runner_settings, config)
    agent = _make_agent("admin-1")
    agent._current_envelope = None
    runtime.agents_by_id = {"admin-1": agent}
    runtime._resume_tasks = []
    runtime.router = MagicMock()
    runtime.router.stop = AsyncMock()

    await runtime.stop()

    agent.save_checkpoint.assert_not_called()


@pytest.mark.anyio
async def test_stop_handles_checkpoint_error_gracefully(runner_settings):
    """stop() should log warning but continue when checkpoint save fails."""
    from team_runner.main import TeamConfig, TeamRuntime

    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
                "budget_defaults": {
                    "max_llm_calls": 10,
                    "max_tool_calls": 5,
                    "max_subtasks": 3,
                    "max_cost_usd": 0.25,
                },
                "tools": [],
            },
            "workers": [],
        }
    )

    runtime = TeamRuntime(runner_settings, config)
    agent = _make_agent("admin-1")
    envelope = _make_envelope()
    agent._current_envelope = envelope
    agent._checkpoint = {}
    agent.save_checkpoint = AsyncMock(side_effect=RuntimeError("db error"))
    runtime.agents_by_id = {"admin-1": agent}
    runtime._resume_tasks = []
    runtime.router = MagicMock()
    runtime.router.stop = AsyncMock()

    # Should not raise
    await runtime.stop()


# ── _handle_shutdown_message sends ACK/NACK correctly ────────────────────────


@pytest.mark.anyio
async def test_shutdown_message_sends_ack_when_checkpoints_ok(runner_settings):
    """_handle_shutdown_message should POST /system/shutdown-ack when all checkpoints save."""
    from team_runner.main import TeamConfig, TeamRuntime

    from mas_core.protocols import AgentRole, MessageEnvelope, MessageType
    from mas_core.protocols.ws import WSMessageFrame

    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
                "budget_defaults": {
                    "max_llm_calls": 10,
                    "max_tool_calls": 5,
                    "max_subtasks": 3,
                    "max_cost_usd": 0.25,
                },
                "tools": [],
            },
            "workers": [],
        }
    )

    runtime = TeamRuntime(runner_settings, config)
    agent = _make_agent("admin-1")
    runtime.agents_by_id = {"admin-1": agent}
    runtime.admin_agent = agent

    shutdown_envelope = MessageEnvelope(
        msg_type=MessageType.SHUTDOWN,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="orchestrator",
        recipient_team="test_team",
        payload={"action": "SHUTDOWN", "timeout_s": 45},
    )
    frame = WSMessageFrame(
        entry_id="shutdown-1",
        envelope=shutdown_envelope,
        stream="stream:test_team",
        retry_count=0,
    )

    with patch("httpx.AsyncClient") as mock_http:
        mock_response = MagicMock(status_code=200)
        mock_client = MagicMock(post=AsyncMock(return_value=mock_response))
        mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        await runtime._handle_shutdown_message(frame)

    mock_client.post.assert_called_once()
    call_url = mock_client.post.call_args.args[0]
    assert "/system/shutdown-ack" in call_url
    call_json = mock_client.post.call_args.kwargs["json"]
    assert call_json["team_id"] == "test_team"


@pytest.mark.anyio
async def test_shutdown_message_sends_nack_when_checkpoint_fails(runner_settings):
    """_handle_shutdown_message should POST /system/shutdown-nack when checkpoints fail."""
    from team_runner.main import TeamConfig, TeamRuntime

    from mas_core.protocols import AgentRole, MessageEnvelope, MessageType
    from mas_core.protocols.ws import WSMessageFrame

    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
                "budget_defaults": {
                    "max_llm_calls": 10,
                    "max_tool_calls": 5,
                    "max_subtasks": 3,
                    "max_cost_usd": 0.25,
                },
                "tools": [],
            },
            "workers": [],
        }
    )

    runtime = TeamRuntime(runner_settings, config)
    agent = _make_agent("admin-1")
    envelope = _make_envelope()
    agent._current_envelope = envelope
    agent._checkpoint = {}
    agent.save_checkpoint = AsyncMock(side_effect=RuntimeError("db error"))
    runtime.agents_by_id = {"admin-1": agent}
    runtime.admin_agent = agent

    shutdown_envelope = MessageEnvelope(
        msg_type=MessageType.SHUTDOWN,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="orchestrator",
        recipient_team="test_team",
        payload={"action": "SHUTDOWN", "timeout_s": 45},
    )
    frame = WSMessageFrame(
        entry_id="shutdown-1",
        envelope=shutdown_envelope,
        stream="stream:test_team",
        retry_count=0,
    )

    with patch("httpx.AsyncClient") as mock_http:
        mock_response = MagicMock(status_code=200)
        mock_client = MagicMock(post=AsyncMock(return_value=mock_response))
        mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        await runtime._handle_shutdown_message(frame)

    mock_client.post.assert_called_once()
    call_url = mock_client.post.call_args.args[0]
    assert "/system/shutdown-nack" in call_url
    call_json = mock_client.post.call_args.kwargs["json"]
    assert call_json["team_id"] == "test_team"
    assert "reason" in call_json


@pytest.mark.anyio
async def test_shutdown_message_sets_stop_event(runner_settings):
    """_handle_shutdown_message should set _stop_event to terminate subscription loop."""
    from team_runner.main import TeamConfig, TeamRuntime

    from mas_core.protocols import AgentRole, MessageEnvelope, MessageType
    from mas_core.protocols.ws import WSMessageFrame

    config = TeamConfig.model_validate(
        {
            "team_id": "test_team",
            "admin": {
                "agent_id": "admin-1",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Test Admin",
                "budget_defaults": {
                    "max_llm_calls": 10,
                    "max_tool_calls": 5,
                    "max_subtasks": 3,
                    "max_cost_usd": 0.25,
                },
                "tools": [],
            },
            "workers": [],
        }
    )

    runtime = TeamRuntime(runner_settings, config)
    agent = _make_agent("admin-1")
    runtime.agents_by_id = {"admin-1": agent}
    runtime.admin_agent = agent
    assert not runtime._stop_event.is_set()

    shutdown_envelope = MessageEnvelope(
        msg_type=MessageType.SHUTDOWN,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="orchestrator",
        recipient_team="test_team",
        payload={"action": "SHUTDOWN", "timeout_s": 45},
    )
    frame = WSMessageFrame(
        entry_id="shutdown-1",
        envelope=shutdown_envelope,
        stream="stream:test_team",
        retry_count=0,
    )

    with patch("httpx.AsyncClient") as mock_http:
        mock_response = MagicMock(status_code=200)
        mock_client = MagicMock(post=AsyncMock(return_value=mock_response))
        mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        await runtime._handle_shutdown_message(frame)

    assert runtime._stop_event.is_set()
