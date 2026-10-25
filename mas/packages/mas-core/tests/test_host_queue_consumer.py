from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import anyio
import pytest

import mas_core.worker_registry.local_host_queue_runtime as local_queue_module
from mas_core.worker_contract.adapters import AdapterContext, NativeWorkerAdapter
from mas_core.worker_contract.models import (
    WorkerResult,
    WorkerRunRequest,
    WorkerToolResponse,
)
from mas_core.worker_registry.deterministic_file_worker import generate_project_file
from mas_core.worker_registry.host_queue_consumer import (
    HostQueueConsumer,
    PinnedWorkerAdapter,
)
from mas_core.worker_registry.local_host_queue_runtime import (
    LocalHostQueueConfig,
    LocalHostQueueRuntime,
)
from mas_core.worker_registry.runtime_adapters import ProcessAdapter

if TYPE_CHECKING:
    from mas_core.worker_registry.run_host_binding import (
        RunHostBindingIdentity,
        RunHostBindingRequest,
    )

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
            "transport_type": "native",
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

    async def list_worker_runs(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
        **_filters: object,
    ) -> list[dict[str, object]]:
        if offset > 0 or limit < 1:
            return []
        if state is not None and self.run.get("state") != state:
            return []
        return [deepcopy(self.run)]

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


class _RuntimeBindings(_Bindings):
    def __init__(self, storage: _MemoryStorage) -> None:
        super().__init__({})
        self.storage = storage
        self.current = None
        self.host_id: str | None = None
        self.owner: str | None = None
        self.placement_requests: list[RunHostBindingRequest] = []

    async def get(self, run_id: UUID) -> dict[str, object] | None:
        assert run_id == RUN_ID
        return deepcopy(self.current)

    async def assign(self, request: RunHostBindingRequest) -> dict[str, object]:
        self.placement_requests.append(request)
        labels = dict(request.placement.required_labels)
        self.host_id = labels["aiat.local_executor_host"]
        self.owner = request.owner
        self.current = _binding(
            host_id=self.host_id,
            owner=self.owner,
            state="ASSIGNED",
            reservation_state="RESERVED",
        )
        return deepcopy(self.current)

    async def commit(self, run_id: UUID, *, owner: str) -> dict[str, object]:
        assert run_id == RUN_ID
        assert owner == self.owner
        self.current = {
            **self.current,
            "state": "COMMITTED",
            "reservation_state": "COMMITTED",
            "current_host_lease_valid": True,
        }
        self.snapshot = deepcopy(self.current)
        return deepcopy(self.current)

    async def list_queued_committed_for_host(
        self,
        host_id: str,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        assert limit >= 1
        if (
            host_id == self.host_id
            and self.current is not None
            and self.current.get("state") == "COMMITTED"
            and self.current.get("reservation_state") == "COMMITTED"
            and self.storage.run.get("state") == "QUEUED"
        ):
            return [deepcopy(self.current)]
        return []

    async def release(self, run_id: UUID, *, owner: str, expected_identity):
        assert run_id == RUN_ID
        assert owner == self.owner
        self.releases.append(expected_identity)
        self.current = {
            **self.current,
            "state": "RELEASED",
            "reservation_state": "RELEASED",
        }
        return deepcopy(self.current)


class _RuntimeHostRegistry:
    def __init__(self, _storage: object) -> None:
        self.calls: list[str] = []
        self.host_id: str | None = None
        self.token: str | None = None
        self.fail_heartbeat = False

    async def register_host(self, **values: object) -> dict[str, object]:
        self.host_id = str(values["host_id"])
        self.token = str(values["registration_token"])
        self.calls.append("register")
        assert values["host_plane"] == "worker"
        assert values["sandbox_profile"] == "sandboxed"
        assert values["sandbox_runtime"] == "runsc"
        assert values["labels"] == {"aiat.local_executor_host": self.host_id}
        return {"host_id": self.host_id, "lease_generation": 1, "status": "REGISTERING"}

    async def heartbeat(self, **values: object) -> dict[str, object]:
        assert values["host_id"] == self.host_id
        assert values["registration_token"] == self.token
        self.calls.append("heartbeat")
        if self.fail_heartbeat:
            raise RuntimeError("host readiness unavailable")
        return {"host_id": self.host_id, "lease_generation": 1, "status": "READY"}

    async def set_status(self, **values: object) -> dict[str, object]:
        assert values["host_id"] == self.host_id
        assert values["registration_token"] == self.token
        assert values["status"] == "OFFLINE"
        self.calls.append("offline")
        return {"host_id": self.host_id, "status": "OFFLINE"}


PROJECT_ID = UUID("00000000-0000-4000-a000-000000000320")


class _DeterministicVerticalStorage(_MemoryStorage):
    """Offline AgentStorage contract double for the full mediated worker path."""

    def __init__(self) -> None:
        super().__init__()
        request = WorkerRunRequest(
            run_id=RUN_ID,
            idempotency_key="deterministic-file-vertical-run",
            worker_id=str(WORKER_ID),
            task_type="deterministic_file_generation",
            task_input={"path": "src/vertical.py", "content": "VALUE = 7\n"},
            project_id=PROJECT_ID,
            tool_grants=["file_write"],
            timeout_seconds=10,
        )
        self.run.update(
            {
                "idempotency_key": request.idempotency_key,
                "task_type": request.task_type,
                "request_json": request.model_dump(mode="json"),
                "project_id": PROJECT_ID,
            }
        )
        self.worker.update(
            {
                "name": "aiat-deterministic-file-worker",
                "source_repo": "https://example.invalid/aiat-worker",
                "source_revision": "fixture-revision",
                "version_pin": "0.1.1+sha256.fixture",
                "adapter_type": "oci",
                "sandbox_profile": "sandboxed",
                "model_mode": "none",
                "tool_grants": ["file_write"],
                "adapter_config": {
                    "tool_grants": ["file_write"],
                    "execution_policy": {
                        "trust": "untrusted",
                        "formats": ["oci", "linux"],
                        "minimum_isolation": "sandboxed",
                    },
                },
            }
        )
        self.adapter_record["transport_type"] = "oci"
        self.effects: dict[tuple[UUID, str], dict[str, object]] = {}
        self.project_files: dict[tuple[UUID, str], bytes] = {}
        self.terminal_evidence: dict[str, object] | None = None

    async def record_project_usage(self, **_values: object) -> dict[str, object]:
        """Expose the project-usage capability expected by the controller.

        A claimed run must pass the project-scoped row to atomic completion;
        it must not fall back to this standalone write method.
        """
        raise AssertionError("claimed usage must be persisted atomically")

    def _claim_is_current(self, claim_owner: str | None, attempt_count: int) -> bool:
        expires_at = self.run.get("lease_expires_at")
        return (
            self.run.get("state") == "RUNNING"
            and self.run.get("claim_owner") == claim_owner
            and self.run.get("attempt_count") == attempt_count
            and isinstance(expires_at, datetime)
            and expires_at > datetime.now(UTC)
            and self.run.get("cancel_requested_at") is None
        )

    async def begin_worker_tool_effect(self, **values: object) -> dict[str, object]:
        self.mutation_calls.append("begin_worker_tool_effect")
        claim_owner = values.get("claim_owner")
        attempt_count = int(values.get("attempt_count") or 0)
        if (
            values.get("run_id") != self.run.get("id")
            or not self._claim_is_current(claim_owner, attempt_count)
        ):
            return {"disposition": "CLAIM_LOST"}
        key = (values["run_id"], str(values["idempotency_key"]))
        existing = self.effects.get(key)
        if existing is not None:
            if existing["request_sha256"] != values["request_sha256"]:
                disposition = "CONFLICT"
            elif existing["state"] == "COMPLETED":
                disposition = "REPLAY"
            else:
                disposition = "AMBIGUOUS"
            return {**deepcopy(existing), "disposition": disposition}
        row: dict[str, object] = {
            "id": uuid4(),
            **values,
            "state": "IN_FLIGHT",
            "response_json": None,
        }
        self.effects[key] = row
        return {**deepcopy(row), "disposition": "NEW"}

    async def complete_worker_tool_effect(
        self,
        effect_id: UUID,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        state: str,
        response_json: dict[str, object],
    ) -> dict[str, object] | None:
        self.mutation_calls.append("complete_worker_tool_effect")
        if state != "COMPLETED" or not self._claim_is_current(claim_owner, attempt_count):
            return None
        row = next(
            (
                value
                for value in self.effects.values()
                if value.get("id") == effect_id and value.get("run_id") == run_id
            ),
            None,
        )
        if row is None or row.get("state") != "IN_FLIGHT":
            return None
        row.update({"state": state, "response_json": deepcopy(response_json)})
        return deepcopy(row)

    async def complete_worker_run_success_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, object]],
        usage: dict[str, object],
        result: dict[str, object],
        project_usage: dict[str, object] | None = None,
        **_values: object,
    ) -> dict[str, object] | None:
        self.mutation_calls.append("complete_worker_run_success_claim_fenced")
        if run_id != RUN_ID or not self._claim_is_current(claim_owner, attempt_count):
            return None
        if any(
            row.get("state") in {"IN_FLIGHT", "AMBIGUOUS"}
            for row in self.effects.values()
            if row.get("run_id") == run_id
        ):
            return None
        # One in-memory operation models the production transaction that
        # commits artifact links, usage, and terminal success together.
        self.terminal_evidence = {
            "run_id": run_id,
            "project_id": PROJECT_ID,
            "artifacts": deepcopy(artifacts),
            "usage": deepcopy(usage),
            "project_usage": deepcopy(project_usage),
        }
        self.run.update(
            {
                "state": "SUCCEEDED",
                "result_json": deepcopy(result),
                "claim_owner": None,
                "lease_expires_at": None,
            }
        )
        return deepcopy(self.run)


class _FakeOciDeterministicAdapter(NativeWorkerAdapter):
    """A fake OCI transport driving the real deterministic worker function."""

    runtime_type = "oci"


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
async def test_queue_consumer_rejects_direct_process_for_hardened_worker() -> None:
    storage = _MemoryStorage()
    storage.worker.update(
        {
            "sandbox_profile": "sandboxed",
            "adapter_config": {
                "execution_policy": {
                    "trust": "untrusted",
                    "formats": ["oci", "linux"],
                    "minimum_isolation": "sandboxed",
                }
            },
        }
    )
    storage.adapter_record["transport_type"] = "process"
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []

    summaries = await _consumer(storage, bindings, factory_calls.append).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "worker_sandbox_process_transport_unsupported"
    assert factory_calls == []
    assert storage.mutation_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pinned_transport", "resolved_transport"),
    [
        ("oci", "process"),
        ("oci", "firecracker"),
        ("unrecognized-runtime", "unrecognized-runtime"),
    ],
)
async def test_queue_consumer_rejects_resolved_transport_mismatch_before_claim(
    pinned_transport: str,
    resolved_transport: str,
) -> None:
    storage = _MemoryStorage()
    storage.worker.update(
        {
            "source_repo": "https://example.invalid/worker",
            "adapter_type": pinned_transport,
            "sandbox_profile": "sandboxed",
            "adapter_config": {
                "execution_policy": {
                    "trust": "untrusted",
                    "formats": ["oci", "linux"],
                    "minimum_isolation": "sandboxed",
                }
            },
        }
    )
    storage.adapter_record["transport_type"] = pinned_transport
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []
    if resolved_transport == "process":
        # This process adapter's executable is intentionally absent. A
        # mismatched resolver result must be rejected before the host executor
        # claims or attempts to launch it.
        adapter = ProcessAdapter(
            ["aiat-test-worker-executable-must-not-run"],
            worker_id=str(WORKER_ID),
        )
        adapter._aiat_adapter_id = str(ADAPTER_ID)
    else:
        # Firecracker is separately pinned compatibility transport, and an
        # unknown runtime never qualifies as a validated OCI/Kata adapter.
        adapter = type(
            "MismatchedTransport",
            (),
            {"runtime_type": resolved_transport, "_aiat_adapter_id": str(ADAPTER_ID)},
        )()

    def resolve_adapter(pins: PinnedWorkerAdapter) -> object:
        factory_calls.append(pins)
        return adapter

    summaries = await _consumer(storage, bindings, resolve_adapter).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "pinned_adapter_transport_mismatch"
    assert len(factory_calls) == 1
    assert storage.run["state"] == "QUEUED"
    assert storage.mutation_calls == []
    if isinstance(adapter, ProcessAdapter):
        assert not adapter._processes
        await adapter.close()


@pytest.mark.asyncio
async def test_queue_consumer_requires_transport_from_pinned_adapter_record() -> None:
    storage = _MemoryStorage()
    storage.worker["adapter_type"] = "oci"
    storage.adapter_record.pop("transport_type")
    bindings = _Bindings(_binding())
    resolver_calls: list[PinnedWorkerAdapter] = []

    summaries = await _consumer(
        storage,
        bindings,
        lambda pins: resolver_calls.append(pins),
    ).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "worker_adapter_transport_missing"
    assert resolver_calls == []
    assert storage.run["state"] == "QUEUED"
    assert storage.mutation_calls == []


@pytest.mark.asyncio
async def test_queue_consumer_treats_source_backed_process_worker_as_external() -> None:
    storage = _MemoryStorage()
    storage.worker.update(
        {
            "source_repo": "https://example.invalid/worker",
            "sandbox_profile": "trusted",
            "adapter_config": {"execution_policy": {"formats": ["linux"]}},
        }
    )
    storage.adapter_record["transport_type"] = "process"
    bindings = _Bindings(_binding())
    factory_calls: list[PinnedWorkerAdapter] = []

    summaries = await _consumer(storage, bindings, factory_calls.append).consume_once()

    assert summaries[0].state == "QUEUED"
    assert summaries[0].reason_code == "worker_sandbox_process_transport_unsupported"
    assert factory_calls == []
    assert storage.mutation_calls == []


@pytest.mark.asyncio
async def test_queue_consumer_allows_trusted_internal_process_worker() -> None:
    storage = _MemoryStorage()
    storage.worker.update(
        {
            "sandbox_profile": "trusted",
            "adapter_config": {
                "execution_policy": {
                    "trust": "trusted",
                    "minimum_isolation": "trusted",
                }
            },
        }
    )
    storage.adapter_record["transport_type"] = "process"
    bindings = _Bindings(_binding())
    worker_calls: list[str] = []

    async def process_runner(request, _adapter):
        worker_calls.append(str(request.run_id))
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"result": "trusted internal process fixture"},
        )

    adapter = ProcessAdapter(
        ["unused-fixture-command"],
        worker_id=str(WORKER_ID),
        runner=process_runner,
    )
    adapter._aiat_adapter_id = str(ADAPTER_ID)

    summaries = await _consumer(
        storage,
        bindings,
        lambda _pins: adapter,
    ).consume_once()

    assert summaries[0].state == "SUCCEEDED"
    assert summaries[0].reason_code is None
    assert worker_calls == [str(RUN_ID)]
    await adapter.close()


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


def test_local_host_queue_configuration_is_explicit_and_development_only() -> None:
    base = {
        "AIAT_LOCAL_HOST_QUEUE_ENABLED": "true",
        "MAS_ENVIRONMENT": "test",
        "AIAT_LOCAL_WORKER_HOST_ID": "alpha-local",
        "AIAT_LOCAL_WORKER_HOST_TOKEN": "local-host-test-token",
        "AIAT_LOCAL_WORKER_HOST_SANDBOX_PROFILE": "sandboxed",
        "AIAT_LOCAL_WORKER_HOST_SANDBOX_RUNTIME": "runsc",
    }
    config = LocalHostQueueConfig.from_environment(base)
    assert config is not None
    assert config.host_id_prefix == "alpha-local"
    assert config.sandbox_profile == "sandboxed"
    assert config.sandbox_runtime == "runsc"
    assert LocalHostQueueConfig.from_environment({}) is None

    with pytest.raises(ValueError, match="development/test"):
        LocalHostQueueConfig.from_environment({**base, "MAS_ENVIRONMENT": "production"})
    with pytest.raises(ValueError, match="sandboxed/runsc"):
        LocalHostQueueConfig.from_environment(
            {**base, "AIAT_LOCAL_WORKER_HOST_SANDBOX_RUNTIME": "runc"}
        )


@pytest.mark.asyncio
async def test_running_local_queue_places_then_consumes_through_real_executor_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _MemoryStorage()
    storage.worker.update(
        {
            "sandbox_profile": "sandboxed",
            "adapter_config": {"execution_policy": {}},
        }
    )
    config = LocalHostQueueConfig(
        host_id_prefix="alpha-local",
        registration_token="local-host-test-token",
        sandbox_profile="sandboxed",
        sandbox_runtime="runsc",
        poll_seconds=0.01,
    )
    host_registries: list[_RuntimeHostRegistry] = []
    binding_services: list[_RuntimeBindings] = []

    def registry_factory(value: object) -> _RuntimeHostRegistry:
        registry = _RuntimeHostRegistry(value)
        host_registries.append(registry)
        return registry

    def binding_factory(_value: object) -> _RuntimeBindings:
        bindings = _RuntimeBindings(storage)
        binding_services.append(bindings)
        return bindings

    monkeypatch.setattr(local_queue_module, "WorkerHostRegistry", registry_factory)
    monkeypatch.setattr(local_queue_module, "WorkerRunHostBindingService", binding_factory)

    worker_calls: list[str] = []

    async def local_worker(request, _adapter):
        worker_calls.append(str(request.run_id))
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"artifact": "generated by deterministic local worker"},
        )

    adapter = NativeWorkerAdapter(local_worker, worker_id=str(WORKER_ID))
    adapter._aiat_adapter_id = str(ADAPTER_ID)
    terminal_summaries = []

    async def record_terminal(summary) -> None:
        terminal_summaries.append(summary)

    runtime = LocalHostQueueRuntime(
        storage,  # type: ignore[arg-type]
        config,
        adapter_resolver=lambda _pins: adapter,
        terminal_run_handler=record_terminal,
    )
    await runtime.start()
    assert runtime.active

    stop_event = anyio.Event()
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(runtime.run, stop_event)
        try:
            with anyio.fail_after(2):
                while storage.run["state"] != "SUCCEEDED":
                    await anyio.sleep(0.01)
        finally:
            stop_event.set()

    assert storage.run["state"] == "SUCCEEDED"
    assert storage.run["attempt_count"] == 1
    assert worker_calls == [str(RUN_ID)]
    assert len(binding_services) == 1
    assert len(binding_services[0].placement_requests) == 1
    assert binding_services[0].placement_requests[0].owner == f"aiat-local-host:{runtime.host_id}"
    assert binding_services[0].placement_requests[0].placement.required_labels == (
        ("aiat.local_executor_host", runtime.host_id),
    )
    assert binding_services[0].current["state"] == "RELEASED"
    assert [summary.state for summary in terminal_summaries] == ["SUCCEEDED"]
    assert host_registries[0].calls == ["register", "heartbeat", "offline"]
    assert runtime.public_status()["status"] == "STOPPED"
    assert PAYLOAD_MARKER not in repr(runtime.public_status())
    assert RESULT_MARKER not in repr(runtime.public_status())
    await adapter.close()


@pytest.mark.asyncio
async def test_deterministic_file_worker_vertical_claim_tool_artifact_and_terminal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _DeterministicVerticalStorage()
    config = LocalHostQueueConfig(
        host_id_prefix="alpha-local",
        registration_token="local-host-test-token",
        sandbox_profile="sandboxed",
        sandbox_runtime="runsc",
        poll_seconds=0.01,
    )
    host_registries: list[_RuntimeHostRegistry] = []
    binding_services: list[_RuntimeBindings] = []

    def registry_factory(value: object) -> _RuntimeHostRegistry:
        registry = _RuntimeHostRegistry(value)
        host_registries.append(registry)
        return registry

    def binding_factory(_value: object) -> _RuntimeBindings:
        bindings = _RuntimeBindings(storage)
        binding_services.append(bindings)
        return bindings

    monkeypatch.setattr(local_queue_module, "WorkerHostRegistry", registry_factory)
    monkeypatch.setattr(local_queue_module, "WorkerRunHostBindingService", binding_factory)

    tool_calls: list[object] = []
    worker_calls: list[str] = []
    resolved_pins: list[PinnedWorkerAdapter] = []
    artifact_uploads: list[dict[str, object]] = []
    content = b"VALUE = 7\n"

    async def tool_service(tool_request) -> WorkerToolResponse:
        tool_calls.append(tool_request)
        claim_fence = tool_request.execution_claim_fence
        assert storage.run["state"] == "RUNNING"
        assert claim_fence["claim_owner"] == storage.run["claim_owner"]
        assert claim_fence["attempt_count"] == storage.run["attempt_count"]
        assert tool_request.run_id == RUN_ID
        assert tool_request.tool_name == "file_write"
        assert tool_request.permission_scope == ["file_write"]
        assert tool_request.approval_required is False
        assert storage.run["project_id"] == PROJECT_ID
        assert "project_id" not in tool_request.arguments
        path = tool_request.arguments["path"]
        payload = tool_request.arguments["content"].encode("utf-8")
        storage.project_files[(PROJECT_ID, path)] = payload
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={
                "success": True,
                "project_id": str(PROJECT_ID),
                "path": path,
                "bytes_written": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    async def register_artifact(artifact, *, claim_fence, cleanup=False) -> None:
        if cleanup:
            return
        assert claim_fence["claim_owner"] == storage.run["claim_owner"]
        assert claim_fence["attempt_count"] == storage.run["attempt_count"]
        project_id = UUID(artifact.metadata["project_id"])
        run_id = UUID(artifact.metadata["worker_run_id"])
        stored_content = storage.project_files[(project_id, artifact.uri)]
        assert run_id == RUN_ID
        assert project_id == PROJECT_ID
        assert hashlib.sha256(stored_content).hexdigest() == artifact.sha256
        artifact_uploads.append(
            {"run_id": run_id, "project_id": project_id, "uri": artifact.uri, "sha256": artifact.sha256}
        )

    async def deterministic_worker(request, adapter):
        worker_calls.append(str(request.run_id))
        return await generate_project_file(request, adapter)

    adapter = _FakeOciDeterministicAdapter(
        deterministic_worker,
        worker_id=str(WORKER_ID),
        context=AdapterContext(
            tool_dispatcher=tool_service,
            artifact_registrar=register_artifact,
        ),
    )
    adapter._aiat_adapter_id = str(ADAPTER_ID)
    terminal_summaries = []

    async def record_terminal(summary) -> None:
        terminal_summaries.append(summary)

    runtime = LocalHostQueueRuntime(
        storage,  # type: ignore[arg-type]
        config,
        adapter_resolver=lambda pins: (resolved_pins.append(pins) or adapter),
        terminal_run_handler=record_terminal,
    )
    await runtime.start()
    stop_event = anyio.Event()
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(runtime.run, stop_event)
        try:
            with anyio.fail_after(2):
                while storage.run["state"] != "SUCCEEDED":
                    await anyio.sleep(0.01)
        finally:
            stop_event.set()

    effect_rows = list(storage.effects.values())
    assert storage.run["state"] == "SUCCEEDED"
    assert storage.run["project_id"] == PROJECT_ID
    assert resolved_pins == [
        PinnedWorkerAdapter(
            worker_id=WORKER_ID,
            worker_shell_version_id=SHELL_ID,
            adapter_id=ADAPTER_ID,
            skill_bundle_id=SKILL_ID,
            steward_id=STEWARD_ID,
        )
    ]
    assert worker_calls == [str(RUN_ID)]
    assert len(tool_calls) == 1
    assert len(effect_rows) == 1
    assert effect_rows[0]["run_id"] == RUN_ID
    assert effect_rows[0]["state"] == "COMPLETED"
    assert effect_rows[0]["response_json"]["success"] is True
    assert storage.project_files[(PROJECT_ID, "src/vertical.py")] == content
    assert len(artifact_uploads) == 1
    assert artifact_uploads[0]["run_id"] == RUN_ID
    assert artifact_uploads[0]["project_id"] == PROJECT_ID
    assert storage.terminal_evidence is not None
    assert storage.terminal_evidence["run_id"] == RUN_ID
    assert storage.terminal_evidence["project_id"] == PROJECT_ID
    project_usage = storage.terminal_evidence["project_usage"]
    assert project_usage["project_id"] == PROJECT_ID
    assert project_usage["run_id"] == RUN_ID
    assert project_usage["worker_id"] == WORKER_ID
    artifact_rows = storage.terminal_evidence["artifacts"]
    assert len(artifact_rows) == 1
    assert artifact_rows[0]["metadata"]["worker_run_id"] == str(RUN_ID)
    assert artifact_rows[0]["metadata"]["project_id"] == str(PROJECT_ID)
    assert artifact_rows[0]["metadata"]["tool_effect_id"] == str(effect_rows[0]["id"])
    assert storage.terminal_evidence["usage"]["total_tokens"] == 0
    assert [summary.state for summary in terminal_summaries] == ["SUCCEEDED"]
    assert binding_services[0].current["state"] == "RELEASED"
    placement = binding_services[0].placement_requests[0].placement
    assert placement.required_sandbox_profile == "sandboxed"
    assert placement.execution_policy == storage.worker["adapter_config"]["execution_policy"]
    assert host_registries[0].calls == ["register", "heartbeat", "offline"]
    await adapter.close()


@pytest.mark.asyncio
async def test_local_host_startup_fences_registered_host_when_readiness_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _MemoryStorage()
    registry = _RuntimeHostRegistry(storage)
    registry.fail_heartbeat = True
    monkeypatch.setattr(local_queue_module, "WorkerHostRegistry", lambda _storage: registry)
    config = LocalHostQueueConfig(
        host_id_prefix="alpha-local",
        registration_token="local-host-test-token",
        sandbox_profile="sandboxed",
        sandbox_runtime="runsc",
    )
    runtime = LocalHostQueueRuntime(
        storage,  # type: ignore[arg-type]
        config,
        adapter_resolver=lambda _pins: None,
    )

    with pytest.raises(RuntimeError, match="host readiness unavailable"):
        await runtime.start()

    assert registry.calls == ["register", "heartbeat", "offline"]
    assert not runtime.active
    assert runtime.public_status()["status"] == "FAILED"


@pytest.mark.asyncio
async def test_local_host_stop_fences_its_generation_before_storage_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _MemoryStorage()
    registry = _RuntimeHostRegistry(storage)
    monkeypatch.setattr(local_queue_module, "WorkerHostRegistry", lambda _storage: registry)
    config = LocalHostQueueConfig(
        host_id_prefix="alpha-local",
        registration_token="local-host-test-token",
        sandbox_profile="sandboxed",
        sandbox_runtime="runsc",
    )
    runtime = LocalHostQueueRuntime(
        storage,  # type: ignore[arg-type]
        config,
        adapter_resolver=lambda _pins: None,
    )

    await runtime.start()
    await runtime.stop()
    await runtime.stop()

    assert registry.calls == ["register", "heartbeat", "offline"]
    assert not runtime.active
    assert runtime.public_status()["status"] == "STOPPED"


@pytest.mark.asyncio
async def test_local_host_stop_bounds_a_stalled_offline_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StalledOfflineRegistry(_RuntimeHostRegistry):
        async def set_status(self, **values: object) -> dict[str, object]:
            self.calls.append("offline_started")
            await anyio.sleep_forever()

    storage = _MemoryStorage()
    registry = _StalledOfflineRegistry(storage)
    monkeypatch.setattr(local_queue_module, "WorkerHostRegistry", lambda _storage: registry)
    monkeypatch.setattr(local_queue_module, "LOCAL_HOST_FENCE_TIMEOUT_SECONDS", 0.01)
    config = LocalHostQueueConfig(
        host_id_prefix="alpha-local",
        registration_token="local-host-test-token",
        sandbox_profile="sandboxed",
        sandbox_runtime="runsc",
    )
    runtime = LocalHostQueueRuntime(
        storage,  # type: ignore[arg-type]
        config,
        adapter_resolver=lambda _pins: None,
    )

    await runtime.start()
    with anyio.fail_after(0.25):
        await runtime.stop()

    assert registry.calls == ["register", "heartbeat", "offline_started"]
    assert not runtime.active
    assert runtime.public_status()["status"] == "STOPPED"
