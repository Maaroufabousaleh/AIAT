"""Role-scoped write workflows over canonical executive control-plane paths."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest


class _CFOStorage:
    project_id = UUID("00000000-0000-4000-a000-000000000901")

    async def get_project(self, project_id: UUID):
        return {"id": project_id, "state": "IN_PROGRESS"} if project_id == self.project_id else None

    async def get_model_profile(self, profile_id: str):
        return {"id": uuid4(), "logical_profile_id": profile_id}

    async def create_model_override_request(self, **kwargs):
        return {
            "id": uuid4(),
            "status": "PENDING",
            "secret": "must-not-cross-executive-envelope",
            "scope": {"secret": "must-not-cross-executive-envelope"},
            **kwargs,
        }


@pytest.mark.anyio
async def test_cfo_executive_action_creates_durable_model_override_request(client) -> None:
    from orchestrator_api import main

    previous_storage = main.app.state.storage
    storage = _CFOStorage()
    main.app.state.storage = storage
    try:
        response = await client.post(
            "/executive/actions/cfo/model-overrides",
            json={
                "project_id": str(storage.project_id),
                "requested_profile_id": "profile-cost-review",
                "requested_by": "office_cfo",
                "reason": "Compare a lower-cost approved route for this project",
            },
        )
    finally:
        main.app.state.storage = previous_storage

    assert response.status_code == 201
    body = response.json()
    assert body["schema_version"] == "aiat.executive-action.v1"
    assert body["role"] == "cfo"
    assert body["action"] == "request_model_override"
    assert body["result"]["status"] == "PENDING"
    assert "must-not-cross-executive-envelope" not in response.text
    assert body["evidence"]["kind"] == "model_override_request"
    assert body["evidence"]["project_id"] == str(storage.project_id)


@pytest.mark.anyio
async def test_cto_executive_action_dispatches_through_canonical_worker_route(client, monkeypatch) -> None:
    from orchestrator_api import main

    dispatch = AsyncMock(
        return_value={
            "run_id": "run-cto-1",
            "state": "QUEUED",
            "dispatch_mode": "queued",
            "status_url": "/workers/runs/run-cto-1",
            "events_url": "/workers/runs/run-cto-1/events",
            "accepted": {
                "run_id": "run-cto-1",
                "idempotency_key": "cto-idempotency-1",
                "initial_state": "QUEUED",
            },
            "result": {"secret": "must-not-cross-executive-envelope"},
            "events": [{"secret": "must-not-cross-executive-envelope"}],
        }
    )
    monkeypatch.setattr(main, "dispatch_worker_run", dispatch)
    worker_id = UUID("00000000-0000-4000-a000-000000000902")

    response = await client.post(
        "/executive/actions/cto/worker-runs",
        json={
            "requested_by": "office_cto",
            "dispatch": {
                "worker_id": str(worker_id),
                "idempotency_key": "cto-idempotency-1",
                "task_type": "kpi.reconcile",
                "task_input": {"project": "scoped"},
            },
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["role"] == "cto"
    assert body["result"]["run_id"] == "run-cto-1"
    assert body["result"]["accepted"]["initial_state"] == "QUEUED"
    assert "must-not-cross-executive-envelope" not in response.text
    dispatch.assert_awaited_once()


@pytest.mark.anyio
async def test_ceo_executive_action_uses_privileged_gate_and_returns_safe_summary(client, monkeypatch) -> None:
    from orchestrator_api import main

    gate = AsyncMock(
        return_value={
            "allowed": False,
            "level": "privileged",
            "decision": "pending_approval",
            "record_id": "audit-ceo-1",
            "risk": "high",
            "reason": "pending_human_approval",
            "payload": {"secret": "must-not-return"},
        }
    )
    monkeypatch.setattr(main, "request_privileged_action", gate)

    response = await client.post(
        "/executive/actions/ceo/privileged-actions",
        json={
            "requested_by": "exec_ceo",
            "action": "security.override_cso",
            "payload": {"secret": "input-is-audited-by-the-gate"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "ceo"
    assert body["result"] == {
        "allowed": False,
        "level": "privileged",
        "decision": "pending_approval",
        "record_id": "audit-ceo-1",
        "risk": "high",
        "reason": "pending_human_approval",
    }
    assert "must-not-return" not in response.text
    gate.assert_awaited_once()
    called = gate.await_args.args[0]
    assert called.action == "security.override_cso"
    assert called.actor_role == "ceo"


@pytest.mark.anyio
async def test_worker_principal_cannot_invoke_executive_write_surface(client, monkeypatch) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    monkeypatch.setattr(main, "request_privileged_action", AsyncMock())

    response = await client.post(
        "/executive/actions/ceo/privileged-actions",
        headers={"X-API-Key": "test-worker-key"},
        json={"action": "team.drain", "requested_by": "worker"},
    )

    assert response.status_code == 403
