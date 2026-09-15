"""PR A characterization of TeamRunner's distinct governance runtime plane."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from mas_core.agent_runtime import AgentBase
from mas_core.protocols import AgentRole, MessageEnvelope, MessageType
from mas_core.protocols.ws import WSMessageFrame
from mas_core.worker_contract.adapters import WorkerAdapter


def test_team_runner_instantiates_agentbase_governance_agents_outside_worker_adapter() -> None:
    from team_runner.main import RunnerSettings, TeamConfig, TeamRuntime

    config = TeamConfig.model_validate(
        {
            "team_id": "exec_ceo",
            "admin": {
                "agent_id": "admin-agent",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Admin",
            },
            "workers": [
                {
                    "agent_id": "worker-agent",
                    "role": "worker",
                    "class": "WorkerAgent",
                    "display_name": "Worker",
                }
            ],
        }
    )
    runtime = TeamRuntime(
        RunnerSettings(team_config_path=Path("exec_ceo.yaml")),
        config,
    )

    runtime._instantiate_agents()

    assert runtime.admin_agent is not None
    assert isinstance(runtime.admin_agent, AgentBase)
    assert runtime.worker_agents and all(isinstance(agent, AgentBase) for agent in runtime.worker_agents)
    assert all(not isinstance(agent, WorkerAdapter) for agent in runtime.agents_by_id.values())
    assert sorted(runtime.agents_by_id) == ["admin-agent", "worker-agent"]


@pytest.mark.asyncio
async def test_team_runner_resolves_an_explicit_snapshot_for_one_governance_dispatch() -> None:
    from unittest.mock import AsyncMock

    from team_runner.main import RunnerSettings, TeamConfig, TeamRuntime

    project_id = uuid4()
    snapshot_id = uuid4()
    config = TeamConfig.model_validate(
        {
            "team_id": "exec_ceo",
            "admin": {
                "agent_id": "admin-agent",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Admin",
            },
            "workers": [],
        }
    )
    runtime = TeamRuntime(
        RunnerSettings(team_config_path=Path("exec_ceo.yaml")),
        config,
    )

    class Storage:
        async def get_model_resolution_snapshot(
            self,
            requested_snapshot_id: UUID,
            *,
            project_id: UUID | None = None,
        ) -> dict[str, object] | None:
            assert requested_snapshot_id == snapshot_id
            assert project_id == project_id_value
            return {
                "id": snapshot_id,
                "project_id": project_id_value,
                "resolved_profile_id": "governance",
                "resolved_profile_version": "v1",
                "provider_id": "provider",
                "exact_model_id": "provider/model",
            }

    project_id_value = project_id
    runtime.storage = Storage()  # type: ignore[assignment]
    runtime._instantiate_agents()
    assert runtime.admin_agent is not None
    dispatch = AsyncMock()
    runtime.admin_agent._dispatch = dispatch  # type: ignore[method-assign]

    envelope = MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="orchestrator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="orchestrator",
        recipient_id="admin-agent",
        project_id=str(project_id),
        model_resolution_snapshot_id=snapshot_id,
    )
    await runtime._handle_frame(
        WSMessageFrame(
            entry_id="1-0",
            envelope=envelope,
            stream="stream:exec_ceo",
        )
    )

    binding = dispatch.await_args.kwargs["model_resolution_binding"]
    assert binding.resolution_snapshot_id == snapshot_id
    assert binding.profile_id == "governance"
    assert binding.exact_model_id == "provider/model"


@pytest.mark.asyncio
async def test_team_runner_accepts_unscoped_operator_direct_snapshot() -> None:
    from unittest.mock import AsyncMock

    from team_runner.main import RunnerSettings, TeamConfig, TeamRuntime

    snapshot_id = uuid4()
    config = TeamConfig.model_validate(
        {
            "team_id": "exec_ceo",
            "admin": {
                "agent_id": "admin-agent",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Admin",
            },
            "workers": [],
        }
    )
    runtime = TeamRuntime(
        RunnerSettings(team_config_path=Path("exec_ceo.yaml")),
        config,
    )

    class Storage:
        async def get_model_resolution_snapshot(
            self,
            requested_snapshot_id: UUID,
            *,
            project_id: UUID | None = None,
        ) -> dict[str, object] | None:
            assert requested_snapshot_id == snapshot_id
            assert project_id is None
            return {
                "id": snapshot_id,
                "project_id": None,
                "resolved_profile_id": "governance",
                "resolved_profile_version": "v1",
                "provider_id": "provider",
                "exact_model_id": "provider/model",
            }

    runtime.storage = Storage()  # type: ignore[assignment]
    runtime._instantiate_agents()
    assert runtime.admin_agent is not None
    dispatch = AsyncMock()
    runtime.admin_agent._dispatch = dispatch  # type: ignore[method-assign]

    envelope = MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="operator",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="orchestrator",
        recipient_id="admin-agent",
        project_id="operator-direct",
        model_resolution_snapshot_id=snapshot_id,
    )
    await runtime._handle_frame(
        WSMessageFrame(
            entry_id="operator-direct-1-0",
            envelope=envelope,
            stream="stream:exec_ceo",
        )
    )

    binding = dispatch.await_args.kwargs["model_resolution_binding"]
    assert binding.resolution_snapshot_id == snapshot_id
    assert binding.exact_model_id == "provider/model"


@pytest.mark.asyncio
async def test_team_runner_rejects_model_governed_message_without_snapshot() -> None:
    """The deployed governance boundary must not fall back to config/auto."""
    from unittest.mock import AsyncMock

    from team_runner.main import RunnerSettings, TeamConfig, TeamRuntime

    config = TeamConfig.model_validate(
        {
            "team_id": "exec_ceo",
            "admin": {
                "agent_id": "admin-agent",
                "role": "admin",
                "class": "AdminAgent",
                "display_name": "Admin",
            },
            "workers": [],
        }
    )
    runtime = TeamRuntime(
        RunnerSettings(team_config_path=Path("exec_ceo.yaml")),
        config,
    )
    runtime.storage = object()  # type: ignore[assignment]
    runtime._instantiate_agents()
    assert runtime.admin_agent is not None
    dispatch = AsyncMock()
    runtime.admin_agent._dispatch = dispatch  # type: ignore[method-assign]

    envelope = MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="untrusted-producer",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="orchestrator",
        recipient_id="admin-agent",
        project_id="operator-direct",
        payload={"action": "CHAT"},
    )

    with pytest.raises(
        RuntimeError,
        match="missing an AIAT model-resolution snapshot",
    ):
        await runtime._handle_frame(
            WSMessageFrame(
                entry_id="unbound-governance-1-0",
                envelope=envelope,
                stream="stream:exec_ceo",
            )
        )

    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkpoint_adapter_preserves_model_snapshot_reference() -> None:
    from team_runner.main import CheckpointAdapter

    snapshot_id = uuid4()

    class Store:
        async def load(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "task_message_id": str(uuid4()),
                "messages_json": [],
                "tool_results_json": [],
                "budget_state_json": None,
                "task_envelope_json": {
                    "project_id": "resume-project",
                    "model_resolution_snapshot_id": str(snapshot_id),
                },
            }

    checkpoint = CheckpointAdapter(Store(), agent_id="agent", team_id="exec_ceo")
    restored = await checkpoint.load_checkpoint("agent", "resume-project")

    assert restored is not None
    assert restored["model_resolution_snapshot_id"] == str(snapshot_id)
