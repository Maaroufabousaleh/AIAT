from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_local_queued_dispatch_is_not_claimed_by_orchestrator(monkeypatch) -> None:
    import orchestrator_api.main as main

    class Storage:
        def __init__(self) -> None:
            self.claims = []
            self.run_id = uuid4()

        async def get_worker(self, worker_id):
            return {
                "id": worker_id,
                "status": "ACTIVE",
                "model_mode": "none",
                "adapter_config": {},
            }

        async def create_model_resolution_snapshot(self, **_values):
            return None

        async def create_worker_run(self, **values):
            self.run_id = values["run_id"]
            return {"id": self.run_id, "state": "QUEUED"}

        async def claim_worker_run(self, **values):
            self.claims.append(values)
            return None

        async def reserve_budget(self, **values):
            return {"id": uuid4(), **values}

        async def settle_budget_reservation(self, *_args, **_values):
            return None

    storage = Storage()
    scheduled = []

    class LocalHostRuntime:
        async def schedule_run(self, run_id):
            scheduled.append(run_id)
            return True

    monkeypatch.setattr(main.app.state, "storage", storage)
    monkeypatch.setattr(main.app.state, "local_host_queue_config", object(), raising=False)
    monkeypatch.setattr(main.app.state, "local_host_queue_runtime", LocalHostRuntime(), raising=False)
    monkeypatch.setattr(main, "_certified_worker_adapter", AsyncMock(return_value=object()))

    result = await main.dispatch_worker_run(
        main.WorkerRunDispatchRequest(
            worker_id=uuid4(),
            idempotency_key="local-host-queue-dispatch",
            task_type="test.local",
        )
    )

    assert result["state"] == "QUEUED"
    assert result["dispatch_mode"] == "queued"
    assert result["host_binding_url"].endswith("/host-binding")
    assert result["tool_effects_url"].endswith("/tool-effects")
    assert scheduled == [storage.run_id]
    assert storage.claims == []


@pytest.mark.anyio
async def test_host_binding_and_tool_effect_routes_are_payload_safe_and_operator_only(
    monkeypatch,
) -> None:
    import orchestrator_api.main as main

    from mas_core.worker_registry import run_host_binding

    run_id = uuid4()
    storage = SimpleNamespace(
        get_worker_run=AsyncMock(return_value={"id": run_id, "request_json": {"secret": "payload"}}),
        list_worker_tool_effects=AsyncMock(
            return_value=[
                {
                    "id": uuid4(),
                    "run_id": run_id,
                    "request_id": uuid4(),
                    "tool_name": "opencode.workspace_pytest",
                    "request_sha256": "a" * 64,
                    "state": "COMPLETED",
                    "response_success": True,
                    "created_at": "2026-10-12T00:00:00+00:00",
                    "updated_at": "2026-10-12T00:00:00+00:00",
                    "completed_at": "2026-10-12T00:00:00+00:00",
                    "request_payload": {"secret": "private request"},
                    "response_payload": {"secret": "private response"},
                }
            ]
        ),
    )

    class Bindings:
        def __init__(self, _storage):
            pass

        async def get(self, _run_id):
            return {
                "schema_version": "aiat.worker-run-host-binding.v1",
                "id": uuid4(),
                "run_id": run_id,
                "worker_id": uuid4(),
                "host_uuid": uuid4(),
                "host_id": "alpha-local-ab12cd34ef56",
                "host_plane": "worker",
                "host_status": "READY",
                "reservation_id": uuid4(),
                "host_lease_generation": 1,
                "current_host_lease_generation": 1,
                "state": "COMMITTED",
                "reservation_state": "COMMITTED",
                "reservation_lease_valid": None,
                "current_host_lease_valid": True,
                "owner": "internal-owner",
                "metadata": {"private": "metadata"},
                "created_at": "2026-10-12T00:00:00+00:00",
                "committed_at": "2026-10-12T00:00:00+00:00",
                "released_at": None,
            }

    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(run_host_binding, "WorkerRunHostBindingService", Bindings)
    operator = SimpleNamespace(state=SimpleNamespace(aiat_auth_principal="operator"))

    binding = await main.get_worker_run_host_binding_api(run_id, operator)
    effects = await main.get_worker_run_tool_effects_api(run_id, operator)

    assert binding["host_binding"]["state"] == "COMMITTED"
    assert "metadata" not in binding["host_binding"]
    assert "owner" not in binding["host_binding"]
    assert effects["tool_effects"][0]["response_success"] is True
    assert "request_payload" not in effects["tool_effects"][0]
    assert "response_payload" not in effects["tool_effects"][0]
    with pytest.raises(main.HTTPException) as denied:
        await main.get_worker_run_host_binding_api(
            run_id,
            SimpleNamespace(state=SimpleNamespace(aiat_auth_principal="service")),
        )
    assert denied.value.status_code == 403
