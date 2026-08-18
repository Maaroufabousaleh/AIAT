"""Bind durable Worker Runs to AIAT-owned worker-host reservations.

The host scheduler already selects a worker-plane host and creates a durable
capacity reservation.  This service adds the missing authority edge: one
Worker Run is bound to that reservation, with the host lease generation copied
into a separate immutable assignment record.  Assignment settlement is
idempotent and owner-bound.  The service does not invoke a worker runtime or a
provider; live execution remains a later dispatch boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from .host_reservations import HostCapacityReservationLedger
from .host_scheduler import HostScheduler, HostScheduleRequest

if TYPE_CHECKING:
    from ..memory.storage import AgentStorage
    from .placement import WorkerPlacementRequest


RUN_HOST_BINDING_SCHEMA = "aiat.worker-run-host-binding.v1"
RUN_HOST_BINDING_STATES = frozenset({"ASSIGNED", "COMMITTED", "RELEASED"})
RUN_HOST_RECOVERY_SCHEMA = "aiat.worker-run-host-recovery.v1"


class RunHostBindingRejected(RuntimeError):
    """Raised when a run cannot be assigned or settled safely."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class RunHostBindingRecoveryRejected(RuntimeError):
    """Raised when a committed binding cannot be safely reassigned."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RunHostBindingRequest:
    """Explicit identity and placement inputs for one run assignment."""

    run_id: UUID | str
    worker_id: UUID | str
    assignment_key: str
    owner: str
    placement: WorkerPlacementRequest
    lease_seconds: int = 300
    metadata: Mapping[str, Any] | None = None
    reservation_id: UUID | None = None

    def validate(self) -> tuple[UUID, UUID, str, str]:
        try:
            run_id = self.run_id if isinstance(self.run_id, UUID) else UUID(str(self.run_id))
            worker_id = self.worker_id if isinstance(self.worker_id, UUID) else UUID(str(self.worker_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("run_id and worker_id must be UUIDs") from exc
        assignment_key = str(self.assignment_key or "").strip()
        owner = str(self.owner or "").strip()
        if not assignment_key:
            raise ValueError("assignment_key is required")
        if not owner:
            raise ValueError("owner is required")
        if int(self.lease_seconds) < 1 or int(self.lease_seconds) > 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        if self.placement.invalid():
            raise ValueError("placement request is invalid")
        return run_id, worker_id, assignment_key, owner


def public_run_host_binding(
    row: Mapping[str, Any],
    *,
    host_key: str | None = None,
    reservation_state: str | None = None,
    reservation_lease_valid: bool | None = None,
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    """Project a binding without task input, result, or credential material."""

    return {
        "schema_version": RUN_HOST_BINDING_SCHEMA,
        "id": row.get("id"),
        "run_id": row.get("run_id"),
        "worker_id": row.get("worker_id"),
        "host_uuid": row.get("host_id"),
        "host_id": host_key if host_key is not None else row.get("host_key"),
        "host_plane": str(row.get("host_plane") or ""),
        "host_status": str(row.get("host_status") or ""),
        "reservation_id": row.get("reservation_id"),
        "host_lease_generation": int(row.get("host_lease_generation") or 1),
        "current_host_lease_generation": int(
            row.get("current_host_lease_generation")
            or row.get("host_lease_generation")
            or 1
        ),
        "assignment_key": str(row.get("assignment_key") or ""),
        "owner": str(row.get("owner") or ""),
        "state": str(row.get("state") or ""),
        "reservation_state": reservation_state
        if reservation_state is not None
        else row.get("reservation_state"),
        "reservation_lease_valid": reservation_lease_valid
        if reservation_lease_valid is not None
        else row.get("reservation_lease_valid"),
        "current_host_lease_valid": row.get("current_host_lease_valid"),
        "metadata": dict(row.get("metadata") or {}),
        "created_at": row.get("created_at"),
        "committed_at": row.get("committed_at"),
        "released_at": row.get("released_at"),
        "idempotent_replay": bool(idempotent_replay),
    }


class WorkerRunHostBindingService:
    """Durable run-to-host assignment authority layered on AgentStorage."""

    def __init__(self, storage: AgentStorage) -> None:
        self._storage = storage
        self._scheduler = HostScheduler(storage)
        self._ledger = HostCapacityReservationLedger(storage)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC)

    async def _fetch(
        self,
        connection: Any,
        *,
        run_id: UUID | None = None,
        assignment_key: str | None = None,
        for_update: bool = False,
    ) -> Mapping[str, Any] | None:
        from ..memory import models as t

        query = (
            sa.select(
                t.worker_run_host_bindings,
                t.worker_hosts.c.host_id.label("host_key"),
                t.worker_hosts.c.host_plane,
                t.worker_hosts.c.status.label("host_status"),
                t.worker_hosts.c.lease_generation.label("current_host_lease_generation"),
                t.worker_hosts.c.lease_expires_at.label("current_host_lease_expires_at"),
                t.worker_host_reservations.c.state.label("reservation_state"),
                t.worker_host_reservations.c.lease_expires_at.label("reservation_lease_expires_at"),
            )
            .select_from(
                t.worker_run_host_bindings.join(
                    t.worker_hosts,
                    t.worker_hosts.c.id == t.worker_run_host_bindings.c.host_id,
                ).join(
                    t.worker_host_reservations,
                    t.worker_host_reservations.c.id == t.worker_run_host_bindings.c.reservation_id,
                )
            )
        )
        if run_id is not None:
            query = query.where(t.worker_run_host_bindings.c.run_id == run_id)
        if assignment_key is not None:
            query = query.where(t.worker_run_host_bindings.c.assignment_key == assignment_key)
        if for_update:
            query = query.with_for_update()
        row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        values = dict(row)
        expires_at = values.get("reservation_lease_expires_at")
        host_expires_at = values.get("current_host_lease_expires_at")
        if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if isinstance(host_expires_at, datetime) and host_expires_at.tzinfo is None:
            host_expires_at = host_expires_at.replace(tzinfo=UTC)
        values["reservation_lease_valid"] = (
            values.get("reservation_state") == "RESERVED"
            and isinstance(expires_at, datetime)
            and expires_at > self._now()
        )
        values["current_host_lease_valid"] = (
            values.get("host_status") == "READY"
            and isinstance(host_expires_at, datetime)
            and host_expires_at > self._now()
        )
        return values

    async def _run_worker_id(self, run_id: UUID) -> UUID | None:
        from ..memory import models as t

        async with self._storage.engine.connect() as connection:
            return await connection.scalar(
                sa.select(t.worker_runs.c.worker_id).where(t.worker_runs.c.id == run_id)
            )

    async def get(self, run_id: UUID | str) -> dict[str, Any] | None:
        try:
            normalized = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("run_id must be a UUID") from exc
        async with self._storage.engine.connect() as connection:
            row = await self._fetch(connection, run_id=normalized)
        return public_run_host_binding(row) if row else None

    async def assign(self, request: RunHostBindingRequest) -> dict[str, Any]:
        run_id, worker_id, assignment_key, owner = request.validate()
        existing = await self.get(run_id)
        if existing is not None:
            if (
                str(existing.get("worker_id")) != str(worker_id)
                or str(existing.get("assignment_key")) != assignment_key
                or str(existing.get("owner")) != owner
            ):
                raise RunHostBindingRejected("run_host_binding_conflict")
            if existing.get("state") == "RELEASED":
                raise RunHostBindingRejected("run_host_binding_released")
            return {**existing, "idempotent_replay": True}

        run_worker_id = await self._run_worker_id(run_id)
        if run_worker_id is None:
            raise RunHostBindingRejected("run_not_found")
        if str(run_worker_id) != str(worker_id):
            raise RunHostBindingRejected("run_worker_mismatch")

        schedule = await self._scheduler.schedule(
            HostScheduleRequest(
                schedule_key=assignment_key,
                owner=owner,
                placement=request.placement,
                lease_seconds=request.lease_seconds,
                metadata={
                    **dict(request.metadata or {}),
                    "run_id": str(run_id),
                    "binding_schema": RUN_HOST_BINDING_SCHEMA,
                },
                reservation_id=request.reservation_id,
            )
        )
        reservation = schedule.get("reservation")
        if schedule.get("status") not in {"RESERVED", "REPLAYED"} or not isinstance(reservation, Mapping):
            raise RunHostBindingRejected("host_reservation_unavailable")
        try:
            host_uuid = reservation.get("host_uuid")
            reservation_id = reservation.get("id")
            host_lease_generation = int(reservation.get("host_lease_generation") or 1)
            if host_uuid is None or reservation_id is None:
                raise ValueError
            host_uuid = host_uuid if isinstance(host_uuid, UUID) else UUID(str(host_uuid))
            reservation_id = reservation_id if isinstance(reservation_id, UUID) else UUID(str(reservation_id))
        except (TypeError, ValueError) as exc:
            raise RunHostBindingRejected("reservation_projection_invalid") from exc

        from ..memory import models as t

        binding_id = uuid4()
        now = self._now()
        try:
            async with self._storage.engine.begin() as connection:
                run_row = (
                    await connection.execute(
                        sa.select(t.worker_runs.c.worker_id)
                        .where(t.worker_runs.c.id == run_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if run_row is None:
                    raise RunHostBindingRejected("run_not_found")
                if str(run_row["worker_id"]) != str(worker_id):
                    raise RunHostBindingRejected("run_worker_mismatch")
                await connection.execute(
                    t.worker_run_host_bindings.insert().values(
                        id=binding_id,
                        run_id=run_id,
                        worker_id=worker_id,
                        host_id=host_uuid,
                        reservation_id=reservation_id,
                        host_lease_generation=host_lease_generation,
                        assignment_key=assignment_key,
                        owner=owner,
                        state="ASSIGNED",
                        metadata=dict(request.metadata or {}),
                        created_at=now,
                    )
                )
        except sa.exc.IntegrityError as exc:
            replay = await self.get(run_id)
            if replay is not None and str(replay.get("assignment_key")) == assignment_key:
                return {**replay, "idempotent_replay": True}
            raise RunHostBindingRejected("run_host_binding_persistence_conflict") from exc
        result = await self.get(run_id)
        if result is None:
            raise RunHostBindingRejected("run_host_binding_not_readable")
        return result

    async def _settle(self, run_id: UUID | str, *, owner: str, target: str) -> dict[str, Any]:
        if target not in {"COMMITTED", "RELEASED"}:
            raise ValueError("unsupported run-host binding transition")
        try:
            normalized_run_id = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("run_id must be a UUID") from exc
        actor = str(owner or "").strip()
        if not actor:
            raise ValueError("owner is required")
        existing = await self.get(normalized_run_id)
        if existing is None:
            raise RunHostBindingRejected("run_host_binding_not_found")
        if str(existing.get("owner")) != actor:
            raise PermissionError("run-host binding owner mismatch")
        state = str(existing.get("state") or "")
        if state == target:
            return {**existing, "idempotent_replay": True}
        transitionable = state == "ASSIGNED" or (target == "RELEASED" and state == "COMMITTED")
        if not transitionable:
            raise RunHostBindingRejected("run_host_binding_not_transitionable")
        reservation_id = existing.get("reservation_id")
        if not isinstance(reservation_id, UUID):
            reservation_id = UUID(str(reservation_id))
        if target == "COMMITTED":
            await self._ledger.commit(reservation_id, owner=actor)
        else:
            await self._ledger.release(reservation_id, owner=actor)
        from ..memory import models as t

        now = self._now()
        state_clause = (
            t.worker_run_host_bindings.c.state == "ASSIGNED"
            if target == "COMMITTED"
            else t.worker_run_host_bindings.c.state.in_(("ASSIGNED", "COMMITTED"))
        )
        async with self._storage.engine.begin() as connection:
            await connection.execute(
                t.worker_run_host_bindings.update()
                .where(
                    sa.and_(
                        t.worker_run_host_bindings.c.run_id == normalized_run_id,
                        t.worker_run_host_bindings.c.owner == actor,
                        state_clause,
                    )
                )
                .values(
                    state=target,
                    committed_at=now if target == "COMMITTED" else None,
                    released_at=now if target == "RELEASED" else None,
                )
            )
        result = await self.get(normalized_run_id)
        if result is None:
            raise RunHostBindingRejected("run_host_binding_not_readable")
        return result

    async def commit(self, run_id: UUID | str, *, owner: str) -> dict[str, Any]:
        return await self._settle(run_id, owner=owner, target="COMMITTED")

    async def release(self, run_id: UUID | str, *, owner: str) -> dict[str, Any]:
        return await self._settle(run_id, owner=owner, target="RELEASED")

    async def reassign_after_host_loss(
        self,
        request: RunHostBindingRequest,
        *,
        recovery_reason: str = "host_lease_expired",
    ) -> dict[str, Any]:
        """Move a queued run from an expired binding to a fresh reservation.

        Host lease recovery expires the old reservation and leaves the binding
        as a durable audit pointer.  This method is the explicit queue-recovery
        edge: it only accepts that fenced state, requires the canonical run to
        be queued again, reserves a new eligible host, replaces the current
        binding in one row-locked update, and commits the new reservation.  It
        never dispatches a worker or contacts a provider.
        """

        run_id, worker_id, assignment_key, owner = request.validate()
        reason = str(recovery_reason or "").strip()
        if not reason or len(reason) > 200:
            raise ValueError("recovery_reason must be between 1 and 200 characters")
        existing = await self.get(run_id)
        if existing is None:
            raise RunHostBindingRecoveryRejected("run_host_binding_not_found")
        if str(existing.get("worker_id")) != str(worker_id):
            raise RunHostBindingRecoveryRejected("run_host_binding_worker_mismatch")
        if str(existing.get("owner") or "") != owner:
            raise PermissionError("run-host binding owner mismatch")
        if str(existing.get("state") or "") != "COMMITTED":
            raise RunHostBindingRecoveryRejected("run_host_binding_not_committed")
        if str(existing.get("assignment_key") or "") == assignment_key:
            raise RunHostBindingRecoveryRejected("run_host_recovery_assignment_key_reused")
        if str(existing.get("reservation_state") or "") != "EXPIRED":
            raise RunHostBindingRecoveryRejected("run_host_recovery_reservation_not_expired")
        if existing.get("current_host_lease_valid") is not False:
            raise RunHostBindingRecoveryRejected("run_host_recovery_host_lease_not_fenced")
        run = await self._storage.get_worker_run(run_id)
        if run is None:
            raise RunHostBindingRecoveryRejected("run_not_found")
        if str(run.get("state") or "") != "QUEUED":
            raise RunHostBindingRecoveryRejected("run_not_queued_for_host_recovery")

        old_host_id = str(existing.get("host_id") or "")
        old_reservation_id = str(existing.get("reservation_id") or "")
        old_generation = int(existing.get("host_lease_generation") or 1)
        schedule = await self._scheduler.schedule(
            HostScheduleRequest(
                schedule_key=assignment_key,
                owner=owner,
                placement=request.placement,
                lease_seconds=request.lease_seconds,
                metadata={
                    **dict(request.metadata or {}),
                    "run_id": str(run_id),
                    "binding_schema": RUN_HOST_BINDING_SCHEMA,
                    "recovery_schema": RUN_HOST_RECOVERY_SCHEMA,
                    "recovery_reason": reason,
                    "recovered_from_host_id": old_host_id,
                    "recovered_from_reservation_id": old_reservation_id,
                    "recovered_from_lease_generation": old_generation,
                },
                reservation_id=request.reservation_id,
            )
        )
        reservation = schedule.get("reservation")
        if schedule.get("status") not in {"RESERVED", "REPLAYED"} or not isinstance(
            reservation, Mapping
        ):
            raise RunHostBindingRecoveryRejected("host_recovery_reservation_unavailable")
        try:
            host_uuid = reservation.get("host_uuid")
            reservation_id = reservation.get("id")
            host_lease_generation = int(reservation.get("host_lease_generation") or 1)
            if host_uuid is None or reservation_id is None:
                raise ValueError
            host_uuid = host_uuid if isinstance(host_uuid, UUID) else UUID(str(host_uuid))
            reservation_id = reservation_id if isinstance(reservation_id, UUID) else UUID(str(reservation_id))
        except (TypeError, ValueError) as exc:
            raise RunHostBindingRecoveryRejected("reservation_projection_invalid") from exc
        if str(reservation_id) == old_reservation_id or str(reservation.get("host_id") or "") == old_host_id:
            raise RunHostBindingRecoveryRejected("host_recovery_same_host_selected")

        from ..memory import models as t

        metadata = {
            **dict(existing.get("metadata") or {}),
            **dict(request.metadata or {}),
            "recovery_schema": RUN_HOST_RECOVERY_SCHEMA,
            "recovery_reason": reason,
            "recovered_from_host_id": old_host_id,
            "recovered_from_reservation_id": old_reservation_id,
            "recovered_from_lease_generation": old_generation,
        }
        async with self._storage.engine.begin() as connection:
            row = (
                await connection.execute(
                    t.worker_run_host_bindings.select()
                    .where(t.worker_run_host_bindings.c.run_id == run_id)
                    .with_for_update()
                )
            ).mappings().first()
            if row is None or str(row.get("state") or "") != "COMMITTED":
                raise RunHostBindingRecoveryRejected("run_host_binding_changed_during_recovery")
            await connection.execute(
                t.worker_run_host_bindings.update()
                .where(t.worker_run_host_bindings.c.run_id == run_id)
                .values(
                    host_id=host_uuid,
                    reservation_id=reservation_id,
                    host_lease_generation=host_lease_generation,
                    assignment_key=assignment_key,
                    owner=owner,
                    state="ASSIGNED",
                    metadata=metadata,
                    committed_at=None,
                    released_at=None,
                )
            )
        return await self.commit(run_id, owner=owner)
