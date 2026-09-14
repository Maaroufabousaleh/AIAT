"""Governance-plane model decisions are created at orchestrator boundaries."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest


def _profile_row() -> dict[str, object]:
    return {
        "logical_profile_id": "governance-profile",
        "purpose": "Governance-agent test profile",
        "approved_provider_ids": ["provider"],
        "required_capabilities": ["tool_calling"],
        "fallback_profile_ids": [],
        "status": "approved",
        "owner": "aiat",
        "versions": [
            {
                "version": "1",
                "provider_id": "provider",
                "exact_model_id": "provider/governance-model",
                "capabilities": ["tool_calling", "streaming"],
                "constraints_json": {
                    "tool_calling": True,
                    "streaming": True,
                    "context_window": 32_000,
                },
                "status": "approved",
            }
        ],
    }


class _GovernanceStorage:
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        self.snapshots: list[dict[str, object]] = []

    async def list_model_profiles(self) -> list[dict[str, object]]:
        return [_profile_row()]

    async def get_config(self, key: str) -> object | None:
        assert key == "model_policy.organization"
        return {"constraints": {"allowed_profile_ids": ["governance-profile"]}}

    async def get_project(self, project_id: UUID) -> dict[str, object]:
        assert project_id == self.project_id
        return {"id": project_id, "config": {}}

    async def create_model_resolution_snapshot(
        self,
        *,
        snapshot: dict[str, object],
        project_id: UUID | None,
    ) -> dict[str, object]:
        persisted = {"id": snapshot["snapshot_id"], "project_id": project_id, **snapshot}
        self.snapshots.append(persisted)
        return persisted


@pytest.mark.anyio
async def test_governance_message_gets_persisted_project_scoped_snapshot(monkeypatch) -> None:
    from orchestrator_api import main

    project_id = uuid4()
    storage = _GovernanceStorage(project_id)
    monkeypatch.setattr(main.app.state, "storage", storage, raising=False)
    envelope: dict[str, object] = {
        "msg_type": "DIRECTIVE",
        "recipient_team": "exec_coo",
        "project_id": str(project_id),
        "payload": {"action": "START_EXECUTION"},
    }

    snapshot_id = await main._attach_governance_model_snapshot(envelope)

    assert snapshot_id is not None
    assert envelope["model_resolution_snapshot_id"] == str(snapshot_id)
    assert len(storage.snapshots) == 1
    persisted = storage.snapshots[0]
    assert persisted["project_id"] == project_id
    assert persisted["provider_id"] == "provider"
    assert persisted["exact_model_id"] == "provider/governance-model"


@pytest.mark.anyio
async def test_operator_direct_governance_message_uses_unscoped_snapshot(monkeypatch) -> None:
    from orchestrator_api import main

    storage = _GovernanceStorage(uuid4())
    monkeypatch.setattr(main.app.state, "storage", storage, raising=False)
    envelope: dict[str, object] = {
        "msg_type": "TASK",
        "recipient_team": "exec_ceo",
        "project_id": "operator-direct",
        "payload": {"action": "HUMAN_DIRECTIVE"},
    }

    await main._attach_governance_model_snapshot(envelope)

    assert len(storage.snapshots) == 1
    assert storage.snapshots[0]["project_id"] is None
    assert str(envelope["model_resolution_snapshot_id"]) == str(storage.snapshots[0]["id"])


@pytest.mark.anyio
async def test_existing_governance_snapshot_is_not_re_resolved(monkeypatch) -> None:
    from orchestrator_api import main

    snapshot_id = uuid4()
    monkeypatch.setattr(main.app.state, "storage", None, raising=False)
    envelope: dict[str, object] = {
        "msg_type": "ADMIN_TASK",
        "recipient_team": "office_cto",
        "project_id": str(uuid4()),
        "model_resolution_snapshot_id": str(snapshot_id),
    }

    result = await main._attach_governance_model_snapshot(envelope)

    assert result == snapshot_id
    assert envelope["model_resolution_snapshot_id"] == str(snapshot_id)
