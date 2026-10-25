"""Consume a bounded queue snapshot for one configured worker host.

This module intentionally contains no placement, registration, or execution
authority. It revalidates the payload-free host binding and canonical Worker
Run rows, then delegates execution and all state changes to
``WorkerHostExecutor`` and ``WorkerRunController``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from mas_core.sandbox_policy import (
    WorkerIsolationPolicyError,
    worker_requires_hardened_isolation,
)

from ..worker_contract.models import WorkerRunRequest
from .execution_lineage import (
    SHELL_EXECUTION_LINEAGE_KEY,
    WorkerExecutionLineageError,
    worker_execution_transport_supported,
    worker_shell_execution_lineage_blocker,
)
from .host_executor import (
    HostExecutionRequest,
    WorkerHostExecutionRejected,
    WorkerHostExecutor,
)
from .run_host_binding import RunHostBindingIdentity, WorkerRunHostBindingService
from .worker_run_readiness import (
    HARDENED_SANDBOX_TRANSPORTS,
    canonical_worker_adapter_transport,
    worker_run_project_scope_blocker,
)

if TYPE_CHECKING:
    from ..memory.storage import AgentStorage
    from ..worker_contract.adapters import WorkerAdapter


_WORKER_RUN_STATES = frozenset(
    {
        "CREATED",
        "QUEUED",
        "CLAIMED",
        "VALIDATING",
        "READY",
        "DISPATCHING",
        "RUNNING",
        "PAUSING",
        "PAUSED",
        "RESUMING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "TIMED_OUT",
    }
)

_TRANSPORT_RUNTIME_TYPES: dict[str, frozenset[str]] = {
    "process": frozenset({"process"}),
    "wasm": frozenset({"wasmtime"}),
    "wasmtime": frozenset({"wasmtime"}),
    "http": frozenset({"http"}),
    "mcp": frozenset({"mcp"}),
    "oci": frozenset({"oci"}),
    "firecracker": frozenset({"firecracker"}),
    "gateway": frozenset({"aiat_gateway"}),
    "aiat_gateway": frozenset({"aiat_gateway"}),
    "openhands": frozenset({"openhands_agent_server"}),
    "openhands_agent_server": frozenset({"openhands_agent_server"}),
    "native": frozenset({"native"}),
    "langgraph": frozenset({"langgraph"}),
    "crewai": frozenset({"crewai"}),
    "opencode": frozenset({"opencode"}),
}


@dataclass(frozen=True, slots=True)
class PinnedWorkerAdapter:
    """Immutable registry pins used by the trusted adapter resolver."""

    worker_id: UUID
    worker_shell_version_id: UUID
    adapter_id: UUID
    skill_bundle_id: UUID | None = None
    steward_id: UUID | None = None


AdapterResolver = Callable[
    [PinnedWorkerAdapter], "WorkerAdapter | Awaitable[WorkerAdapter]"
]


@dataclass(frozen=True, slots=True)
class HostQueueRunSummary:
    """Payload-free outcome for one queue entry."""

    run_id: str
    state: str
    reason_code: str | None = None


class _RunRejected(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class HostQueueConsumer:
    """Execute committed queued runs for one fixed host identity.

    The configured host ID and owner are process identity, not per-entry
    inputs. A consumer cannot select another host or owner while running.
    ``adapter_resolver`` is a trusted resolver that must return the exact
    adapter represented by the supplied immutable run pins. It must never
    fall back to a worker's current adapter when the pinned adapter is missing
    or revoked. The consumer independently checks that worker, shell, adapter,
    and optional skill records remain eligible before calling that resolver.
    """

    def __init__(
        self,
        storage: AgentStorage,
        *,
        host_id: str,
        owner: str,
        adapter_resolver: AdapterResolver,
        binding_service: WorkerRunHostBindingService | None = None,
    ) -> None:
        normalized_host_id = str(host_id or "").strip()
        normalized_owner = str(owner or "").strip()
        if not normalized_host_id:
            raise ValueError("host_id is required")
        if not normalized_owner:
            raise ValueError("owner is required")
        if not callable(adapter_resolver):
            raise TypeError("adapter_resolver must be callable")
        self._storage = storage
        self._host_id = normalized_host_id
        self._owner = normalized_owner
        self._adapter_resolver = adapter_resolver
        self._bindings = binding_service or WorkerRunHostBindingService(storage)
        self._executor = WorkerHostExecutor(storage, binding_service=self._bindings)

    @staticmethod
    def _uuid(value: Any, reason_code: str) -> UUID:
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise _RunRejected(reason_code) from exc

    @staticmethod
    def _state(value: Any) -> str:
        state = str(value or "UNKNOWN").upper()
        return state if state in _WORKER_RUN_STATES else "UNKNOWN"

    @staticmethod
    def _optional_uuid(value: Any, reason_code: str) -> UUID | None:
        if value is None:
            return None
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise _RunRejected(reason_code) from exc

    @classmethod
    def _adapter_pins(
        cls,
        run: Mapping[str, Any],
        *,
        worker_id: UUID,
    ) -> PinnedWorkerAdapter:
        pinned_worker_id = cls._uuid(run.get("worker_id"), "run_worker_id_invalid")
        if pinned_worker_id != worker_id:
            raise _RunRejected("run_worker_mismatch")
        shell_version_id = cls._uuid(
            run.get("worker_shell_version_id"), "worker_shell_pin_missing_or_invalid"
        )
        adapter_id = cls._uuid(run.get("adapter_id"), "worker_adapter_pin_missing_or_invalid")
        return PinnedWorkerAdapter(
            worker_id=pinned_worker_id,
            worker_shell_version_id=shell_version_id,
            adapter_id=adapter_id,
            skill_bundle_id=cls._optional_uuid(
                run.get("skill_bundle_id"), "worker_skill_pin_invalid"
            ),
            steward_id=cls._optional_uuid(run.get("steward_id"), "worker_steward_pin_invalid"),
        )

    @staticmethod
    def _validate_binding_projection(
        projection: Mapping[str, Any],
        *,
        run_id: UUID,
        host_id: str,
        owner: str,
    ) -> RunHostBindingIdentity:
        if str(projection.get("run_id")) != str(run_id):
            raise _RunRejected("binding_run_mismatch")
        if str(projection.get("host_id") or "") != host_id:
            raise _RunRejected("binding_host_mismatch")
        if str(projection.get("owner") or "") != owner:
            raise _RunRejected("binding_owner_mismatch")
        if (
            str(projection.get("host_plane") or "") != "worker"
            or str(projection.get("host_status") or "") != "READY"
            or str(projection.get("state") or "") != "COMMITTED"
            or str(projection.get("reservation_state") or "") != "COMMITTED"
            or projection.get("current_host_lease_valid") is not True
        ):
            raise _RunRejected("binding_not_currently_executable")
        try:
            return RunHostBindingIdentity.from_binding(projection)
        except Exception as exc:
            raise _RunRejected("binding_identity_invalid") from exc

    @staticmethod
    def _validate_request(run: Mapping[str, Any], worker_id: UUID) -> WorkerRunRequest:
        request_payload = run.get("request_json")
        if not isinstance(request_payload, Mapping):
            raise _RunRejected("worker_request_missing_or_invalid")
        try:
            request = WorkerRunRequest.model_validate(dict(request_payload))
        except Exception as exc:
            raise _RunRejected("worker_request_invalid") from exc

        if str(request.run_id) != str(run.get("id")):
            raise _RunRejected("worker_request_run_mismatch")
        if str(request.worker_id) != str(worker_id):
            raise _RunRejected("worker_request_worker_mismatch")
        if str(request.idempotency_key) != str(run.get("idempotency_key") or ""):
            raise _RunRejected("worker_request_idempotency_mismatch")
        if str(request.task_type) != str(run.get("task_type") or ""):
            raise _RunRejected("worker_request_task_type_mismatch")
        for request_field, run_field in (
            ("project_id", "project_id"),
            ("flow_id", "flow_id"),
            ("flow_instance_id", "flow_instance_id"),
            ("flow_node_execution_id", "flow_node_execution_id"),
        ):
            request_value = getattr(request, request_field)
            stored_value = run.get(run_field)
            if str(request_value) != str(stored_value):
                raise _RunRejected("worker_request_scope_mismatch")
        return request

    async def _validate_current_project_scope(
        self,
        run: Mapping[str, Any],
        request: WorkerRunRequest,
    ) -> None:
        project: Mapping[str, Any] | None = None
        assignments: list[Mapping[str, Any]] = []
        if request.project_id is not None:
            project_reader = getattr(self._storage, "get_project", None)
            if not inspect.iscoroutinefunction(project_reader):
                raise _RunRejected("worker_project_scope_store_unavailable")
            project = await project_reader(request.project_id)
            if project is not None and project.get("company_id") is not None:
                assignment_reader = getattr(
                    self._storage,
                    "list_company_worker_assignments",
                    None,
                )
                if not inspect.iscoroutinefunction(assignment_reader):
                    raise _RunRejected("company_assignment_store_unavailable")
                rows = await assignment_reader(UUID(str(project["company_id"])))
                if not isinstance(rows, list):
                    raise _RunRejected("company_assignment_store_invalid")
                assignments = rows
        blocker = worker_run_project_scope_blocker(
            run=run,
            request=request.model_dump(mode="json"),
            project=project,
            assignments=assignments,
        )
        if blocker is not None:
            raise _RunRejected(blocker)

    async def _validate_worker_activation_and_pins(
        self,
        worker: Mapping[str, Any],
        pins: PinnedWorkerAdapter,
    ) -> tuple[str, str]:
        status = str(worker.get("status") or "").upper()
        if status not in {"ACTIVE", "DRAINING"}:
            raise _RunRejected("worker_not_active")
        if str(worker.get("evaluation_status") or "").lower() != "approved":
            raise _RunRejected("worker_evaluation_not_approved")

        shell_reader = getattr(self._storage, "get_worker_shell_version", None)
        adapter_reader = getattr(self._storage, "get_runtime_adapter", None)
        if not callable(shell_reader) or not callable(adapter_reader):
            raise _RunRejected("worker_pin_validation_unavailable")
        shell = await shell_reader(pins.worker_shell_version_id)
        if (
            not isinstance(shell, Mapping)
            or str(shell.get("id")) != str(pins.worker_shell_version_id)
            or str(shell.get("worker_id")) != str(pins.worker_id)
            or str(shell.get("status") or "").lower() not in {"active", "superseded"}
        ):
            raise _RunRejected("worker_shell_pin_not_eligible")

        adapter = await adapter_reader(pins.adapter_id)
        if (
            not isinstance(adapter, Mapping)
            or str(adapter.get("id")) != str(pins.adapter_id)
            or str(adapter.get("worker_id")) != str(pins.worker_id)
            or str(adapter.get("status") or "").lower() not in {"active", "superseded"}
            or str(adapter.get("conformance_status") or "").lower() != "passed"
        ):
            raise _RunRejected("worker_adapter_pin_not_eligible")

        transport = canonical_worker_adapter_transport(
            adapter.get("transport_type")
        )
        if not transport:
            raise _RunRejected("worker_adapter_transport_missing")
        try:
            requires_hardened_isolation = worker_requires_hardened_isolation(
                worker
            )
        except WorkerIsolationPolicyError as exc:
            raise _RunRejected(exc.reason_code) from exc
        if requires_hardened_isolation:
            if transport == "process":
                # ProcessAdapter executes directly on the host. Placement may
                # certify a host runtime, but that decision is only enforced by
                # adapters that launch through a supported sandbox runtime.
                raise _RunRejected("worker_sandbox_process_transport_unsupported")
            if transport not in HARDENED_SANDBOX_TRANSPORTS:
                raise _RunRejected("worker_sandbox_transport_unverified")

        if not worker_execution_transport_supported(transport):
            raise _RunRejected("worker_adapter_transport_unverified")

        lineage_blocker = worker_shell_execution_lineage_blocker(
            shell=shell,
            adapter=adapter,
            worker_id=pins.worker_id,
            adapter_id=pins.adapter_id,
        )
        if lineage_blocker is not None:
            raise _RunRejected(lineage_blocker)
        shell_provenance = shell.get("provenance_json") or {}
        lineage = (
            shell_provenance.get(SHELL_EXECUTION_LINEAGE_KEY)
            if isinstance(shell_provenance, Mapping)
            else None
        )
        execution_config_sha256 = (
            str(lineage.get("sha256") or "") if isinstance(lineage, Mapping) else ""
        )
        if not execution_config_sha256:
            raise _RunRejected("worker_runtime_lineage_unavailable")

        if pins.skill_bundle_id is not None:
            bundle_reader = getattr(self._storage, "get_skill_bundle", None)
            if not callable(bundle_reader):
                raise _RunRejected("worker_skill_pin_validation_unavailable")
            bundle = await bundle_reader(pins.skill_bundle_id)
            if (
                not isinstance(bundle, Mapping)
                or str(bundle.get("id")) != str(pins.skill_bundle_id)
                or str(bundle.get("worker_id")) != str(pins.worker_id)
                or str(bundle.get("status") or "").upper()
                not in {"APPROVED", "SUPERSEDED"}
                or (
                    pins.steward_id is not None
                    and str(bundle.get("steward_id")) != str(pins.steward_id)
                )
            ):
                raise _RunRejected("worker_skill_pin_not_eligible")

        return transport, execution_config_sha256

    async def _current_state(self, run_id: UUID) -> str:
        try:
            row = await self._storage.get_worker_run(run_id)
        except Exception:
            return "UNKNOWN"
        return self._state((row or {}).get("state"))

    async def _execute_entry(self, queue_entry: Mapping[str, Any]) -> HostQueueRunSummary:
        raw_run_id = queue_entry.get("run_id")
        try:
            run_id = self._uuid(raw_run_id, "queue_run_id_invalid")
        except _RunRejected as exc:
            # Only a bounded identifier marker is exposed for a corrupt queue
            # projection; never echo any other queue fields.
            return HostQueueRunSummary("<invalid>", "UNKNOWN", exc.reason_code)
        safe_run_id = str(run_id)

        try:
            run = await self._storage.get_worker_run(run_id)
            if not isinstance(run, Mapping):
                raise _RunRejected("worker_run_not_found")
            if str(run.get("id")) != safe_run_id:
                raise _RunRejected("worker_run_identity_mismatch")
            state = self._state(run.get("state"))
            if state != "QUEUED":
                raise _RunRejected("worker_run_not_queued")

            worker_id = self._uuid(run.get("worker_id"), "run_worker_id_invalid")
            if str(queue_entry.get("worker_id")) != str(worker_id):
                raise _RunRejected("queue_worker_mismatch")
            worker = await self._storage.get_worker(worker_id)
            if not isinstance(worker, Mapping):
                raise _RunRejected("worker_not_found")
            if str(worker.get("id")) != str(worker_id):
                raise _RunRejected("worker_identity_mismatch")
            if str(worker.get("status") or "").upper() not in {"ACTIVE", "DRAINING"}:
                raise _RunRejected("worker_not_active")

            queued_identity = self._validate_binding_projection(
                queue_entry,
                run_id=run_id,
                host_id=self._host_id,
                owner=self._owner,
            )
            binding = await self._bindings.get(run_id)
            if not isinstance(binding, Mapping):
                raise _RunRejected("current_binding_not_found")
            current_identity = self._validate_binding_projection(
                binding,
                run_id=run_id,
                host_id=self._host_id,
                owner=self._owner,
            )
            if queued_identity != current_identity:
                raise _RunRejected("queue_binding_stale")
            if str(binding.get("worker_id")) != str(worker_id):
                raise _RunRejected("binding_worker_mismatch")

            request = self._validate_request(run, worker_id)
            await self._validate_current_project_scope(run, request)
            pins = self._adapter_pins(run, worker_id=worker_id)
            pinned_transport, pinned_execution_config_sha256 = (
                await self._validate_worker_activation_and_pins(worker, pins)
            )
            adapter = self._adapter_resolver(pins)
            if inspect.isawaitable(adapter):
                adapter = await adapter
            resolved_adapter_id = getattr(adapter, "_aiat_adapter_id", None)
            if str(resolved_adapter_id or "") != str(pins.adapter_id):
                raise _RunRejected("pinned_adapter_resolution_mismatch")
            resolved_shell_id = getattr(adapter, "_aiat_shell_version_id", None)
            if str(resolved_shell_id or "") != str(pins.worker_shell_version_id):
                raise _RunRejected("pinned_worker_shell_resolution_mismatch")
            resolved_execution_config_sha256 = getattr(
                adapter, "_aiat_execution_config_sha256", None
            )
            if str(resolved_execution_config_sha256 or "") != pinned_execution_config_sha256:
                raise _RunRejected("pinned_worker_execution_config_mismatch")
            resolved_transport = str(getattr(adapter, "runtime_type", "") or "").strip().lower()
            expected_runtime_types = _TRANSPORT_RUNTIME_TYPES.get(pinned_transport)
            if expected_runtime_types is None or resolved_transport not in expected_runtime_types:
                raise _RunRejected("pinned_adapter_transport_mismatch")

            result = await self._executor.execute(
                HostExecutionRequest(
                    run_id=run_id,
                    host_id=self._host_id,
                    owner=self._owner,
                ),
                request,
                adapter,
                worker_registry_id=worker_id,
                worker_shell_version_id=pins.worker_shell_version_id,
                adapter_id=pins.adapter_id,
                skill_bundle_id=pins.skill_bundle_id,
                steward_id=pins.steward_id,
                model_resolution_snapshot_id=self._optional_uuid(
                    run.get("model_resolution_snapshot_id"),
                    "model_resolution_snapshot_id_invalid",
                ),
            )
            return HostQueueRunSummary(
                run_id=safe_run_id,
                state=self._state(result.outcome.state),
            )
        except _RunRejected as exc:
            return HostQueueRunSummary(
                run_id=safe_run_id,
                state=await self._current_state(run_id),
                reason_code=exc.reason_code,
            )
        except WorkerHostExecutionRejected as exc:
            return HostQueueRunSummary(
                run_id=safe_run_id,
                state=await self._current_state(run_id),
                reason_code=exc.reason_code,
            )
        except WorkerExecutionLineageError as exc:
            return HostQueueRunSummary(
                run_id=safe_run_id,
                state=await self._current_state(run_id),
                reason_code=exc.reason_code,
            )
        except Exception:
            # Adapter/runtime exceptions may contain prompt, tool, or secret
            # material. Keep the result useful without exposing exception text.
            return HostQueueRunSummary(
                run_id=safe_run_id,
                state=await self._current_state(run_id),
                reason_code="host_execution_failed",
            )

    async def consume_once(self, *, limit: int = 20) -> tuple[HostQueueRunSummary, ...]:
        """Read and process at most one bounded payload-free host queue page."""

        if isinstance(limit, bool):
            raise ValueError("limit must be between 1 and 100")
        try:
            bounded_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be between 1 and 100") from exc
        if bounded_limit < 1 or bounded_limit > 100:
            raise ValueError("limit must be between 1 and 100")

        queue_page = await self._bindings.list_queued_committed_for_host(
            self._host_id,
            limit=bounded_limit,
        )
        if not isinstance(queue_page, list):
            raise TypeError("host queue query must return a list")
        summaries: list[HostQueueRunSummary] = []
        seen_run_ids: set[str] = set()
        for queue_entry in queue_page[:bounded_limit]:
            if not isinstance(queue_entry, Mapping):
                summaries.append(
                    HostQueueRunSummary("<invalid>", "UNKNOWN", "queue_entry_invalid")
                )
                continue
            raw_run_id = queue_entry.get("run_id")
            try:
                run_key = str(self._uuid(raw_run_id, "queue_run_id_invalid"))
            except _RunRejected:
                summaries.append(
                    HostQueueRunSummary("<invalid>", "UNKNOWN", "queue_run_id_invalid")
                )
                continue
            if run_key in seen_run_ids:
                continue
            seen_run_ids.add(run_key)
            summaries.append(await self._execute_entry(queue_entry))
        return tuple(summaries)
