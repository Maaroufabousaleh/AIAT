"""Admit and execute one Worker Run on its committed AIAT host binding.

The host executor is the narrow boundary between durable placement authority and
the existing Worker Run lifecycle controller.  It does not choose a host and
does not make licensing decisions.  A run is admitted only when its binding,
reservation, worker-plane identity, and current host lease all agree.  The
runtime remains behind the normal ``WorkerRunController`` adapter boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .run_host_binding import WorkerRunHostBindingService

if TYPE_CHECKING:
    from ..memory.storage import AgentStorage
    from ..worker_contract.adapters import WorkerAdapter
    from ..worker_contract.controller import WorkerRunOutcome
    from ..worker_contract.models import WorkerRunRequest


HOST_EXECUTION_SCHEMA = "aiat.worker-host-execution.v1"


class WorkerHostExecutionRejected(RuntimeError):
    """Raised when a host cannot safely execute the requested Worker Run."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class HostExecutionRequest:
    """Identity of the host process asking to execute one bound run."""

    run_id: UUID | str
    host_id: str
    owner: str
    lease_seconds: int = 300

    def validate(self) -> tuple[UUID, str, str, int]:
        try:
            run_id = self.run_id if isinstance(self.run_id, UUID) else UUID(str(self.run_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("run_id must be a UUID") from exc
        host_id = str(self.host_id or "").strip()
        owner = str(self.owner or "").strip()
        if not host_id:
            raise ValueError("host_id is required")
        if not owner:
            raise ValueError("owner is required")
        try:
            lease_seconds = int(self.lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("lease_seconds must be an integer") from exc
        if lease_seconds < 1 or lease_seconds > 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        return run_id, host_id, owner, lease_seconds


@dataclass(frozen=True, slots=True)
class WorkerHostExecutionResult:
    """Secret-safe result of one admitted host execution."""

    run_id: UUID
    host_id: str
    outcome: WorkerRunOutcome
    binding_before: dict[str, Any]
    binding_after: dict[str, Any]
    claimed: dict[str, Any]


class WorkerHostExecutor:
    """Execute a committed binding through the canonical worker controller."""

    def __init__(
        self,
        storage: AgentStorage,
        *,
        binding_service: WorkerRunHostBindingService | None = None,
    ) -> None:
        self._storage = storage
        self._bindings = binding_service or WorkerRunHostBindingService(storage)

    @staticmethod
    def _validate_binding(
        binding: dict[str, Any] | None,
        *,
        run_id: UUID,
        host_id: str,
        worker_registry_id: UUID,
    ) -> None:
        if binding is None:
            raise WorkerHostExecutionRejected("run_host_binding_not_found")
        if str(binding.get("run_id")) != str(run_id):
            raise WorkerHostExecutionRejected("run_host_binding_run_mismatch")
        if str(binding.get("worker_id")) != str(worker_registry_id):
            raise WorkerHostExecutionRejected("run_host_binding_worker_mismatch")
        if str(binding.get("host_id") or "") != host_id:
            raise WorkerHostExecutionRejected("run_host_binding_host_mismatch")
        if str(binding.get("state") or "") != "COMMITTED":
            raise WorkerHostExecutionRejected("run_host_binding_not_committed")
        if str(binding.get("reservation_state") or "") != "COMMITTED":
            raise WorkerHostExecutionRejected("run_host_reservation_not_committed")
        if str(binding.get("host_plane") or "") != "worker":
            raise WorkerHostExecutionRejected("run_host_plane_mismatch")
        try:
            assignment_generation = int(binding.get("host_lease_generation") or 0)
            current_generation = int(binding.get("current_host_lease_generation") or 0)
        except (TypeError, ValueError) as exc:
            raise WorkerHostExecutionRejected("run_host_lease_generation_invalid") from exc
        if assignment_generation < 1 or current_generation < 1:
            raise WorkerHostExecutionRejected("run_host_lease_generation_invalid")
        if assignment_generation != current_generation:
            raise WorkerHostExecutionRejected("run_host_lease_generation_mismatch")
        if binding.get("host_status") != "READY":
            raise WorkerHostExecutionRejected("run_host_not_ready")
        if binding.get("current_host_lease_valid") is not True:
            raise WorkerHostExecutionRejected("run_host_lease_invalid")

    async def _validate_model_resolution(
        self,
        worker_request: WorkerRunRequest,
        model_resolution_snapshot_id: UUID | str | None,
    ) -> dict[str, Any] | None:
        """Fail closed when a run carries an inconsistent model snapshot.

        A snapshot is optional for legacy/native workers.  Once a caller
        supplies one (directly or through the resolved model reference), the
        host edge requires the immutable control-plane row to exist, be
        authorized, and agree with the request's selected profile/version and
        exact model.  This check happens before the Worker Run claim, so a
        malformed model route cannot consume a host lease or dispatch work.
        """

        reference = getattr(worker_request, "resolved_model_profile", None)
        reference_snapshot_id = getattr(reference, "resolution_snapshot_id", None)
        effective_snapshot_id = model_resolution_snapshot_id or reference_snapshot_id
        if effective_snapshot_id is None:
            return None
        try:
            normalized_snapshot_id = (
                effective_snapshot_id
                if isinstance(effective_snapshot_id, UUID)
                else UUID(str(effective_snapshot_id))
            )
        except (TypeError, ValueError) as exc:
            raise WorkerHostExecutionRejected("model_resolution_snapshot_invalid") from exc
        if reference_snapshot_id is not None:
            try:
                normalized_reference_id = (
                    reference_snapshot_id
                    if isinstance(reference_snapshot_id, UUID)
                    else UUID(str(reference_snapshot_id))
                )
            except (TypeError, ValueError) as exc:
                raise WorkerHostExecutionRejected("model_resolution_snapshot_invalid") from exc
            if normalized_reference_id != normalized_snapshot_id:
                raise WorkerHostExecutionRejected("model_resolution_snapshot_mismatch")
        getter = getattr(self._storage, "get_model_resolution_snapshot", None)
        if not callable(getter):
            raise WorkerHostExecutionRejected("model_resolution_snapshot_unavailable")
        snapshot = await getter(normalized_snapshot_id)
        if snapshot is None:
            raise WorkerHostExecutionRejected("model_resolution_snapshot_not_found")
        if snapshot.get("policy_failure_code"):
            raise WorkerHostExecutionRejected("model_resolution_snapshot_not_authorized")
        required_snapshot_fields = (
            "requested_profile_id",
            "resolved_profile_id",
            "resolved_profile_version",
            "exact_model_id",
        )
        if any(not str(snapshot.get(field) or "").strip() for field in required_snapshot_fields):
            raise WorkerHostExecutionRejected("model_resolution_snapshot_incomplete")
        if reference is not None:
            checks = (
                ("profile_id", snapshot.get("resolved_profile_id")),
                ("version", snapshot.get("resolved_profile_version")),
                ("exact_model_id", snapshot.get("exact_model_id")),
            )
            for field, snapshot_value in checks:
                reference_value = getattr(reference, field, None)
                if reference_value is not None and str(reference_value) != str(snapshot_value):
                    raise WorkerHostExecutionRejected("model_resolution_snapshot_reference_mismatch")
            requested = getattr(worker_request, "requested_model_profile", None)
            requested_profile_id = getattr(requested, "profile_id", None)
            if requested_profile_id is not None and str(requested_profile_id) != str(snapshot.get("requested_profile_id")):
                raise WorkerHostExecutionRejected("model_resolution_snapshot_requested_profile_mismatch")
        return dict(snapshot)

    async def execute(
        self,
        execution_request: HostExecutionRequest,
        worker_request: WorkerRunRequest,
        adapter: WorkerAdapter,
        *,
        worker_registry_id: UUID | str | None = None,
        worker_shell_version_id: UUID | None = None,
        adapter_id: UUID | None = None,
        skill_bundle_id: UUID | None = None,
        steward_id: UUID | None = None,
        model_resolution_snapshot_id: UUID | None = None,
    ) -> WorkerHostExecutionResult:
        run_id, host_id, owner, lease_seconds = execution_request.validate()
        request_run_id = getattr(worker_request, "run_id", None)
        try:
            normalized_request_run_id = (
                request_run_id if isinstance(request_run_id, UUID) else UUID(str(request_run_id))
            )
        except (TypeError, ValueError) as exc:
            raise WorkerHostExecutionRejected("worker_request_run_id_invalid") from exc
        if normalized_request_run_id != run_id:
            raise WorkerHostExecutionRejected("worker_request_run_mismatch")
        if worker_registry_id is None:
            raise WorkerHostExecutionRejected("worker_registry_id_required")
        try:
            normalized_worker_registry_id = (
                worker_registry_id
                if isinstance(worker_registry_id, UUID)
                else UUID(str(worker_registry_id))
            )
        except (TypeError, ValueError) as exc:
            raise WorkerHostExecutionRejected("worker_registry_id_invalid") from exc

        binding = await self._bindings.get(run_id)
        self._validate_binding(
            binding,
            run_id=run_id,
            host_id=host_id,
            worker_registry_id=normalized_worker_registry_id,
        )
        await self._validate_model_resolution(worker_request, model_resolution_snapshot_id)
        # The binding owner is the host's scoped execution identity.  Reusing
        # it for the Worker Run lease keeps claim and release auditable under
        # one actor without exposing host credentials to the worker runtime.
        claimed = await self._storage.claim_worker_run(
            owner=owner,
            lease_seconds=lease_seconds,
            run_id=run_id,
        )
        if claimed is None or str(claimed.get("state") or "") != "CLAIMED":
            raise WorkerHostExecutionRejected("worker_run_claim_failed")
        if str(claimed.get("worker_id") or "") != str(normalized_worker_registry_id):
            raise WorkerHostExecutionRejected("worker_run_worker_mismatch")

        from ..worker_contract.controller import WorkerRunController

        controller = WorkerRunController(storage=self._storage)
        binding_after: dict[str, Any] | None = None
        release_error: Exception | None = None
        try:
            outcome = await controller.execute(
                worker_request,
                adapter,
                worker_registry_id=normalized_worker_registry_id,
                worker_shell_version_id=worker_shell_version_id,
                adapter_id=adapter_id,
                skill_bundle_id=skill_bundle_id,
                steward_id=steward_id,
                model_resolution_snapshot_id=model_resolution_snapshot_id,
            )
        finally:
            try:
                binding_after = await self._bindings.release(run_id, owner=owner)
            except Exception as exc:  # pragma: no cover - live failure path
                release_error = exc
        if release_error is not None:
            raise WorkerHostExecutionRejected("run_host_binding_release_failed") from release_error
        if binding_after is None or str(binding_after.get("state") or "") != "RELEASED":
            raise WorkerHostExecutionRejected("run_host_binding_release_failed")
        return WorkerHostExecutionResult(
            run_id=run_id,
            host_id=host_id,
            outcome=outcome,
            binding_before=binding,
            binding_after=binding_after,
            claimed=claimed,
        )
