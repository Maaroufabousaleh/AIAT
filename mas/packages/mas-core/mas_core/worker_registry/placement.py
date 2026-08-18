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

PLACEMENT_SCHEMA = "aiat.worker-placement.v1"
READY_HOST_STATUS = "READY"


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
    required_sandbox_profile: str | None = None
    required_isolation_mode: str | None = None
    memory_bytes: int = 0
    gpu_count: int = 0
    slots: int = 1

    def invalid(self) -> bool:
        return (
            not self.worker_id.strip()
            or self.memory_bytes < 0
            or self.gpu_count < 0
            or self.slots < 1
        )


@dataclass(frozen=True, slots=True)
class WorkerHostSnapshot:
    """Read-only host state used by one placement decision."""

    host_id: str
    status: str
    labels: tuple[tuple[str, str], ...] = ()
    capabilities: frozenset[str] = frozenset()
    sandbox_profiles: frozenset[str] = frozenset()
    isolation_modes: frozenset[str] = frozenset()
    capacity: HostCapacity = HostCapacity(0)
    lease_valid: bool = True
    priority: int = 0


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "eligible": self.eligible,
            "reason_codes": list(self.reason_codes),
            "free_slots": self.free_slots,
            "free_memory_bytes": self.free_memory_bytes,
            "free_gpus": self.free_gpus,
            "priority": self.priority,
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
    if request.invalid():
        reasons.append("request_invalid")
    if not host.host_id.strip():
        reasons.append("host_id_missing")
    if host.status != READY_HOST_STATUS:
        reasons.append("host_not_ready")
    if not host.lease_valid:
        reasons.append("host_lease_invalid")
    if capacity.invalid():
        reasons.append("capacity_invalid")

    host_labels = _labels(host.labels)
    for key, value in request.required_labels:
        if host_labels.get(key) != value:
            reasons.append("placement_label_mismatch")
            break
    if not request.required_capabilities.issubset(host.capabilities):
        reasons.append("capability_missing")
    if (
        request.required_sandbox_profile
        and request.required_sandbox_profile not in host.sandbox_profiles
    ):
        reasons.append("sandbox_profile_unsupported")
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
            )
            for decision in decisions
        )
    eligible = [decision for decision in decisions if decision.eligible]
    if not eligible:
        return None, decisions
    selected = sorted(
        eligible,
        key=lambda decision: (
            -decision.priority,
            -decision.free_slots,
            -decision.free_memory_bytes,
            -decision.free_gpus,
            decision.host_id,
        ),
    )[0]
    return selected.host_id, decisions


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
        "scope": "deterministic placement constraints and capacity evaluation over an explicit host snapshot; no registration, reservation, lease, or dispatch",
    }


def mapping_to_host_snapshot(value: Mapping[str, Any]) -> WorkerHostSnapshot:
    """Normalize a JSON-like host row for adapter and checker callers."""

    capacity = value.get("capacity")
    if not isinstance(capacity, Mapping):
        capacity = {}
    return WorkerHostSnapshot(
        host_id=str(value.get("host_id") or ""),
        status=str(value.get("status") or ""),
        labels=tuple(sorted((str(key), str(item)) for key, item in (value.get("labels") or {}).items())),
        capabilities=frozenset(str(item) for item in (value.get("capabilities") or ())),
        sandbox_profiles=frozenset(str(item) for item in (value.get("sandbox_profiles") or ())),
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
    )


__all__ = [
    "HostCapacity",
    "PLACEMENT_SCHEMA",
    "PlacementDecision",
    "WorkerHostSnapshot",
    "WorkerPlacementRequest",
    "build_placement_report",
    "evaluate_host",
    "mapping_to_host_snapshot",
    "select_host",
]
