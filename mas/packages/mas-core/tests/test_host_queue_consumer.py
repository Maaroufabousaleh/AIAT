from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from mas_core.worker_contract.adapters import NativeWorkerAdapter
from mas_core.worker_contract.models import WorkerResult, WorkerRunRequest
from mas_core.worker_registry.host_queue_consumer import (
    HostQueueConsumer,
    PinnedWorkerAdapter,
)

if TYPE_CHECKING:
    from mas_core.worker_registry.run_host_binding import RunHostBindingIdentity

RUN_ID = UUID("00000000-0000-4000-a000-000000000301")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000302")
SHELL_ID = UUID("00000000-0000-4000-a000-000000000303")
ADAPTER_ID = UUID("00000000-0000-4000-a000-000000000304")
SKILL_ID = UUID("00000000-0000-4000-a000-000000000305")
STEWARD_ID = UUID("00000000-0000-4000-a000-000000000306")
HOST_ID = "host-local-a"
OWNER = "host-agent-local-a"
PAYLOAD_MARKER = "private-task-payload-must-not-escape"
RESULT_MARKER = "private-worker-result-must-not-escape"


def _binding(**overrides: object) -> dict[str, object]:
    return {
        "id": UUID("00000000-0000-4000-a000-000000000310"),
        "run_id": RUN_ID,
        "worker_id": WORKER_ID,
        "host_uuid": UUID("00000000-0000-4000-a000-000000000311"),
        "host_id": HOST_ID,
        "reservation_id": UUID("00000000-0000-4000-a000-000000000312"),
        "host_lease_generation": 4,
        "current_host_lease_generation": 4,
        "owner": OWNER,
        "host_plane": "worker",
        "host_status": "READY",
        "state": "COMMITTED",
        "reservation_state": "COMMITTED",
        "current_host_lease_valid": True,
        **overrides,
    }


class _MemoryStorage:
    """Small storage double exposing only canonical AgentStorage APIs."""

    def __init__(self, *, run_state: str = "QUEUED") -> None:
        request = WorkerRunRequest(
            run_id=RUN_ID,
            idempotency_key="host-consumer-local-run",
            worker_id=str(WORKER_ID),
            task_type="deterministic-local-task",
            task_input={"prompt": PAYLOAD_MARKER},
            timeout_seconds=10,
        )
        self.run: dict[str, object] = {
            "id": RUN_ID,
            "worker_id": WORKER_ID,
            "idempotency_key": request.idempotency_key,
            "task_type": request.task_type,
            "request_json": request.model_dump(mode="json"),
            "project_id": None,
            "flow_id": None,
            "flow_instance_id": None,
            "flow_node_execution_id": None,
            "worker_shell_version_id": SHELL_ID,
            "adapter_id": ADAPTER_ID,
            "skill_bundle_id": SKILL_ID,
            "steward_id": STEWARD_ID,
            "model_resolution_snapshot_id": None,
            "state": run_state,
            "claim_owner": None,
            "attempt_count": 0,
            "lease_expires_at": None,
            "result_json": None,
        }
        self.worker: dict[str, object] = {
            "id": WORKER_ID,
            "name": "deterministic-local-worker",
            "status": "ACTIVE",
            "evaluation_status": "approved",
        }
        self.shell: dict[str, object] = {
            "id": SHELL_ID,
            "worker_id": WORKER_ID,
            "status": "active",
        }
        self.adapter_record: dict[str, object] = {
            "id": ADAPTER_ID,
            "worker_id": WORKER_ID,
            "status": "active",
            "conformance_status": "passed",
        }
        self.skill_bundle: dict[str, object] = {
            "id": SKILL_ID,
            "worker_id": WORKER_ID,
            "steward_id": STEWARD_ID,
            "status": "APPROVED",
        }
        self.mutation_calls: list[str] = []
        self.events: list[dict[str, object]] = []

    async def get_worker_run(self, run_id: UUID) -> dict[str, object] | None:
        return deepcopy(self.run) if run_id == RUN_ID else None

    async def get_worker(self, worker_id: UUID) -> dict[str, object] | None:
        return deepcopy(self.worker) if worker_id == WORKER_ID else None

    async def get_worker_shell_version(self, shell_version_id: UUID) -> dict[str, object] | None:
        return deepcopy(self.shell) if shell_version_id == SHELL_ID else None

    async def get_runtime_adapter(self, adapter_id: UUID) -> dict[str, object] | None:
        return deepcopy(self.adapter_record) if adapter_id == ADAPTER_ID else None

    async def get_skill_bundle(self, skill_bundle_id: UUID) -> dict[str, object] | None:
        return deepcopy(self.skill_bundle) if skill_bundle_id == SKILL_ID else None

    async def create_worker_run(self, **_values: object) -> dict[str, object]:
        self.mutation_calls.append("create_worker_run")
        return deepcopy(self.run)

    async def claim_worker_run(
        self,
        *,
        owner: str,
        lease_seconds: int,
        run_id: UUID,
    ) -> dict[str, object] | None:
        self.mutation_calls.append("claim_worker_run")
        if run_id != RUN_ID or self.run["state"] != "QUEUED":
            return None
        self.run.update(
            {
                "state": "CLAIMED",
                "claim_owner": owner,
                "attempt_count": int(self.run["attempt_count"]) + 1,
                "lease_expires_at": datetime.now(UTC) + timedelta(seconds=lease_seconds),
            }
        )
        return deepcopy(self.run)

    async def heartbeat_worker_run(
        self,
        run_id: UUID,
        *,
        owner: str,
        attempt_count: int,
        lease_seconds: int,
    ) -> dict[str, object] | None:
        self.mutation_calls.append("heartbeat_worker_run")
        if (
            run_id != RUN_ID
            or self.run.get("claim_owner") != owner
            or self.run.get("attempt_count") != attempt_count
            or self.run.get("state") in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}
        ):
            return None
        self.run["lease_expires_at"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        return deepcopy(self.run)

    async def transition_worker_run(
        self,
        run_id: UUID,
        *,
        new_state: str,
        expected_state: str | None = None,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
        result: dict[str, object] | None = None,
        **values: object,
    ) -> dict[str, object] | None:
        self.mutation_calls.append("transition_worker_run")
        del values
        if run_id != RUN_ID:
            return None
        if expected_state is not None and self.run.get("state") != expected_state:
            return None
        if self.run.get("claim_owner") != claim_owner:
            return None
        if self.run.get("attempt_count") != attempt_count:
            return None
        expires_at = self.run.get("lease_expires_at")
        if not isinstance(expires_at, datetime) or expires_at <= datetime.now(UTC):
            return None
        self.run["state"] = new_state
        if result is not None:
            self.run["result_json"] = deepcopy(result)
        if new_state in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            self.run["claim_owner"] = None
            self.run["lease_expires_at"] = None
        return deepcopy(self.run)

    async def append_worker_event(self, **values: object) -> dict[str, object]:
        self.mutation_calls.append("append_worker_event")
        self.events.append(deepcopy(values))
        return {"id": len(self.events), **values}

    async def create_worker_usage(self, **values: object) -> dict[str, object]:
        self.mutation_calls.append("create_worker_usage")
        return {"id": 1, **values}

    async def persist_worker_result_evidence_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, object]],
        usage: dict[str, object],
    ) -> dict[str, object] | None:
        self.mutation_calls.append("persist_worker_result_evidence_claim_fenced")
        if (
            run_id != RUN_ID
            or self.run.get("state") != "RUNNING"
            or self.run.get("claim_owner") != claim_owner
            or self.run.get("attempt_count") != attempt_count
        ):
            return None
        return {"artifacts": deepcopy(artifacts), "usage": deepcopy(usage)}


class _Bindings:
    def __init__(self, snapshot: dict[str, object], current: dict[str, object] | None = None) -> None:
        self.snapshot = snapshot
        self.current = current or deepcopy(snapshot)
        self.queue_reads: list[tuple[str, int]] = []
        self.get_reads: list[UUID] = []
        self.releases: list[RunHostBindingIdentity] = []

    async def list_queued_committed_for_host(
        self,
        host_id: str,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        self.queue_reads.append((host_id, limit))
        return [deepcopy(self.snapshot)]

    async def get(self, run_id: UUID) -> dict[str, object] | None:
        self.get_reads.append(run_id)
        return deepcopy(self.current)

    async def release(
        self,
        run_id: UUID,
        *,
        owner: str,
        expected_identity: RunHostBindingIdentity,
    ) -> dict[str, object]:
        assert run_id == RUN_ID
        assert owner == OWNER
        self.releases.append(expected_identity)
        self.current = {**self.current, "state": "RELEASED"}
        return deepcopy(self.current)


def _consumer(
    storage: _MemoryStorage,
    bindings: _Bindings,
    adapter_resolver,
) -> HostQueueConsumer:
    return HostQueueConsumer(
        storage,  # type: ignore[arg-type]
        host_id=HOST_ID,
        owner=OWNER,
        adapter_resolver=adapter_resolver,
        binding_service=bindings,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_queue_consumer_executes_through_host_executor_and_controller() -> None:
    storage = _MemoryStorage()
    bindings = _Bindings(_binding())
    worker_calls: list[str] = []
    factory_calls: list[PinnedWorkerAdapter] = []

    async def local_worker(request, _adapter):
        worker_calls.append(str(request.run_id))
        assert request.task_input["prompt"] == PAYLOAD_MARKER
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"private": RESULT_MARKER},
        )

    adapter = NativeWorkerAdapter(local_worker, worker_id=str(WORKER_ID))
    adapter._aiat_adapter_id = str(ADAPTER_ID)

    def resolve_adapter(pins: PinnedWorkerAdapter) -> NativeWorkerAdapter:
        factory_calls.append(pins)
        return adapter

    summaries = await _consumer(storage, bindings, resolve_adapter).consume_once(limit=7)

    assert len(summaries) == 1
    assert summaries[0].run_id == str(RUN_ID)
    assert summaries[0].state == "SUCCEEDED"
    assert summaries[0].reason_code is None
    assert worker_calls == [str(RUN_ID)]
    assert factory_calls == [
        PinnedWorkerAdapter(
            worker_id=WORKER_ID,
            worker_shell_version_id=SHELL_ID,
            adapter_id=ADAPTER_ID,
            skill_bundle_id=SKILL_ID,
            steward_id=STEWARD_ID,
        )
    ]
    assert storage.run["state"] == "SUCCEEDED"
    assert storage.run["attempt_count"] == 1
    assert bindings.queue_reads == [(HOST_ID, 7)]
    assert bindings.get_reads == [RUN_ID, RUN_ID]
    assert len(bindings.releases) == 1
    allowed_mutations = {
        "create_worker_run",
        "claim_worker_run",
        "transition_worker_run",
        "append_worker_event",
        "create_worker_usage",
        "persist_worker_result_evidence_claim_fenced",
    }
    assert set(storage.mutation_calls) <= allowed_mutations
    summary_text = repr(summaries)
    assert PAYLOAD_MARKER not in summary_text
    assert RESULT_MARKER not in summary_text
    await adapter.close()


@pytest.mark.asyncio
async def test_queue_consumer_rejects_stale_binding_before_adapter_resolution() -> None:
    storage = _MemoryStorage()
    current = _binding(id=uuid4(), reservation_id=uuid4())
    bindings = _Bindings(_binding(), current)
    factory_calls: list[PinnedWorkerAdapter] = []

    summaries = await _consumer(storage, bindings, factory_calls.append).consume_once()

    assert len(summaries) == 1
    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "queue_binding_stale"
    assert factory_calls == []
    assert storage.mutation_calls == []


@pytest.mark.asyncio
async def test_queue_consumer_rejects_replayed_nonqueued_run_without_payload() -> None:
    storage = _MemoryStorage()
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []
    adapter = NativeWorkerAdapter(
        lambda request, _adapter: WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"private": RESULT_MARKER},
        ),
        worker_id=str(WORKER_ID),
    )
    adapter._aiat_adapter_id = str(ADAPTER_ID)

    def resolve_adapter(pins: PinnedWorkerAdapter) -> NativeWorkerAdapter:
        factory_calls.append(pins)
        return adapter

    consumer = _consumer(storage, bindings, resolve_adapter)

    first = await consumer.consume_once()
    replay = await consumer.consume_once()

    assert first[0].state == "SUCCEEDED"
    assert replay[0].run_id == str(RUN_ID)
    assert replay[0].state == "SUCCEEDED"
    assert replay[0].reason_code == "worker_run_not_queued"
    assert len(factory_calls) == 1
    summary_text = repr((first, replay))
    assert PAYLOAD_MARKER not in summary_text
    assert RESULT_MARKER not in summary_text
    await adapter.close()


@pytest.mark.asyncio
async def test_queue_consumer_rejects_mismatched_persisted_worker_request() -> None:
    storage = _MemoryStorage()
    corrupted_request = dict(storage.run["request_json"])
    corrupted_request["task_type"] = "different-task-type"
    storage.run["request_json"] = corrupted_request
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []

    summaries = await _consumer(storage, bindings, factory_calls.append).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "worker_request_task_type_mismatch"
    assert factory_calls == []


@pytest.mark.asyncio
async def test_queue_consumer_rejects_deactivated_worker_before_adapter_resolution() -> None:
    storage = _MemoryStorage()
    storage.worker["status"] = "INACTIVE"
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []

    summaries = await _consumer(storage, bindings, factory_calls.append).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "worker_not_active"
    assert factory_calls == []
    assert storage.mutation_calls == []


@pytest.mark.asyncio
async def test_queue_consumer_rejects_unapproved_pinned_adapter() -> None:
    storage = _MemoryStorage()
    storage.adapter_record["conformance_status"] = "pending"
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []

    summaries = await _consumer(storage, bindings, factory_calls.append).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "worker_adapter_pin_not_eligible"
    assert factory_calls == []
    assert storage.mutation_calls == []


@pytest.mark.asyncio
async def test_queue_consumer_rejects_factory_adapter_with_different_pin() -> None:
    storage = _MemoryStorage()
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []
    wrong_adapter = NativeWorkerAdapter(
        lambda request, _adapter: WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
        ),
        worker_id=str(WORKER_ID),
    )
    wrong_adapter._aiat_adapter_id = str(uuid4())

    def resolve_wrong_adapter(pins: PinnedWorkerAdapter) -> NativeWorkerAdapter:
        factory_calls.append(pins)
        return wrong_adapter

    summaries = await _consumer(storage, bindings, resolve_wrong_adapter).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "pinned_adapter_resolution_mismatch"
    assert len(factory_calls) == 1
    assert storage.mutation_calls == []
    await wrong_adapter.close()
