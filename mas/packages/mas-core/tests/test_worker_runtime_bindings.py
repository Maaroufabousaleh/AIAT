"""Durable subordinate-runtime binding coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.worker_contract.adapters import BaseWorkerAdapter
from mas_core.worker_contract.controller import WorkerRunController
from mas_core.worker_contract.models import WorkerResult, WorkerRunRequest, WorkerRuntimeStatus


def _request() -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key=f"runtime-binding-{uuid4()}",
        worker_id="runtime-binding-worker",
        task_type="runtime-binding-test",
    )


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _SuccessfulBoundAdapter(BaseWorkerAdapter):
    runtime_type = "test-runtime"
    runtime_version = "test-runtime-1"

    def _acceptance_runtime_run_id(self, request: WorkerRunRequest) -> str:
        return f"external-{request.run_id}"

    async def _execute(self, request: WorkerRunRequest) -> WorkerResult:
        return WorkerResult(
            run_id=request.run_id,
            worker_id=self.worker_id,
            success=True,
            output={"ok": True},
        )


@pytest.mark.asyncio
async def test_memory_controller_retains_runtime_binding_across_terminal_settlement() -> None:
    request = _request()
    adapter = _SuccessfulBoundAdapter(worker_id=request.worker_id)
    controller = WorkerRunController()

    outcome = await controller.execute(request, adapter)

    assert outcome.state == "SUCCEEDED"
    row = await controller.get_run(request.run_id)
    assert row is not None
    assert row["runtime_run_id"] == f"external-{request.run_id}"
    assert row["runtime_binding"]["runtime_type"] == "test-runtime"
    assert row["runtime_binding"]["status"] == "SUCCEEDED"
    reconciled = await adapter.reconcile(request.run_id)
    assert reconciled.status == "SUCCEEDED"
    await adapter.close()


@pytest.mark.asyncio
async def test_reconcile_prefers_durable_runtime_binding_before_legacy_transition_metadata() -> None:
    request = _request()
    observed_runtime_ids: list[str | None] = []

    class Storage:
        async def get_worker_run(self, run_id):
            return {"id": run_id, "state": "RUNNING"}

        async def get_worker_runtime_binding(self, run_id):
            assert run_id == request.run_id
            return {
                "run_id": run_id,
                "attempt_count": 2,
                "runtime_run_id": "durable-runtime-2",
            }

        async def list_worker_run_transitions(self, _run_id):
            raise AssertionError("legacy transition scan should not be needed")

    class Adapter:
        async def reconcile(self, run_id, *, runtime_run_id=None):
            observed_runtime_ids.append(runtime_run_id)
            return WorkerRuntimeStatus(
                run_id=run_id,
                status="RUNNING",
                runtime_run_id=runtime_run_id,
            )

    observed = await WorkerRunController(storage=Storage()).reconcile(
        request.run_id,
        Adapter(),  # type: ignore[arg-type]
    )

    assert observed.status == "RUNNING"
    assert observed.runtime_run_id == "durable-runtime-2"
    assert observed_runtime_ids == ["durable-runtime-2"]


@pytest.mark.asyncio
async def test_reconcile_persists_observation_without_mutating_canonical_run_state() -> None:
    request = _request()
    observations: list[dict[str, object]] = []

    class Storage:
        async def get_worker_run(self, run_id):
            return {"id": run_id, "state": "RUNNING"}

        async def get_worker_runtime_binding(self, run_id):
            return {
                "run_id": run_id,
                "attempt_count": 1,
                "runtime_run_id": "durable-runtime-1",
            }

        async def record_worker_runtime_observation(self, run_id, *, status, runtime_run_id=None):
            observations.append(
                {
                    "run_id": run_id,
                    "status": status,
                    "runtime_run_id": runtime_run_id,
                }
            )

        async def transition_worker_run(self, *args, **kwargs):
            raise AssertionError("reconciliation must not transition the canonical run")

    class Adapter:
        async def reconcile(self, run_id, *, runtime_run_id=None):
            return WorkerRuntimeStatus(
                run_id=run_id,
                status="RUNNING",
                runtime_run_id=runtime_run_id,
            )

    observed = await WorkerRunController(storage=Storage()).reconcile(
        request.run_id,
        Adapter(),  # type: ignore[arg-type]
    )

    assert observed.status == "RUNNING"
    assert observations == [
        {
            "run_id": request.run_id,
            "status": "RUNNING",
            "runtime_run_id": "durable-runtime-1",
        }
    ]


@pytest.mark.asyncio
async def test_storage_records_runtime_observation_on_latest_attempt_only() -> None:
    run_id = uuid4()
    binding_id = uuid4()
    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _Result(
                [
                    {
                        "id": binding_id,
                        "run_id": run_id,
                        "attempt_count": 3,
                        "runtime_run_id": "external-3",
                        "status": "RUNNING",
                    }
                ]
            ),
            MagicMock(rowcount=1),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    from mas_core.memory.storage import AgentStorage

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    updated = await storage.record_worker_runtime_observation(
        run_id,
        status="failed",
        runtime_run_id="external-3",
    )

    assert updated is not None
    assert updated["status"] == "FAILED"
    assert updated["runtime_run_id"] == "external-3"
    assert updated["last_reconciled_at"] is not None
    update_statement = connection.execute.await_args_list[1].args[0]
    params = update_statement.compile().params
    assert params["status"] == "FAILED"
    assert params["runtime_run_id"] == "external-3"


def test_runtime_binding_schema_and_migration_are_aligned() -> None:
    from pathlib import Path

    mas_root = Path(__file__).resolve().parents[3]
    models = (
        mas_root / "packages" / "mas-core" / "mas_core" / "memory" / "models.py"
    ).read_text(encoding="utf-8")
    migration = (
        mas_root / "migrations" / "versions" / "0044_worker_run_runtime_bindings.py"
    ).read_text(encoding="utf-8")

    assert '"worker_run_runtime_bindings"' in models
    assert 'revision = "0044_worker_run_runtime_bindings"' in migration
    assert 'down_revision = "0043_project_transition_outbox"' in migration
    for field in ("attempt_count", "runtime_type", "runtime_run_id", "status", "last_reconciled_at"):
        assert f'"{field}"' in migration
