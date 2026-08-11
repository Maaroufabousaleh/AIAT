"""Company grant and budget-settlement coverage for worker dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

WORKER_ID = UUID("00000000-0000-4000-a000-000000000701")
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000702")
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000703")


def test_model_override_expiry_normalizes_serialized_values_and_fails_closed() -> None:
    import orchestrator_api.main as main

    reference = datetime(2026, 1, 1, tzinfo=UTC)
    assert main._model_override_is_expired(None, now=reference) is False
    assert main._model_override_is_expired(reference - timedelta(seconds=1), now=reference) is True
    assert main._model_override_is_expired("2025-12-31T23:59:59Z", now=reference) is True
    assert main._model_override_is_expired("2026-01-01T00:00:01+00:00", now=reference) is False
    assert main._model_override_is_expired("not-a-timestamp", now=reference) is True
    assert main._model_override_is_expired(object(), now=reference) is True


class _Adapter:
    def __init__(self) -> None:
        from mas_core.worker_contract import WorkerCapabilities

        self.capabilities = WorkerCapabilities()


class _DispatchStorage:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.reservations: list[dict[str, object]] = []
        self.settlements: list[dict[str, object]] = []

    async def get_worker(self, worker_id: UUID):
        if worker_id != WORKER_ID:
            return None
        return {
            "id": WORKER_ID,
            "status": "ACTIVE",
            "model_mode": "none",
            "adapter_config": {},
        }

    async def create_model_resolution_snapshot(self, **_kwargs):
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


def _patch_storage(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


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
