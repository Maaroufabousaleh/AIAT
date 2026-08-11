"""Non-mutating flow readiness preview coverage."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

WORKER_ID = UUID("00000000-0000-4000-a000-000000000701")


def _definition() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "config": {}},
            {
                "id": "task",
                "type": "task",
                "label": "Governed task",
                "config": {
                    "worker_id": str(WORKER_ID),
                    "task_type": "test.run",
                    "model_mode": "none",
                    "required_capabilities": ["test.run"],
                    "checkpoint_policy": {"mode": "native", "required": True},
                },
            },
            {"id": "end", "type": "end", "label": "End", "config": {}},
        ],
        "edges": [
            {"id": "start-task", "source": "start", "target": "task"},
            {"id": "task-end", "source": "task", "target": "end"},
        ],
    }


class _DryRunStorage:
    def __init__(self, *, worker_status: str = "ACTIVE") -> None:
        self.worker_status = worker_status

    async def get_worker(self, worker_id: UUID):
        if worker_id != WORKER_ID:
            return None
        return {
            "id": WORKER_ID,
            "status": self.worker_status,
            "adapter_config": {},
        }

    async def get_active_runtime_adapter(self, worker_id: UUID):
        if worker_id != WORKER_ID:
            return None
        return {
            "status": "active",
            "conformance_status": "passed",
            "capabilities_json": {
                "checkpoint_mode": "native",
                "capability_names": ["test.run"],
            },
        }


def _patch_storage(storage: _DryRunStorage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
async def test_flow_dry_run_reports_governed_worker_readiness(client) -> None:
    _patch_storage(_DryRunStorage())

    response = await client.post("/flows/dry-run", json={"definition_json": _definition()})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["nodes"] == [
        {
            "node_id": "task",
            "ready": True,
            "checks": {
                "worker_status": "ACTIVE",
                "adapter": {"status": "active", "conformance_status": "passed"},
                "required_capabilities": {"required": ["test.run"], "missing": []},
                "model_policy": "model-less",
            },
        }
    ]


@pytest.mark.anyio
async def test_flow_dry_run_rejects_inactive_worker(client) -> None:
    _patch_storage(_DryRunStorage(worker_status="INACTIVE"))

    response = await client.post("/flows/dry-run", json={"definition_json": _definition()})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(error["code"] == "WORKER_NOT_ACTIVE" for error in body["errors"])


@pytest.mark.anyio
async def test_flow_dry_run_surfaces_legacy_task_alias_migration_guidance(client) -> None:
    _patch_storage(_DryRunStorage())
    definition = {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "legacy-task",
                "type": "task",
                "config": {"team_id": "dept_qa", "action": "test.run"},
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "start-task", "source": "start", "target": "legacy-task"},
            {"id": "task-end", "source": "legacy-task", "target": "end"},
        ],
    }

    response = await client.post("/flows/dry-run", json={"definition_json": definition})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["compatibility_aliases"] == [
        {
            "node_id": "legacy-task",
            "deprecated_fields": ["action", "team_id"],
            "has_worker_id": False,
            "disposition": "manual_worker_binding_required",
            "recommendation": "bind a concrete worker_id before activation; team_id/action cannot dispatch a Worker Run",
        }
    ]
    assert body["nodes"][0]["compatibility_aliases"]["disposition"] == "manual_worker_binding_required"


def test_model_less_task_rejects_a_model_profile() -> None:
    from mas_core.workflow import parse_flow_definition, validate_flow

    definition = _definition()
    definition["nodes"][1]["config"]["model_profile_id"] = "coding"  # type: ignore[index]

    errors = validate_flow(parse_flow_definition(definition))

    assert any("model_mode none does not allow" in error for error in errors)


@pytest.mark.anyio
async def test_flow_dry_run_uses_the_worker_model_policy_for_override_checks(client) -> None:
    class ModelGovernedStorage(_DryRunStorage):
        async def get_worker(self, worker_id: UUID):
            worker = await super().get_worker(worker_id)
            assert worker is not None
            return {
                **worker,
                "model_mode": "aiat_gateway",
                "model_profile_id": "worker-default",
            }

    definition = _definition()
    definition["nodes"][1]["config"].update(  # type: ignore[index]
        {"model_mode": "aiat_gateway", "model_profile_id": "task-override"}
    )
    _patch_storage(ModelGovernedStorage())

    response = await client.post("/flows/dry-run", json={"definition_json": definition})

    assert response.status_code == 200
    assert any(
        error["code"] == "MODEL_OVERRIDE_APPROVAL_REQUIRED"
        for error in response.json()["errors"]
    )


@pytest.mark.anyio
async def test_dispatch_rejects_required_checkpoint_when_adapter_lacks_it(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    from mas_core.worker_contract import WorkerCapabilities

    class UnsupportedCheckpointAdapter:
        capabilities = WorkerCapabilities(checkpoint_mode="unsupported")

    _patch_storage(_DryRunStorage())
    monkeypatch.setattr(
        main,
        "_certified_worker_adapter",
        AsyncMock(return_value=UnsupportedCheckpointAdapter()),
    )

    response = await client.post(
        "/workers/runs",
        json={
            "worker_id": str(WORKER_ID),
            "idempotency_key": "checkpoint-required",
            "task_type": "test.run",
            "checkpoint_policy": {"required": True},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CHECKPOINT_UNSUPPORTED"
