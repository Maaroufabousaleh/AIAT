"""Operator retry creates a new governed WorkerRun without provider dispatch."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from mas_core.worker_contract.adapters import BaseWorkerAdapter
from mas_core.worker_contract.models import WorkerResult, WorkerUsage

WORKER_ID = UUID("00000000-0000-4000-a000-000000000801")
RUN_ID = UUID("00000000-0000-4000-a000-000000000802")
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000803")
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000804")
CONCURRENT_RETRY_RUN_ID = UUID("00000000-0000-4000-a000-000000000805")
OPERATOR_HEADERS = {"X-API-Key": "test-operator-key"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _dispatch_request(*, project_id: UUID | None = None, tool_grants: list[str] | None = None):
    return {
        "worker_id": str(WORKER_ID),
        "task_type": "local.deterministic_test",
        "task_input": {"operation": "record local result"},
        "project_id": str(project_id) if project_id else None,
        "flow_id": None,
        "flow_instance_id": None,
        "flow_node_execution_id": None,
        "requested_model_profile": None,
        "resolved_model_profile": None,
        "capability_requirements": [],
        "tool_grants": tool_grants or [],
        "permission_requirements": [],
        "workspace_mode": "isolated",
        "timeout_seconds": 30,
        "budget": {"max_cost_usd": 0.75},
        "checkpoint_policy": {},
        "retry_policy": {},
        "extensions": {},
        "trace_id": None,
        "span_id": None,
    }


def _retry_context(*, tool_grants: list[str] | None = None):
    return {
        "requested_model_profile": None,
        "capability_requirements": [],
        "tool_grants": tool_grants or [],
        "permission_requirements": [],
        "workspace_mode": "isolated",
        "timeout_seconds": 30,
        "budget": {"max_cost_usd": 0.75},
        "checkpoint_policy": {},
        "retry_policy": {},
        "runtime_extensions": {},
        "model_policy_layers": [],
        "worker_required_model_capabilities": [],
        "steward_required_model_capabilities": [],
        "task_required_model_capabilities": [],
        "adapter_required_model_capabilities": [],
        "prompt_tokens": 0,
        "expected_output_tokens": 0,
        "budget_usd": 0.75,
        "model_override_request_id": None,
        "model_override_approval_id": None,
        "queue_priority": 0,
        "lease_seconds": 300,
    }


class _RetryStorage:
    def __init__(self, *, state: str = "FAILED", project_id: UUID | None = None, tool_grants: list[str] | None = None):
        self.source = {
            "id": RUN_ID,
            "worker_id": WORKER_ID,
            "task_type": "local.deterministic_test",
            "state": state,
            "project_id": project_id,
            "request_json": _dispatch_request(project_id=project_id, tool_grants=tool_grants),
            "retry_context_json": _retry_context(tool_grants=tool_grants),
            "result_json": {"success": False, "output": {"original": True}},
            "error_json": {"code": "LOCAL_FIXTURE_FAILURE", "message": "Original result retained."},
            "completed_at": "2026-10-07T12:00:00Z",
        }
        self.runs: dict[UUID, dict[str, object]] = {RUN_ID: self.source}
        self.transitions: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []
        self.usage: list[dict[str, object]] = []
        self.snapshots: list[dict[str, object]] = []
        self.reservations: list[dict[str, object]] = []
        self.settlements: list[dict[str, object]] = []
        self.assignments: list[dict[str, object]] = []
        self.tool_effects: list[dict[str, object]] = []
        self.insert_retry_before_create = False
        self.claim_calls = 0

    async def get_worker_run(self, run_id: UUID):
        row = self.runs.get(run_id)
        return dict(row) if row is not None else None

    async def get_worker_run_by_idempotency_key(self, *, worker_id: UUID, idempotency_key: str):
        return next(
            (
                dict(row)
                for row in self.runs.values()
                if row.get("worker_id") == worker_id and row.get("idempotency_key") == idempotency_key
            ),
            None,
        )

    async def get_worker_run_retry(self, source_run_id: UUID):
        return next(
            (
                dict(row)
                for row in self.runs.values()
                if row.get("retry_of_run_id") == source_run_id
            ),
            None,
        )

    async def has_worker_tool_effects(self, run_id: UUID):
        return any(row.get("run_id", RUN_ID) == run_id for row in self.tool_effects)

    async def get_worker(self, worker_id: UUID):
        if worker_id != WORKER_ID:
            return None
        return {
            "id": WORKER_ID,
            "status": "ACTIVE",
            "model_mode": "none",
            "adapter_config": {},
            "active_adapter_id": uuid4(),
        }

    async def get_project(self, project_id: UUID):
        return {"id": project_id, "company_id": COMPANY_ID, "state": "IN_PROGRESS"}

    async def list_company_worker_assignments(self, company_id: UUID):
        assert company_id == COMPANY_ID
        return self.assignments

    async def create_model_resolution_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)

    async def reserve_budget(self, **kwargs):
        existing = next(
            (row for row in self.reservations if row["idempotency_key"] == kwargs["idempotency_key"]),
            None,
        )
        if existing is not None:
            return existing
        row = {"id": uuid4(), **kwargs, "state": "RESERVED"}
        self.reservations.append(row)
        return row

    async def settle_budget_reservation(self, reservation_id, *, state, amount=None, metadata=None):
        self.settlements.append({"reservation_id": reservation_id, "state": state, "amount": amount, "metadata": metadata})
        for row in self.reservations:
            if row["id"] == reservation_id:
                row["state"] = state

    async def create_worker_run(self, **kwargs):
        if self.insert_retry_before_create:
            self.insert_retry_before_create = False
            existing_retry = {
                **kwargs,
                "id": CONCURRENT_RETRY_RUN_ID,
                "request_json": kwargs["request"],
                "state": "QUEUED",
                "retry_of_run_id": RUN_ID,
            }
            self.runs[CONCURRENT_RETRY_RUN_ID] = existing_retry
            return dict(existing_retry)
        existing = await self.get_worker_run_by_idempotency_key(
            worker_id=kwargs["worker_id"], idempotency_key=kwargs["idempotency_key"]
        )
        if existing is not None:
            return existing
        source = self.runs.get(kwargs.get("retry_of_run_id")) if kwargs.get("retry_of_run_id") else None
        if kwargs.get("retry_of_run_id") and (source is None or source["state"] not in {"FAILED", "TIMED_OUT"}):
            raise ValueError("retry source is not retryable")
        if source is not None and await self.get_worker_run_retry(UUID(str(source["id"]))):
            raise ValueError("retry source already has a canonical retry run")
        row: dict[str, object] = {
            **kwargs,
            "id": kwargs["run_id"],
            "request_json": kwargs["request"],
            "state": kwargs.get("state", "CREATED"),
            "retry_context_json": kwargs.get("retry_context_json") or {},
            "attempt_count": 0,
        }
        self.runs[UUID(str(row["id"]))] = row
        return dict(row)

    async def claim_worker_run(self, **_kwargs):
        self.claim_calls += 1
        return {"state": "CLAIMED"}

    async def transition_worker_run(self, run_id: UUID, *, new_state: str, expected_state: str | None = None, **kwargs):
        row = self.runs.get(run_id)
        if row is None or (expected_state is not None and row["state"] != expected_state):
            return None
        previous = str(row["state"])
        row["state"] = new_state
        if kwargs.get("result") is not None:
            row["result_json"] = kwargs["result"]
        if kwargs.get("error") is not None:
            row["error_json"] = kwargs["error"]
        row.update(kwargs.get("transition_metadata") or {})
        self.transitions.append({"run_id": run_id, "from_state": previous, "to_state": new_state, **kwargs})
        return dict(row)

    async def append_worker_event(self, **kwargs):
        self.events.append(kwargs)
        return kwargs

    async def create_worker_usage(self, *, run_id: UUID, usage: dict[str, object], **kwargs):
        self.usage.append({"run_id": run_id, **usage, **kwargs})


class _DeterministicLocalAdapter(BaseWorkerAdapter):
    runtime_type = "local-retry-fixture"
    runtime_version = "1"

    def __init__(self):
        from mas_core.worker_contract.models import WorkerCapabilities

        super().__init__(worker_id=str(WORKER_ID), capabilities=WorkerCapabilities())
        self.execution_count = 0

    async def _execute(self, request):
        self.execution_count += 1
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"local_fixture": True},
            usage=WorkerUsage(cost_usd=Decimal("0.10")),
        )


def _install_storage(storage: _RetryStorage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
@pytest.mark.parametrize("source_state", ["FAILED", "TIMED_OUT"])
async def test_operator_retry_runs_through_controller_and_replays_by_key(
    client, monkeypatch, source_state: str
) -> None:
    import orchestrator_api.main as main

    storage = _RetryStorage(state=source_state)
    original = deepcopy(storage.source)
    adapter = _DeterministicLocalAdapter()
    _install_storage(storage)
    monkeypatch.setattr(main, "_certified_worker_adapter", lambda *_args: _return_adapter(adapter))

    payload = {"idempotency_key": "operator-click-01", "reason": "Retry after local fixture failure"}
    response = await client.post(
        f"/workers/runs/{RUN_ID}/retry", headers=OPERATOR_HEADERS, json=payload
    )

    assert response.status_code == 202, response.text
    result = response.json()
    retry_id = UUID(result["run_id"])
    assert retry_id != RUN_ID
    assert result["retry_of_run_id"] == str(RUN_ID)
    assert result["state"] == "SUCCEEDED"
    child = storage.runs[retry_id]
    assert child["retry_of_run_id"] == RUN_ID
    assert child["idempotency_key"] == f"operator-retry:{RUN_ID}:operator-click-01"
    assert child["retry_context_json"]["operator_retry"] == {
        "requested_by": "operator",
        "reason": "Retry after local fixture failure",
        "source_run_id": str(RUN_ID),
        "retry_request_key": "operator-click-01",
    }
    assert storage.source == original
    assert adapter.execution_count == 1
    assert len(storage.snapshots) == 1  # model_mode=none policy decision only
    assert len(storage.usage) == 1
    assert len(storage.reservations) == 2
    assert len(storage.settlements) == 2
    assert all("provider" not in row and "api_key" not in row for row in storage.snapshots)

    replay = await client.post(
        f"/workers/runs/{RUN_ID}/retry", headers=OPERATOR_HEADERS, json=payload
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["run_id"] == str(retry_id)
    assert replay.json()["dispatch_mode"] == "replay"
    assert adapter.execution_count == 1
    assert len(storage.snapshots) == 1
    assert len(storage.reservations) == 2
    assert (await client.get(f"/workers/runs/{RUN_ID}", headers=OPERATOR_HEADERS)).json()["retry_run_id"] == str(retry_id)
    await adapter.close()


async def _return_adapter(adapter):
    return adapter


@pytest.mark.anyio
async def test_retry_requires_operator_and_rejects_successful_source(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RetryStorage(state="SUCCEEDED")
    _install_storage(storage)
    monkeypatch.setattr(main, "_certified_worker_adapter", lambda *_args: _return_adapter(_DeterministicLocalAdapter()))
    payload = {"idempotency_key": "operator-click-02", "reason": "try again"}

    denied = await client.post(f"/workers/runs/{RUN_ID}/retry", json=payload)
    assert denied.status_code == 403
    assert not storage.reservations

    rejected = await client.post(
        f"/workers/runs/{RUN_ID}/retry", headers=OPERATOR_HEADERS, json=payload
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "RUN_NOT_RETRYABLE"
    assert list(storage.runs) == [RUN_ID]
    assert not storage.reservations


@pytest.mark.anyio
async def test_retry_rechecks_current_company_assignment_before_creating_run(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RetryStorage(project_id=PROJECT_ID, tool_grants=["repo.write"])
    original = deepcopy(storage.source)
    _install_storage(storage)
    adapter = _DeterministicLocalAdapter()
    monkeypatch.setattr(main, "_certified_worker_adapter", lambda *_args: _return_adapter(adapter))

    response = await client.post(
        f"/workers/runs/{RUN_ID}/retry",
        headers=OPERATOR_HEADERS,
        json={"idempotency_key": "operator-click-03", "reason": "retry after permission review"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "COMPANY_ASSIGNMENT_REQUIRED"
    assert storage.source == original
    assert list(storage.runs) == [RUN_ID]


@pytest.mark.anyio
async def test_retry_rejects_flow_binding_and_existing_tool_effects(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RetryStorage()
    adapter = _DeterministicLocalAdapter()
    _install_storage(storage)
    monkeypatch.setattr(main, "_certified_worker_adapter", lambda *_args: _return_adapter(adapter))

    storage.source["flow_instance_id"] = uuid4()
    flow_response = await client.post(
        f"/workers/runs/{RUN_ID}/retry",
        headers=OPERATOR_HEADERS,
        json={"idempotency_key": "flow-run", "reason": "retry"},
    )
    assert flow_response.status_code == 409
    assert flow_response.json()["detail"]["code"] == "RUN_RETRY_REQUIRES_FLOW_CONTROLLER"
    assert list(storage.runs) == [RUN_ID]

    storage.source["flow_instance_id"] = None
    storage.tool_effects.append({"run_id": RUN_ID, "state": "COMPLETED"})
    effect_response = await client.post(
        f"/workers/runs/{RUN_ID}/retry",
        headers=OPERATOR_HEADERS,
        json={"idempotency_key": "effect-run", "reason": "retry"},
    )
    assert effect_response.status_code == 409
    assert effect_response.json()["detail"]["code"] == "RUN_RETRY_EFFECTS_REVIEW_REQUIRED"
    assert list(storage.runs) == [RUN_ID]
    assert not storage.reservations
    assert adapter.execution_count == 0
    await adapter.close()


@pytest.mark.anyio
async def test_legacy_run_context_is_rebuilt_from_persisted_worker_request(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    storage = _RetryStorage(tool_grants=["local.inspect"])
    storage.source["retry_context_json"] = {}
    adapter = _DeterministicLocalAdapter()
    _install_storage(storage)
    monkeypatch.setattr(main, "_certified_worker_adapter", lambda *_args: _return_adapter(adapter))

    response = await client.post(
        f"/workers/runs/{RUN_ID}/retry",
        headers=OPERATOR_HEADERS,
        json={"idempotency_key": "legacy-context", "reason": "retry with saved grants"},
    )

    assert response.status_code == 202, response.text
    retried = storage.runs[UUID(response.json()["run_id"])]
    assert retried["retry_context_json"]["tool_grants"] == ["local.inspect"]
    assert retried["retry_context_json"]["workspace_mode"] == "isolated"
    assert retried["retry_context_json"]["budget_usd"] == 0.75
    await adapter.close()


@pytest.mark.anyio
async def test_concurrent_queued_retry_replay_does_not_claim_or_dispatch_twice(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    storage = _RetryStorage()
    storage.insert_retry_before_create = True
    retry_key = f"operator-retry:{RUN_ID}:queued-concurrent"
    shared_reservations = [
        {
            "id": uuid4(),
            "idempotency_key": f"worker-run:{WORKER_ID}:{retry_key}:{budget_key}",
            "run_id": CONCURRENT_RETRY_RUN_ID,
            "state": "RESERVED",
        }
        for budget_key in ("max_cost_usd", "max_concurrent_runs")
    ]
    storage.reservations.extend(shared_reservations)
    adapter = _DeterministicLocalAdapter()
    _install_storage(storage)
    monkeypatch.setattr(main, "_certified_worker_adapter", lambda *_args: _return_adapter(adapter))

    response = await client.post(
        f"/workers/runs/{RUN_ID}/retry",
        headers=OPERATOR_HEADERS,
        json={"idempotency_key": "queued-concurrent", "reason": "retry"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["run_id"] == str(CONCURRENT_RETRY_RUN_ID)
    assert response.json()["dispatch_mode"] == "replay"
    assert storage.claim_calls == 0
    assert adapter.execution_count == 0
    assert storage.reservations == shared_reservations
    assert all(reservation["state"] == "RESERVED" for reservation in storage.reservations)
    assert storage.settlements == []
    await adapter.close()
