"""Admit and execute one Worker Run on its committed AIAT host binding.

The host executor is the narrow boundary between durable placement authority and
the existing Worker Run lifecycle controller.  It does not choose a host and
does not make licensing decisions.  A run is admitted only when its binding,
reservation, worker-plane identity, and current host lease all agree.  The
runtime remains behind the normal ``WorkerRunController`` adapter boundary.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ..worker_contract.models import WorkerCancellation
from .run_host_binding import RunHostBindingIdentity, WorkerRunHostBindingService

if TYPE_CHECKING:
    from ..memory.storage import AgentStorage
    from ..worker_contract.adapters import WorkerAdapter
    from ..worker_contract.controller import WorkerRunOutcome
    from ..worker_contract.models import WorkerRunRequest


HOST_EXECUTION_SCHEMA = "aiat.worker-host-execution.v1"
logger = logging.getLogger(__name__)


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
    def _heartbeat_interval(lease_seconds: int) -> float:
        """Renew often enough to leave time for the bounded run lease to expire."""

        return max(0.1, min(5.0, lease_seconds / 3))

    @staticmethod
    def _validate_binding(
        binding: dict[str, Any] | None,
        *,
        run_id: UUID,
        host_id: str,
        owner: str,
        worker_registry_id: UUID,
    ) -> dict[str, Any]:
        if binding is None:
            raise WorkerHostExecutionRejected("run_host_binding_not_found")
        if str(binding.get("run_id")) != str(run_id):
            raise WorkerHostExecutionRejected("run_host_binding_run_mismatch")
        if str(binding.get("worker_id")) != str(worker_registry_id):
            raise WorkerHostExecutionRejected("run_host_binding_worker_mismatch")
        if str(binding.get("host_id") or "") != host_id:
            raise WorkerHostExecutionRejected("run_host_binding_host_mismatch")
        if str(binding.get("owner") or "") != owner:
            raise WorkerHostExecutionRejected("run_host_binding_owner_mismatch")
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
        return binding

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
        snapshot = await getter(
            normalized_snapshot_id,
            project_id=getattr(worker_request, "project_id", None),
        )
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

    async def _binding_is_current(
        self,
        *,
        run_id: UUID,
        host_id: str,
        owner: str,
        worker_registry_id: UUID,
        identity: RunHostBindingIdentity,
    ) -> bool:
        """Check the exact assignment and its live host lease after each renewal."""

        try:
            binding = self._validate_binding(
                await self._bindings.get(run_id),
                run_id=run_id,
                host_id=host_id,
                owner=owner,
                worker_registry_id=worker_registry_id,
            )
        except Exception:
            return False
        return identity.matches(binding)

    @staticmethod
    async def _cancel_stale_adapter_run(
        adapter: WorkerAdapter,
        *,
        run_id: UUID,
        reason: str,
        owner: str,
        attempt_count: int,
    ) -> None:
        """Stop only this executor's runtime after its durable claim is lost.

        Calling the controller's operator cancellation path after owner/attempt
        loss could cancel a replacement attempt for the same run ID.  The
        adapter cancellation is the canonical runtime SPI and does not settle
        canonical state; the controller's persisted owner/attempt fence rejects
        any subsequent result from this executor.
        """

        cancellation = WorkerCancellation(
            run_id=run_id,
            reason=reason,
            requested_by="worker-host-executor",
            force=True,
        )
        cancellation._execution_claim_fence = {
            "claim_owner": owner,
            "attempt_count": attempt_count,
        }
        await adapter.cancel(cancellation)

    async def _supervise_execution(
        self,
        *,
        controller: Any,
        adapter: WorkerAdapter,
        execution_finished: asyncio.Event,
        run_id: UUID,
        host_id: str,
        owner: str,
        attempt_count: int,
        lease_seconds: int,
        worker_registry_id: UUID,
        binding_identity: RunHostBindingIdentity,
    ) -> None:
        heartbeat = getattr(self._storage, "heartbeat_worker_run", None)
        if not callable(heartbeat):
            # This is checked before claim in execute(); retain a fail-closed
            # guard here in case a storage implementation changes at runtime.
            await self._cancel_stale_adapter_run(
                adapter,
                run_id=run_id,
                reason="worker run heartbeat is unavailable",
                owner=owner,
                attempt_count=attempt_count,
            )
            return

        interval = self._heartbeat_interval(lease_seconds)
        while not execution_finished.is_set():
            try:
                await asyncio.wait_for(execution_finished.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            if execution_finished.is_set():
                return

            try:
                renewed = await heartbeat(
                    run_id,
                    owner=owner,
                    attempt_count=attempt_count,
                    lease_seconds=lease_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "worker_host_run_heartbeat_failed",
                    extra={"run_id": str(run_id), "error": type(exc).__name__},
                )
                try:
                    await self._cancel_stale_adapter_run(
                        adapter,
                        run_id=run_id,
                        reason="worker run claim could not be renewed",
                        owner=owner,
                        attempt_count=attempt_count,
                    )
                except Exception as cancel_exc:
                    logger.warning(
                        "worker_host_runtime_cancel_after_heartbeat_failure_failed",
                        extra={"run_id": str(run_id), "error": type(cancel_exc).__name__},
                    )
                return

            try:
                renewed_attempt_count = int((renewed or {}).get("attempt_count") or 0)
            except (AttributeError, TypeError, ValueError):
                renewed_attempt_count = 0
            if (
                not isinstance(renewed, dict)
                or str(renewed.get("claim_owner") or "") != owner
                or renewed_attempt_count != attempt_count
            ):
                try:
                    await self._cancel_stale_adapter_run(
                        adapter,
                        run_id=run_id,
                        reason="worker run claim is no longer current",
                        owner=owner,
                        attempt_count=attempt_count,
                    )
                except Exception as exc:
                    logger.warning(
                        "worker_host_runtime_cancel_after_claim_loss_failed",
                        extra={"run_id": str(run_id), "error": type(exc).__name__},
                    )
                return

            if execution_finished.is_set():
                return
            if await self._binding_is_current(
                run_id=run_id,
                host_id=host_id,
                owner=owner,
                worker_registry_id=worker_registry_id,
                identity=binding_identity,
            ):
                continue

            try:
                await controller.cancel(
                    run_id,
                    adapter,
                    reason="worker host binding or lease is no longer current",
                    requested_by="worker-host-executor",
                    force=True,
                    claim_owner=owner,
                    attempt_count=attempt_count,
                )
            except Exception as exc:
                logger.warning(
                    "worker_host_runtime_cancel_after_binding_loss_failed",
                    extra={"run_id": str(run_id), "error": type(exc).__name__},
                )
            return

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

        binding = self._validate_binding(
            await self._bindings.get(run_id),
            run_id=run_id,
            host_id=host_id,
            owner=owner,
            worker_registry_id=normalized_worker_registry_id,
        )
        binding_identity = RunHostBindingIdentity.from_binding(binding)
        await self._validate_model_resolution(worker_request, model_resolution_snapshot_id)
        if not callable(getattr(self._storage, "heartbeat_worker_run", None)):
            raise WorkerHostExecutionRejected("worker_run_heartbeat_unavailable")
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
        if str(claimed.get("claim_owner") or "") != owner:
            raise WorkerHostExecutionRejected("worker_run_claim_owner_mismatch")
        try:
            attempt_count = int(claimed.get("attempt_count") or 0)
        except (TypeError, ValueError) as exc:
            raise WorkerHostExecutionRejected("worker_run_claim_attempt_invalid") from exc
        if attempt_count < 1:
            raise WorkerHostExecutionRejected("worker_run_claim_attempt_invalid")
        if str(claimed.get("worker_id") or "") != str(normalized_worker_registry_id):
            raise WorkerHostExecutionRejected("worker_run_worker_mismatch")

        from ..worker_contract.controller import WorkerRunController

        controller = WorkerRunController(storage=self._storage)
        binding_after: dict[str, Any] | None = None
        release_error: Exception | None = None
        execution_finished = asyncio.Event()
        supervisor_task = asyncio.create_task(
            self._supervise_execution(
                controller=controller,
                adapter=adapter,
                execution_finished=execution_finished,
                run_id=run_id,
                host_id=host_id,
                owner=owner,
                attempt_count=attempt_count,
                lease_seconds=lease_seconds,
                worker_registry_id=normalized_worker_registry_id,
                binding_identity=binding_identity,
            ),
            name=f"worker-host-run-supervisor-{run_id}",
        )
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
            execution_finished.set()
            supervisor_task.cancel()
            await asyncio.gather(supervisor_task, return_exceptions=True)
            try:
                binding_after = await self._bindings.release(
                    run_id,
                    owner=owner,
                    expected_identity=binding_identity,
                )
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
