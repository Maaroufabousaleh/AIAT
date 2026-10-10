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
async def test_cfo_executive_action_rejects_unpublished_scope_fields(client, monkeypatch) -> None:
    from orchestrator_api import main

    create_override = AsyncMock()
    monkeypatch.setattr(main, "_create_model_override", create_override)

    response = await client.post(
        "/executive/actions/cfo/model-overrides",
        json={
            "project_id": str(uuid4()),
            "requested_profile_id": "profile-review",
            "requested_by": "office_cfo",
            "reason": "Compare approved model profiles.",
            "scope": {"arbitrary": "unbounded"},
        },
    )

    assert response.status_code == 422
    create_override.assert_not_awaited()


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
                "prompt": "Reconcile the project KPI report.",
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
    assert dispatch.await_args.args[0].task_input == {"prompt": "Reconcile the project KPI report."}


@pytest.mark.anyio
async def test_cto_executive_action_rejects_worker_specific_unbounded_input(client, monkeypatch) -> None:
    from orchestrator_api import main

    dispatch = AsyncMock()
    monkeypatch.setattr(main, "dispatch_worker_run", dispatch)
    response = await client.post(
        "/executive/actions/cto/worker-runs",
        json={
            "dispatch": {
                "worker_id": str(uuid4()),
                "idempotency_key": "bounded-cto-run",
                "task_type": "code.edit",
                "task_input": {"prompt": "Create the README.", "command": "unbounded field"},
            },
        },
    )

    assert response.status_code == 422
    dispatch.assert_not_awaited()


@pytest.mark.anyio
async def test_executive_action_catalogue_is_derived_from_request_schemas_and_policy(client) -> None:
    from mas_core.policy.privileged_ops import PRIVILEGED_ACTIONS

    response = await client.get("/executive/actions/catalogue")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.executive-actions.catalogue.v1"
    cfo = {field["name"]: field for field in body["cfo_model_override_fields"]}
    assert cfo["project_id"]["option_source"] == "projects"
    assert cfo["requested_profile_id"]["max_length"] == 256
    assert cfo["reason"]["max_length"] == 4000
    cto = {field["name"]: field for field in body["cto_worker_run_fields"]}
    assert cto["prompt"]["required"] is True
    assert cto["prompt"]["max_length"] == 20_000
    assert cto["timeout_seconds"]["maximum"] == 86_400
    actions = {item["action"]: item for item in body["ceo_privileged_actions"]}
    assert set(actions) == set(PRIVILEGED_ACTIONS)
    assert actions["system.restart"]["requires_approval"] is True
    assert {field["name"] for field in actions["container.stop"]["fields"]} == {"container_id", "reason"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action,payload",
    [
        ("system.restart", {"reason": "maintenance", "unregistered": "value"}),
        ("container.stop", {"container_id": {"id": "nested"}, "reason": "stop"}),
        ("worker.force_stop", {"worker_id": "   ", "reason": "stop"}),
        ("system.restart", {"reason": ""}),
        ("unregistered.action", {"reason": "not a catalogue action"}),
    ],
)
async def test_executive_ceo_rejects_unknown_or_malformed_catalogue_payloads(client, monkeypatch, action, payload) -> None:
    from orchestrator_api import main

    gate = AsyncMock()
    gate.ensure_tables = AsyncMock()
    gate.check = AsyncMock()
    monkeypatch.setattr(main, "_priv_gate", lambda: gate)

    response = await client.post(
        "/executive/actions/ceo/privileged-actions",
        json={"action": action, "payload": payload},
    )

    assert response.status_code == 422
    gate.check.assert_not_awaited()


@pytest.mark.anyio
async def test_ceo_executive_action_uses_privileged_gate_and_returns_safe_summary(client, monkeypatch) -> None:
    from orchestrator_api import main

    gate = AsyncMock()
    gate.ensure_tables = AsyncMock()
    gate.check = AsyncMock(
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
    monkeypatch.setattr(main, "_priv_gate", lambda: gate)

    response = await client.post(
        "/executive/actions/ceo/privileged-actions",
        json={
            "requested_by": "forged-human-ceo",
            "action": "security.override_cso",
            "payload": {"finding_id": "SEC-12", "reason": "Request review of the documented finding."},
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
    assert body["requested_by"] == "service"
    assert body["authenticated_principal"] == "service"
    gate.check.assert_awaited_once_with(
        "security.override_cso",
        actor_id="service",
        actor_role="service",
        payload={"finding_id": "SEC-12", "reason": "Request review of the documented finding."},
    )


@pytest.mark.anyio
async def test_worker_principal_cannot_invoke_executive_write_surface(client, monkeypatch) -> None:
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    monkeypatch.setattr(main, "request_privileged_action", AsyncMock())

    response = await client.post(
        "/executive/actions/ceo/privileged-actions",
        headers={"X-API-Key": "test-worker-key"},
        json={"action": "team.drain", "requested_by": "worker", "payload": {"team_id": "dept_devops", "reason": "test authorization"}},
    )

    assert response.status_code == 403
