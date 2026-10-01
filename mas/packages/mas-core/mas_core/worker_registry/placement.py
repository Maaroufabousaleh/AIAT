"""Deterministic worker-host placement and capacity policy.

This module is an AIAT-owned policy contract, not a host registry or a
scheduler.  It evaluates an explicitly supplied host snapshot and returns a
secret-safe, deterministic decision.  The caller remains responsible for
authenticated registration, durable leases, reservation/commit, and actual
dispatch.  Licence metadata is intentionally absent from every predicate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mas_core.execution_policy import (
    HostExecutionCapabilities,
    WorkloadExecutionPolicy,
    resolve_execution_target,
)
from mas_core.sandbox_policy import (
    canonical_sandbox_class,
    required_runtime_for_sandbox,
    runtime_implements_sandbox,
)

PLACEMENT_SCHEMA = "aiat.worker-placement.v1"
READY_HOST_STATUS = "READY"
HOST_PLANES = frozenset({"control", "tool", "data", "worker"})


@dataclass(frozen=True, slots=True)
class HostCapacity:
    """Finite host resources used by the placement predicate."""

    slots_total: int
    slots_used: int = 0
    memory_bytes_total: int = 0
    memory_bytes_used: int = 0
    gpu_total: int = 0
    gpu_used: int = 0

    def invalid(self) -> bool:
        values = (
            self.slots_total,
            self.slots_used,
            self.memory_bytes_total,
            self.memory_bytes_used,
            self.gpu_total,
            self.gpu_used,
        )
        return any(value < 0 for value in values) or any(
            used > total
            for total, used in (
                (self.slots_total, self.slots_used),
                (self.memory_bytes_total, self.memory_bytes_used),
                (self.gpu_total, self.gpu_used),
            )
        )

    @property
    def free_slots(self) -> int:
        return max(0, self.slots_total - self.slots_used)

    @property
    def free_memory_bytes(self) -> int:
        return max(0, self.memory_bytes_total - self.memory_bytes_used)

    @property
    def free_gpus(self) -> int:
        return max(0, self.gpu_total - self.gpu_used)


@dataclass(frozen=True, slots=True)
class WorkerPlacementRequest:
    """Explicit requirements supplied by a governed worker run."""

    worker_id: str
    required_capabilities: frozenset[str] = frozenset()
    required_labels: tuple[tuple[str, str], ...] = ()
    required_host_plane: str | None = "worker"
    required_sandbox_profile: str | None = None
    required_isolation_mode: str | None = None
    execution_policy: WorkloadExecutionPolicy | Mapping[str, Any] | None = None
    memory_bytes: int = 0
    gpu_count: int = 0
    slots: int = 1

    def invalid(self) -> bool:
        return (
            not self.worker_id.strip()
            or (
                self.required_host_plane is not None
                and self.required_host_plane not in HOST_PLANES
            )
            or self.memory_bytes < 0
            or self.gpu_count < 0
            or self.slots < 1
        )


@dataclass(frozen=True, slots=True)
class WorkerHostSnapshot:
    """Read-only host state used by one placement decision."""

    host_id: str
    status: str
    host_plane: str = "worker"
    labels: tuple[tuple[str, str], ...] = ()
    capabilities: frozenset[str] = frozenset()
    sandbox_profiles: frozenset[str] = frozenset()
    sandbox_runtimes: frozenset[str] = frozenset()
    isolation_modes: frozenset[str] = frozenset()
    capacity: HostCapacity = HostCapacity(0)
    lease_valid: bool = True
    priority: int = 0
    lease_generation: int = 1
    # New host-capability fields are additive.  Existing registry rows remain
    # usable through the legacy sandbox profile/runtime projection.
    execution_runtimes: frozenset[str] = frozenset()
    workload_formats: frozenset[str] = frozenset()
    kvm_available: bool = False
    wasmtime_available: bool = False
    remote_vm_available: bool = False
    gvisor_platform: str = "systrap"
    execution_capabilities_advertised: bool | None = None


@dataclass(frozen=True, slots=True)
class PlacementDecision:
    """Secret-safe result for one host candidate."""

    host_id: str
    eligible: bool
    reason_codes: tuple[str, ...]
    free_slots: int
    free_memory_bytes: int
    free_gpus: int
    priority: int
    execution_runtime: str | None = None
    execution_sandbox_class: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "eligible": self.eligible,
            "reason_codes": list(self.reason_codes),
            "free_slots": self.free_slots,
            "free_memory_bytes": self.free_memory_bytes,
            "free_gpus": self.free_gpus,
            "priority": self.priority,
            "execution_runtime": self.execution_runtime,
            "execution_sandbox_class": self.execution_sandbox_class,
        }


def _labels(values: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return {str(key): str(value) for key, value in values}


def evaluate_host(
    host: WorkerHostSnapshot,
    request: WorkerPlacementRequest,
) -> PlacementDecision:
    """Evaluate one explicitly supplied host without mutating reservations."""

    capacity = host.capacity
    reasons: list[str] = []
    execution_runtime: str | None = None
    execution_sandbox_class: str | None = None
    if request.invalid():
        reasons.append("request_invalid")
    if not host.host_id.strip():
        reasons.append("host_id_missing")
    if host.status != READY_HOST_STATUS:
        reasons.append("host_not_ready")
    if not host.lease_valid:
        reasons.append("host_lease_invalid")
    if host.host_plane not in HOST_PLANES:
        reasons.append("host_plane_invalid")
    if request.required_host_plane and host.host_plane != request.required_host_plane:
        reasons.append("host_plane_mismatch")
    if capacity.invalid():
        reasons.append("capacity_invalid")

    host_labels = _labels(host.labels)
    for key, value in request.required_labels:
        if host_labels.get(key) != value:
            reasons.append("placement_label_mismatch")
            break
    if not request.required_capabilities.issubset(host.capabilities):
        reasons.append("capability_missing")
    # The legacy profile predicate remains authoritative for callers that have
    # not adopted the format-aware policy.  Once an explicit execution policy
    # is supplied, its target resolution is the single host/runtime decision;
    # otherwise a stronger Kata target would be rejected by a stale
    # ``sandboxed -> runsc`` compatibility field.
    if request.required_sandbox_profile and request.execution_policy is None:
        try:
            required_class = canonical_sandbox_class(request.required_sandbox_profile)
            host_classes = {
                canonical_sandbox_class(profile)
                for profile in host.sandbox_profiles
                if str(profile).strip()
            }
        except ValueError:
            reasons.append("sandbox_profile_invalid")
        else:
            if required_class not in host_classes:
                reasons.append("sandbox_profile_unsupported")
            else:
                if host.sandbox_runtimes and not any(
                    runtime_implements_sandbox(runtime, required_class)
                    for runtime in host.sandbox_runtimes
                ):
                    reasons.append("sandbox_runtime_unsupported")
    if request.execution_policy is not None:
        try:
            execution_policy = (
                request.execution_policy
                if isinstance(request.execution_policy, WorkloadExecutionPolicy)
                else WorkloadExecutionPolicy.model_validate(request.execution_policy)
            )
            target = resolve_execution_target(
                execution_policy,
                HostExecutionCapabilities(
                    runtimes=frozenset(
                        host.execution_runtimes
                        if host.execution_capabilities_advertised is True
                        or (
                            host.execution_capabilities_advertised is None
                            and host.execution_runtimes
                        )
                        else frozenset()
                    ),
                    workload_formats=host.workload_formats,
                    kvm_available=host.kvm_available,
                    wasmtime_available=host.wasmtime_available,
                    remote_vm_available=host.remote_vm_available,
                    gvisor_platform=host.gvisor_platform,
                ),
                external=True,
            )
        except (TypeError, ValueError):
            reasons.append("execution_policy_invalid")
        else:
            execution_runtime = target.runtime
            execution_sandbox_class = target.sandbox_class
            if not target.available:
                reasons.append(target.reason_code)
    if (
        request.required_isolation_mode
        and request.required_isolation_mode not in host.isolation_modes
    ):
        reasons.append("isolation_mode_unsupported")
    if capacity.free_slots < request.slots:
        reasons.append("capacity_slots_exhausted")
    if capacity.free_memory_bytes < request.memory_bytes:
        reasons.append("capacity_memory_exhausted")
    if capacity.free_gpus < request.gpu_count:
        reasons.append("capacity_gpu_exhausted")

    # Preserve first occurrence order while avoiding duplicate reason codes.
    reason_codes = tuple(dict.fromkeys(reasons))
    return PlacementDecision(
        host_id=host.host_id,
        eligible=not reason_codes,
        reason_codes=reason_codes,
        free_slots=capacity.free_slots,
        free_memory_bytes=capacity.free_memory_bytes,
        free_gpus=capacity.free_gpus,
        priority=host.priority,
        execution_runtime=execution_runtime,
        execution_sandbox_class=execution_sandbox_class,
    )


def select_host(
    hosts: tuple[WorkerHostSnapshot, ...],
    request: WorkerPlacementRequest,
) -> tuple[str | None, tuple[PlacementDecision, ...]]:
    """Return the deterministic best eligible host and all decisions.

    Duplicate host IDs fail closed.  Eligible candidates are ordered by
    explicit priority, free slots, free memory, free GPUs, and finally host ID
    so two control-plane readers make the same choice from the same snapshot.
    No reservation or lease is created here.
    """

    host_ids = [host.host_id for host in hosts]
    duplicate_ids = len(host_ids) != len(set(host_ids))
    decisions = tuple(evaluate_host(host, request) for host in hosts)
    if duplicate_ids:
        return None, tuple(
            PlacementDecision(
                host_id=decision.host_id,
                eligible=False,
                reason_codes=tuple(dict.fromkeys((*decision.reason_codes, "host_registry_duplicate_id"))),
                free_slots=decision.free_slots,
                free_memory_bytes=decision.free_memory_bytes,
                free_gpus=decision.free_gpus,
                priority=decision.priority,
                execution_runtime=decision.execution_runtime,
                execution_sandbox_class=decision.execution_sandbox_class,
            )
            for decision in decisions
        )
    eligible = [decision for decision in decisions if decision.eligible]
    if not eligible:
        return None, decisions
    selected = rank_eligible_hosts(tuple(eligible))[0]
    return selected.host_id, decisions


def rank_eligible_hosts(
    decisions: tuple[PlacementDecision, ...],
) -> tuple[PlacementDecision, ...]:
    """Return eligible decisions in the canonical deterministic order.

    The scheduler uses this order to try the preferred host first and then
    safely fall back when a row-locked reservation observes a concurrent
    capacity or lease change.  Non-eligible decisions are omitted.
    """

    return tuple(
        sorted(
            (decision for decision in decisions if decision.eligible),
            key=lambda decision: (
                -decision.priority,
                -decision.free_slots,
                -decision.free_memory_bytes,
                -decision.free_gpus,
                decision.host_id,
            ),
        )
    )


def build_placement_report(
    *,
    hosts: tuple[WorkerHostSnapshot, ...],
    request: WorkerPlacementRequest,
) -> dict[str, Any]:
    """Build a bounded operator/fixture projection without host payloads."""

    selected_host_id, decisions = select_host(hosts, request)
    return {
        "schema_version": PLACEMENT_SCHEMA,
        "status": "pass" if selected_host_id is not None else "blocked",
        "selected_host_id": selected_host_id,
        "host_count": len(hosts),
        "eligible_host_count": sum(decision.eligible for decision in decisions),
        "decision_count": len(decisions),
        "decisions": [decision.as_dict() for decision in decisions],
        "mutation_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "deterministic placement constraints, host-plane separation, and capacity evaluation over an explicit host snapshot; no registration, reservation, lease, or dispatch",
    }


def mapping_to_host_snapshot(value: Mapping[str, Any]) -> WorkerHostSnapshot:
    """Normalize a JSON-like host row for adapter and checker callers."""

    capacity = value.get("capacity")
    if not isinstance(capacity, Mapping):
        capacity = {}
    sandbox_profiles = frozenset(str(item) for item in (value.get("sandbox_profiles") or ()))
    sandbox_runtimes = frozenset(str(item) for item in (value.get("sandbox_runtimes") or ()))
    if not sandbox_runtimes:
        runtime = str(value.get("sandbox_runtime") or "").strip().lower()
        if runtime:
            sandbox_runtimes = frozenset({runtime})
    if not sandbox_runtimes:
        inferred_runtimes: set[str] = set()
        for profile in sandbox_profiles:
            try:
                inferred_runtimes.add(required_runtime_for_sandbox(profile))
            except ValueError:
                continue
        sandbox_runtimes = frozenset(inferred_runtimes)
    def _string_set(raw: Any) -> frozenset[str]:
        values = (raw,) if isinstance(raw, str) else (raw or ())
        return frozenset(str(item).strip().lower() for item in values if str(item).strip())

    if "execution_runtimes" in value:
        execution_runtimes = _string_set(value.get("execution_runtimes"))
    elif "runtime_capabilities" in value:
        execution_runtimes = _string_set(value.get("runtime_capabilities"))
    else:
        execution_runtimes = sandbox_runtimes
    workload_formats = _string_set(
        value.get("workload_formats") or value.get("execution_formats")
    )
    if not workload_formats:
        if execution_runtimes & {"runc", "runsc", "kata"}:
            workload_formats = frozenset({"oci", "linux"})
        if value.get("wasmtime_available") or "wasmtime" in execution_runtimes:
            workload_formats = workload_formats | {"wasm"}
    return WorkerHostSnapshot(
        host_id=str(value.get("host_id") or ""),
        status=str(value.get("status") or ""),
        host_plane=str(value.get("host_plane") or "worker"),
        labels=tuple(sorted((str(key), str(item)) for key, item in (value.get("labels") or {}).items())),
        capabilities=frozenset(str(item) for item in (value.get("capabilities") or ())),
        sandbox_profiles=sandbox_profiles,
        sandbox_runtimes=sandbox_runtimes,
        isolation_modes=frozenset(str(item) for item in (value.get("isolation_modes") or ())),
        capacity=HostCapacity(
            slots_total=int(capacity.get("slots_total") or 0),
            slots_used=int(capacity.get("slots_used") or 0),
            memory_bytes_total=int(capacity.get("memory_bytes_total") or 0),
            memory_bytes_used=int(capacity.get("memory_bytes_used") or 0),
            gpu_total=int(capacity.get("gpu_total") or 0),
            gpu_used=int(capacity.get("gpu_used") or 0),
        ),
        lease_valid=bool(value.get("lease_valid", True)),
        priority=int(value.get("priority") or 0),
        lease_generation=max(1, int(value.get("lease_generation") or 1)),
        execution_runtimes=execution_runtimes,
        workload_formats=workload_formats,
        kvm_available=bool(value.get("kvm_available", False)),
        wasmtime_available=bool(value.get("wasmtime_available", "wasmtime" in execution_runtimes)),
        remote_vm_available=bool(value.get("remote_vm_available", "remote_vm" in execution_runtimes)),
        gvisor_platform=str(value.get("gvisor_platform") or "systrap"),
        execution_capabilities_advertised=(
            bool(value.get("execution_capabilities_advertised"))
            if "execution_capabilities_advertised" in value
            else any(
                key in value
                for key in (
                    "execution_runtimes",
                    "runtime_capabilities",
                    "workload_formats",
                    "execution_formats",
                    "kvm_available",
                    "wasmtime_available",
                    "remote_vm_available",
                )
            )
        ),
    )


__all__ = [
    "HostCapacity",
    "HOST_PLANES",
    "PLACEMENT_SCHEMA",
    "PlacementDecision",
    "WorkerHostSnapshot",
    "WorkerPlacementRequest",
    "build_placement_report",
    "evaluate_host",
    "mapping_to_host_snapshot",
    "rank_eligible_hosts",
    "select_host",
]
