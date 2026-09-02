"""AIAT-owned deterministic multi-host selection and reservation scheduler.

The scheduler joins the durable host registry, pure placement predicate, and
row-locked reservation ledger.  It ranks every eligible host deterministically,
tries candidates in that order, and falls back when a concurrent reservation
or lease change makes a candidate unavailable.  A schedule key is globally
idempotent, so retries return the original host reservation.  This module does
not dispatch a worker, call a provider, or claim host-loss/split-brain
recovery; those remain separate authority boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

if TYPE_CHECKING:
    from uuid import UUID

from .host_registry import WorkerHostRegistry
from .host_reservations import (
    HostCapacityReservationLedger,
    ReservationRejected,
)
from .placement import (
    WorkerPlacementRequest,
    rank_eligible_hosts,
    select_host,
)

SCHEDULER_SCHEMA = "aiat.worker-host-scheduler.v1"
SCHEDULER_STATES = frozenset({"RESERVED", "REPLAYED", "BLOCKED"})


class SchedulerRejected(RuntimeError):
    """Raised for invalid or conflicting schedule identity."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class HostScheduleRequest:
    """Bounded, explicit request for one worker-host reservation."""

    schedule_key: str
    owner: str
    placement: WorkerPlacementRequest
    lease_seconds: int = 60
    metadata: Mapping[str, Any] | None = None
    reservation_id: UUID | None = None

    def validate(self) -> tuple[str, str]:
        schedule_key = str(self.schedule_key or "").strip()
        owner = str(self.owner or "").strip()
        if not schedule_key:
            raise ValueError("schedule_key is required")
        if not owner:
            raise ValueError("owner is required")
        if int(self.lease_seconds) < 1 or int(self.lease_seconds) > 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        if self.placement.invalid():
            raise ValueError("placement request is invalid")
        return schedule_key, owner

    def resources(self) -> dict[str, int]:
        """Translate the placement request into reservation resources."""

        return {
            "slots": int(self.placement.slots),
            "memory_bytes": int(self.placement.memory_bytes),
            "gpu_count": int(self.placement.gpu_count),
        }


def _decision_projection(decision: Any) -> dict[str, Any]:
    return {
        "host_id": decision.host_id,
        "eligible": bool(decision.eligible),
        "reason_codes": list(decision.reason_codes),
        "free_slots": int(decision.free_slots),
        "free_memory_bytes": int(decision.free_memory_bytes),
        "free_gpus": int(decision.free_gpus),
        "priority": int(decision.priority),
    }


def _base_report(*, schedule_key: str, owner: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEDULER_SCHEMA,
        "schedule_key": schedule_key,
        "owner": owner,
        "mutation_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "licence_metadata_is_gate": False,
        "scope": (
            "deterministic multi-host selection, row-locked reservation fallback, "
            "and idempotent schedule replay; no worker dispatch or provider call"
        ),
    }


class HostScheduler:
    """Select and reserve one host through AIAT-owned durable boundaries."""

    def __init__(self, storage: Any) -> None:
        self._registry = WorkerHostRegistry(storage)
        self._ledger = HostCapacityReservationLedger(storage)

    async def _replay(
        self,
        existing: Mapping[str, Any],
        *,
        schedule_key: str,
        owner: str,
        candidate_decisions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if str(existing.get("owner") or "") != owner:
            raise SchedulerRejected("schedule_key_conflict")
        report = _base_report(schedule_key=schedule_key, owner=owner)
        report.update(
            {
                "status": "REPLAYED",
                "selected_host_id": existing.get("host_id"),
                "scheduled_host_id": existing.get("host_id"),
                "candidate_decisions": candidate_decisions or [],
                "eligible_host_count": sum(
                    bool(decision.get("eligible")) for decision in candidate_decisions or []
                ),
                "attempts": [],
                "reservation": {**dict(existing), "idempotent_replay": True},
            }
        )
        return report

    async def schedule(self, request: HostScheduleRequest) -> dict[str, Any]:
        """Select and reserve a host, retrying safely across ranked candidates."""

        schedule_key, owner = request.validate()
        existing = await self._ledger.get_by_key(schedule_key)
        if existing is not None:
            return await self._replay(existing, schedule_key=schedule_key, owner=owner)

        snapshots = await self._registry.list_placement_snapshots()
        selected_host_id, decisions = select_host(snapshots, request.placement)
        candidate_decisions = [_decision_projection(decision) for decision in decisions]
        ranked = rank_eligible_hosts(decisions)
        attempts: list[dict[str, Any]] = []
        for decision in ranked:
            try:
                reservation = await self._ledger.reserve(
                    host_id=decision.host_id,
                    reservation_key=schedule_key,
                    owner=owner,
                    resources=request.resources(),
                    lease_seconds=request.lease_seconds,
                    metadata=request.metadata,
                    reservation_id=request.reservation_id,
                )
            except ReservationRejected as exc:
                attempts.append(
                    {
                        "host_id": decision.host_id,
                        "reason_code": exc.reason_code,
                    }
                )
                if exc.reason_code == "reservation_key_conflict":
                    replay = await self._ledger.get_by_key(schedule_key)
                    if replay is not None:
                        return await self._replay(
                            replay,
                            schedule_key=schedule_key,
                            owner=owner,
                            candidate_decisions=candidate_decisions,
                        )
                    raise SchedulerRejected("schedule_key_conflict") from exc
                continue
            except sa.exc.IntegrityError as exc:
                # Two schedulers may rank different hosts from the same
                # snapshot and race on the globally unique schedule key.  The
                # losing transaction rolls back; replay the winner rather than
                # exposing a database error or trying a second key.
                replay = await self._ledger.get_by_key(schedule_key)
                if replay is not None:
                    return await self._replay(
                        replay,
                        schedule_key=schedule_key,
                        owner=owner,
                        candidate_decisions=candidate_decisions,
                    )
                raise SchedulerRejected("schedule_persistence_conflict") from exc
            if reservation.get("idempotent_replay"):
                return await self._replay(
                    reservation,
                    schedule_key=schedule_key,
                    owner=owner,
                    candidate_decisions=candidate_decisions,
                )
            report = _base_report(schedule_key=schedule_key, owner=owner)
            report.update(
                {
                    "status": "RESERVED",
                    "selected_host_id": selected_host_id,
                    "scheduled_host_id": decision.host_id,
                    "candidate_decisions": candidate_decisions,
                    "eligible_host_count": len(ranked),
                    "attempts": attempts,
                    "reservation": reservation,
                    "mutation_performed": True,
                }
            )
            return report

        report = _base_report(schedule_key=schedule_key, owner=owner)
        report.update(
            {
                "status": "BLOCKED",
                "selected_host_id": selected_host_id,
                "scheduled_host_id": None,
                "candidate_decisions": candidate_decisions,
                "eligible_host_count": len(ranked),
                "attempts": attempts,
                "reservation": None,
            }
        )
        return report


__all__ = [
    "HostScheduleRequest",
    "HostScheduler",
    "SCHEDULER_SCHEMA",
    "SCHEDULER_STATES",
    "SchedulerRejected",
]
