"""Company grant and budget-settlement coverage for worker dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

WORKER_ID = UUID("00000000-0000-4000-a000-000000000701")
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000702")
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000703")


@pytest.mark.anyio
async def test_worker_heartbeat_requires_and_forwards_attempt_fence(monkeypatch) -> None:
    import orchestrator_api.main as main

    run_id = uuid4()
    storage = SimpleNamespace(
        heartbeat_worker_run=AsyncMock(return_value={"id": run_id, "state": "RUNNING"})
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)
    service_request = SimpleNamespace(state=SimpleNamespace(aiat_auth_principal="service"))

    result = await main.heartbeat_worker_run(
        run_id,
        {"owner": "executor-a", "attempt_count": 3, "lease_seconds": 45},
        service_request,
    )

    assert result["state"] == "RUNNING"
    storage.heartbeat_worker_run.assert_awaited_once_with(
        run_id,
        owner="executor-a",
        attempt_count=3,
        lease_seconds=45,
    )

    with pytest.raises(main.HTTPException) as missing_fence:
        await main.heartbeat_worker_run(
            run_id,
            {"owner": "executor-a"},
            service_request,
        )
    assert missing_fence.value.status_code == 422

    with pytest.raises(main.HTTPException) as denied_worker:
        await main.heartbeat_worker_run(
            run_id,
            {"owner": "executor-a", "attempt_count": 3},
            SimpleNamespace(state=SimpleNamespace(aiat_auth_principal="worker")),
        )
    assert denied_worker.value.status_code == 403
    storage.heartbeat_worker_run.assert_awaited_once()


@pytest.mark.anyio
async def test_worker_tool_dispatch_forwards_only_controller_owned_claim_fence(monkeypatch) -> None:
    import orchestrator_api.main as main

    from mas_core.worker_contract import WorkerToolRequest

    run_id = uuid4()
    worker_id = uuid4()
    storage = SimpleNamespace(
        get_worker_run=AsyncMock(
            return_value={
                "id": run_id,
                "project_id": PROJECT_ID,
                "request_json": {"budget": {"max_cost_usd": 0.25}},
            }
        )
    )
    captured: dict[str, object] = {}

    class ToolService:
        async def request(self, method, path, body, *, timeout):
            captured.update({"method": method, "path": path, "body": body, "timeout": timeout})
            return {"success": True, "result": {"ok": True}}

    monkeypatch.setattr(main, "_tool_service_client", lambda: ToolService())
    tool_request = WorkerToolRequest(
        run_id=run_id,
        tool_name="workspace.read_file",
        arguments={"path": "README.md"},
        permission_scope=["workspace.read"],
        idempotency_key="read-readme",
    )
    tool_request._execution_claim_fence = {
        "claim_owner": "host-a",
        "attempt_count": 5,
    }

    response = await main._worker_tool_dispatcher(
        storage,
        {"id": worker_id, "team_id": "engineering"},
    )(tool_request)

    body = captured["body"]
    assert response.success is True
    assert isinstance(body, dict)
    assert body["claim_owner"] == "host-a"
    assert body["claim_attempt_count"] == 5
    assert "execution_claim_fence" not in body


@pytest.mark.asyncio
async def test_queued_run_stops_adapter_when_attempt_heartbeat_is_lost(monkeypatch) -> None:
    import asyncio

    import orchestrator_api.main as main

    run_id = uuid4()
    cancelled = asyncio.Event()
    cancellation_requests = []

    async def mark_cancelled(request):
        cancellation_requests.append(request)
        cancelled.set()

    adapter = SimpleNamespace(cancel=AsyncMock(side_effect=mark_cancelled))

    class Controller:
        async def execute(self, *_args, **_kwargs):
            await asyncio.wait_for(cancelled.wait(), timeout=1)
            return SimpleNamespace(state="CANCELLED", result=None)

    storage = SimpleNamespace(
        heartbeat_worker_run=AsyncMock(return_value=None),
        get_worker_run=AsyncMock(return_value={"id": run_id, "state": "RUNNING"}),
    )
    original_sleep = asyncio.sleep

    async def immediate_sleep(_delay):
        await original_sleep(0)

    monkeypatch.setattr(main.asyncio, "sleep", immediate_sleep)
    monkeypatch.setattr(main.app.state, "worker_run_tasks", set(), raising=False)

    await main._execute_queued_worker_run(
        controller=Controller(),
        request=SimpleNamespace(),
        adapter=adapter,
        worker={"id": uuid4()},
        storage=storage,
        model_resolution_snapshot_id=None,
        lease_seconds=30,
        canonical_run_id=run_id,
        attempt_count=4,
    )

    adapter.cancel.assert_awaited_once()
    assert cancellation_requests[0].execution_claim_fence == {
        "claim_owner": f"orchestrator-background:{run_id}",
        "attempt_count": 4,
    }
    storage.heartbeat_worker_run.assert_awaited_once_with(
        run_id,
        owner=f"orchestrator-background:{run_id}",
        attempt_count=4,
        lease_seconds=30,
    )


def test_model_override_expiry_normalizes_serialized_values_and_fails_closed() -> None:
    import orchestrator_api.main as main

    reference = datetime(2026, 1, 1, tzinfo=UTC)
    assert main._model_override_is_expired(None, now=reference) is False
    assert main._model_override_is_expired(reference - timedelta(seconds=1), now=reference) is True
    assert main._model_override_is_expired("2025-12-31T23:59:59Z", now=reference) is True
    assert main._model_override_is_expired("2026-01-01T00:00:01+00:00", now=reference) is False
    assert main._model_override_is_expired("not-a-timestamp", now=reference) is True
    assert main._model_override_is_expired(object(), now=reference) is True


def test_opencode_model_identity_uses_only_catalogued_gateway_aliases() -> None:
    import orchestrator_api.main as main

    assert main._governed_opencode_model_mapper("omniroute-coding") == "aiat/omniroute-coding"
    assert main._governed_opencode_model_mapper("unknown-unqualified-alias") is None


@pytest.mark.anyio
async def test_certified_opencode_adapter_receives_catalogue_model_mapper(monkeypatch) -> None:
    import orchestrator_api.main as main

    from mas_core.worker_registry import runtime_adapters

    worker_id = uuid4()
    adapter_id = uuid4()

    class Storage:
        async def get_runtime_adapter(self, requested_id):
            assert requested_id == adapter_id
            return {
                "id": adapter_id,
                "worker_id": worker_id,
                "status": "active",
                "conformance_status": "passed",
                "transport_type": "opencode",
                "capabilities_json": {},
            }

    class CapturingOpenCodeAdapter:
        def __init__(self, _verification, *, model_mapper, **_kwargs):
            self.model_mapper = model_mapper

    monkeypatch.setattr(
        runtime_adapters.OpenCodeInterfaceVerification,
        "from_report",
        staticmethod(lambda _configured: object()),
    )
    monkeypatch.setattr(runtime_adapters, "OpenCodeAdapter", CapturingOpenCodeAdapter)
    monkeypatch.setattr(main, "_worker_adapter_runtimes", {})
    monkeypatch.setenv("AIAT_TEST_OPENCODE_PASSWORD", "test-runtime-password")
    monkeypatch.setenv("TOOL_SECRET", "test-tool-secret")

    adapter = await main._certified_worker_adapter(
        Storage(),
        {
            "id": worker_id,
            "active_adapter_id": adapter_id,
            "adapter_config": {
                "base_url": "http://opencode-runtime:4096",
                "interface_verification": {"report_id": "test"},
                "auth_password_env": "AIAT_TEST_OPENCODE_PASSWORD",
            },
        },
    )

    assert adapter is not None
    assert adapter.model_mapper("omniroute-coding") == "aiat/omniroute-coding"
    assert adapter.model_mapper("unknown-unqualified-alias") is None


class _Adapter:
    def __init__(self) -> None:
        from mas_core.worker_contract import WorkerCapabilities

        self.capabilities = WorkerCapabilities()


class _DispatchStorage:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.reservations: list[dict[str, object]] = []
        self.settlements: list[dict[str, object]] = []
        self.snapshots: list[dict[str, object]] = []

    async def get_worker(self, worker_id: UUID):
        if worker_id != WORKER_ID:
            return None
        return {
            "id": WORKER_ID,
            "status": "ACTIVE",
            "model_mode": "none",
            "adapter_config": {},
        }

    async def create_model_resolution_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)
        return None

    async def reserve_budget(self, **kwargs):
        reservation = {"id": uuid4(), **kwargs}
        self.reservations.append(reservation)
        return reservation

    async def settle_budget_reservation(
        self, reservation_id, *, state, amount=None, metadata=None
    ):
        self.settlements.append(
            {
                "reservation_id": reservation_id,
                "state": state,
                "amount": amount,
                "metadata": metadata,
            }
        )

    async def create_worker_run(self, **kwargs):
        if self.fail_at == "create":
            raise RuntimeError("create failed")
        return {"id": kwargs["run_id"], "state": "QUEUED"}

    async def claim_worker_run(self, **_kwargs):
        if self.fail_at == "claim":
            raise RuntimeError("claim failed")
        return None


@pytest.mark.anyio
async def test_cost_reservation_requires_positive_per_run_budget() -> None:
    import orchestrator_api.main as main

    async def reserve_for(budget_usd):
        storage = _DispatchStorage()
        request = SimpleNamespace(
            budget_usd=budget_usd,
            budget={},
            idempotency_key="per-run-budget-boundary",
            run_id=uuid4(),
            task_type="test.run",
        )
        await main._reserve_worker_run_budgets(
            storage=storage,
            request=request,
            worker_id=WORKER_ID,
            project_id=None,
            company_id=COMPANY_ID,
        )
        return [(row["budget_key"], row["amount"]) for row in storage.reservations]

    omitted_budget = await reserve_for(None)
    zero_budget = await reserve_for(Decimal("0"))
    positive_budget = await reserve_for(Decimal("1.25"))

    assert omitted_budget == zero_budget == [
        ("max_concurrent_runs", Decimal("1")),
    ]
    assert positive_budget == [
        ("max_cost_usd", Decimal("1.25")),
        ("max_concurrent_runs", Decimal("1")),
    ]


def _patch_storage(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
async def test_non_llm_dispatch_keeps_policy_snapshot_out_of_model_attribution(
    monkeypatch,
) -> None:
    import orchestrator_api.main as main

    import mas_core.worker_contract as worker_contract

    class ProjectDispatchStorage(_DispatchStorage):
        async def get_project(self, project_id):
            return {"id": project_id, "company_id": None}

        async def list_company_worker_assignments(self, _company_id):
            return []

    storage = ProjectDispatchStorage()
    _patch_storage(storage)
    captured: dict[str, object] = {}

    class RecordingController:
        def __init__(self, *, storage) -> None:
            self.storage = storage

        async def execute(self, request, _adapter, **kwargs):
            captured["request"] = request
            captured["kwargs"] = kwargs
            return worker_contract.WorkerRunOutcome(
                run_id=request.run_id,
                state="SUCCEEDED",
                result=worker_contract.WorkerResult(
                    run_id=request.run_id,
                    worker_id=request.worker_id,
                    success=True,
                ),
            )

    monkeypatch.setattr(worker_contract, "WorkerRunController", RecordingController)
    monkeypatch.setattr(main, "_certified_worker_adapter", AsyncMock(return_value=_Adapter()))

    result = await main.dispatch_worker_run(
        main.WorkerRunDispatchRequest(
            worker_id=WORKER_ID,
            idempotency_key="non-llm-policy-only",
            task_type="deterministic.test",
            project_id=PROJECT_ID,
            dispatch_mode="inline",
        )
    )

    assert result["state"] == "SUCCEEDED"
    assert len(storage.snapshots) == 1
    assert storage.snapshots[0]["project_id"] == PROJECT_ID
    assert captured["kwargs"]["model_resolution_snapshot_id"] is None  # type: ignore[index]
    request = captured["request"]
    assert request.resolved_model_profile is None  # type: ignore[union-attr]
    assert request.extensions["policy_snapshot_id"] == str(
        storage.snapshots[0]["snapshot"]["snapshot_id"]  # type: ignore[index]
    )


@pytest.mark.anyio
async def test_company_dispatch_rejects_grants_outside_active_assignment(
    client, monkeypatch
) -> None:
    class CompanyStorage(_DispatchStorage):
        async def get_project(self, project_id):
            assert project_id == PROJECT_ID
            return {"id": project_id, "company_id": COMPANY_ID}

        async def list_company_worker_assignments(self, company_id):
            assert company_id == COMPANY_ID
            return [
                {
                    "worker_id": WORKER_ID,
                    "status": "ACTIVE",
                    "tool_grants": ["repo.read"],
                    "permission_grants": ["artifact.read"],
                }
            ]

    storage = CompanyStorage()
    _patch_storage(storage)

    response = await client.post(
        "/workers/runs",
        json={
            "worker_id": str(WORKER_ID),
            "idempotency_key": "company-grant-rejection",
            "task_type": "test.run",
            "project_id": str(PROJECT_ID),
            "tool_grants": ["repo.read", "repo.write"],
            "permission_requirements": ["artifact.delete"],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "COMPANY_ASSIGNMENT_GRANT_EXCEEDED",
        "message": "Worker Run grants exceed the active company manifest assignment",
        "unapproved_tool_grants": ["repo.write"],
        "unapproved_permission_requirements": ["artifact.delete"],
    }
    assert storage.reservations == []


@pytest.mark.anyio
async def test_company_dispatch_rejects_assignment_pending_approval(client, monkeypatch) -> None:
    import orchestrator_api.main as main

    class CompanyStorage(_DispatchStorage):
        def __init__(self):
            super().__init__()
            self.created_runs: list[dict[str, object]] = []

        async def get_project(self, project_id):
            assert project_id == PROJECT_ID
            return {"id": project_id, "company_id": COMPANY_ID}

        async def list_company_worker_assignments(self, company_id):
            assert company_id == COMPANY_ID
            return [
                {
                    "worker_id": WORKER_ID,
                    "status": "ACTIVE",
                    "approval_required": True,
                    "tool_grants": [],
                    "permission_grants": [],
                }
            ]

        async def create_worker_run(self, **kwargs):
            self.created_runs.append(kwargs)
            return {"id": kwargs["run_id"], "state": "QUEUED"}

    storage = CompanyStorage()
    _patch_storage(storage)
    adapter_factory = AsyncMock(return_value=_Adapter())
    monkeypatch.setattr(main, "_certified_worker_adapter", adapter_factory)

    response = await client.post(
        "/workers/runs",
        json={
            "worker_id": str(WORKER_ID),
            "idempotency_key": "company-assignment-approval-required",
            "task_type": "test.run",
            "project_id": str(PROJECT_ID),
            "dispatch_mode": "queued",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "COMPANY_ASSIGNMENT_APPROVAL_REQUIRED",
        "message": "Worker assignment requires approval before dispatch",
    }
    assert storage.created_runs == []
    assert storage.reservations == []
    assert storage.snapshots == []
    adapter_factory.assert_not_awaited()


@pytest.mark.anyio
async def test_company_dispatch_accepts_active_assignment_without_pending_approval(
    client, monkeypatch
) -> None:
    import orchestrator_api.main as main

    class CompanyStorage(_DispatchStorage):
        def __init__(self):
            super().__init__()
            self.created_runs: list[dict[str, object]] = []

        async def get_project(self, project_id):
            assert project_id == PROJECT_ID
            return {"id": project_id, "company_id": COMPANY_ID}

        async def list_company_worker_assignments(self, company_id):
            assert company_id == COMPANY_ID
            return [
                {
                    "worker_id": WORKER_ID,
                    "status": "ACTIVE",
                    "approval_required": False,
                    "tool_grants": [],
                    "permission_grants": [],
                }
            ]

        async def create_worker_run(self, **kwargs):
            self.created_runs.append(kwargs)
            return {"id": kwargs["run_id"], "state": "QUEUED"}

    storage = CompanyStorage()
    _patch_storage(storage)
    adapter_factory = AsyncMock(return_value=_Adapter())
    monkeypatch.setattr(main, "_certified_worker_adapter", adapter_factory)

    response = await client.post(
        "/workers/runs",
        json={
            "worker_id": str(WORKER_ID),
            "idempotency_key": "company-assignment-approved",
            "task_type": "test.run",
            "project_id": str(PROJECT_ID),
            "dispatch_mode": "queued",
        },
    )

    assert response.status_code == 202
    assert response.json()["state"] == "QUEUED"
    assert response.json()["dispatch_mode"] == "queued"
    assert len(storage.created_runs) == 1
    adapter_factory.assert_awaited_once()


@pytest.mark.anyio
async def test_successful_dispatch_commits_actual_cost_and_releases_concurrency(
    monkeypatch,
) -> None:
    import orchestrator_api.main as main

    import mas_core.worker_contract as worker_contract

    class SuccessfulController:
        def __init__(self, *, storage) -> None:
            self.storage = storage

        async def execute(self, request, _adapter, **_kwargs):
            result = worker_contract.WorkerResult(
                run_id=request.run_id,
                worker_id=request.worker_id,
                success=True,
                usage=worker_contract.WorkerUsage(cost_usd=0.01),
            )
            return worker_contract.WorkerRunOutcome(
                run_id=request.run_id,
                state="SUCCEEDED",
                result=result,
            )

    storage = _DispatchStorage()
    _patch_storage(storage)
    monkeypatch.setattr(worker_contract, "WorkerRunController", SuccessfulController)
    monkeypatch.setattr(main, "_certified_worker_adapter", AsyncMock(return_value=_Adapter()))

    result = await main.dispatch_worker_run(
        main.WorkerRunDispatchRequest(
            worker_id=WORKER_ID,
            idempotency_key="actual-cost",
            task_type="test.run",
            budget_usd=10,
            dispatch_mode="inline",
        )
    )

    assert result["state"] == "SUCCEEDED"
    cost_reservation = next(
        item for item in storage.reservations if item["budget_key"] == "max_cost_usd"
    )
    concurrency_reservation = next(
        item
        for item in storage.reservations
        if item["budget_key"] == "max_concurrent_runs"
    )
    assert {
        "reservation_id": cost_reservation["id"],
        "state": "COMMITTED",
        "amount": Decimal("0.01"),
        "metadata": None,
    } in storage.settlements
    assert {
        "reservation_id": concurrency_reservation["id"],
        "state": "RELEASED",
        "amount": None,
        "metadata": None,
    } in storage.settlements


@pytest.mark.anyio
async def test_failed_dispatch_commits_reported_billed_cost_and_releases_concurrency(
    monkeypatch,
) -> None:
    import orchestrator_api.main as main

    import mas_core.worker_contract as worker_contract

    class FailedController:
        def __init__(self, *, storage) -> None:
            self.storage = storage

        async def execute(self, request, _adapter, **_kwargs):
            result = worker_contract.WorkerResult(
                run_id=request.run_id,
                worker_id=request.worker_id,
                success=False,
                usage=worker_contract.WorkerUsage(cost_usd=0.25),
                error=worker_contract.WorkerError(
                    code="PROVIDER_ERROR",
                    message="provider failed after billing",
                ),
            )
            return worker_contract.WorkerRunOutcome(
                run_id=request.run_id,
                state="FAILED",
                result=result,
            )

    storage = _DispatchStorage()
    _patch_storage(storage)
    monkeypatch.setattr(worker_contract, "WorkerRunController", FailedController)
    monkeypatch.setattr(main, "_certified_worker_adapter", AsyncMock(return_value=_Adapter()))

    result = await main.dispatch_worker_run(
        main.WorkerRunDispatchRequest(
            worker_id=WORKER_ID,
            idempotency_key="failed-billed-cost",
            task_type="test.run",
            budget_usd=10,
            dispatch_mode="inline",
        )
    )

    assert result["state"] == "FAILED"
    cost_reservation = next(
        item for item in storage.reservations if item["budget_key"] == "max_cost_usd"
    )
    concurrency_reservation = next(
        item
        for item in storage.reservations
        if item["budget_key"] == "max_concurrent_runs"
    )
    assert {
        "reservation_id": cost_reservation["id"],
        "state": "COMMITTED",
        "amount": Decimal("0.25"),
        "metadata": {"settlement_reason": "failed_run_billed_usage"},
    } in storage.settlements
    assert {
        "reservation_id": concurrency_reservation["id"],
        "state": "RELEASED",
        "amount": None,
        "metadata": None,
    } in storage.settlements


@pytest.mark.anyio
@pytest.mark.parametrize("fail_at", ["create", "claim", "inline"])
async def test_dispatch_setup_failures_release_all_reservations(
    monkeypatch, fail_at: str
) -> None:
    import orchestrator_api.main as main

    import mas_core.worker_contract as worker_contract

    class FailingController:
        def __init__(self, *, storage) -> None:
            self.storage = storage

        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("inline failed")

    storage = _DispatchStorage(fail_at=fail_at)
    _patch_storage(storage)
    monkeypatch.setattr(worker_contract, "WorkerRunController", FailingController)
    monkeypatch.setattr(main, "_certified_worker_adapter", AsyncMock(return_value=_Adapter()))
    dispatch_mode = "inline" if fail_at == "inline" else "queued"

    with pytest.raises(RuntimeError, match=f"{fail_at} failed"):
        await main.dispatch_worker_run(
            main.WorkerRunDispatchRequest(
                worker_id=WORKER_ID,
                idempotency_key=f"release-{fail_at}",
                task_type="test.run",
                budget_usd=10,
                dispatch_mode=dispatch_mode,
            )
        )

    assert len(storage.reservations) == 2
    assert len(storage.settlements) == 2
    assert {item["state"] for item in storage.settlements} == {"RELEASED"}
    assert {item["reservation_id"] for item in storage.settlements} == {
        item["id"] for item in storage.reservations
    }
