"""AIAT-owned durable host capacity reservation and settlement ledger.

The ledger serializes reservations per host by locking the durable host row,
checks the host's authenticated READY lease, and sums active reservation
resources before inserting a new idempotent key.  Commit and release are
compare-and-set style transitions; no external worker or provider is called.
This is reservation authority only, not a live multi-host scheduler.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from mas_core.worker_registry.placement import HostCapacity

from ..memory import models as t

if TYPE_CHECKING:
    from ..memory.storage import AgentStorage

RESERVATION_SCHEMA = "aiat.worker-host-reservation.v1"
RESERVATION_STATES = frozenset({"RESERVED", "COMMITTED", "RELEASED", "EXPIRED"})
ACTIVE_STATES = ("RESERVED", "COMMITTED")
RESOURCE_FIELDS = ("slots", "memory_bytes", "gpu_count")


class ReservationRejected(RuntimeError):
    """Raised when a host cannot satisfy an authenticated reservation."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def normalize_resources(resources: Mapping[str, Any] | None) -> dict[str, int]:
    """Normalize a bounded worker resource request."""

    if resources is None:
        resources = {}
    if not isinstance(resources, Mapping):
        raise TypeError("resources must be a mapping")
    normalized: dict[str, int] = {}
    for field in RESOURCE_FIELDS:
        try:
            value = int(resources.get(field, 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"resource field {field!r} must be an integer") from exc
        if value < 0:
            raise ValueError(f"resource field {field!r} cannot be negative")
        normalized[field] = value
    if normalized["slots"] < 1:
        raise ValueError("resource slots must be at least one")
    return normalized


def _host_capacity(row: Mapping[str, Any]) -> HostCapacity:
    values = row.get("capacity")
    if not isinstance(values, Mapping):
        values = {}
    try:
        capacity = HostCapacity(
            slots_total=int(values.get("slots_total") or 0),
            slots_used=int(values.get("slots_used") or 0),
            memory_bytes_total=int(values.get("memory_bytes_total") or 0),
            memory_bytes_used=int(values.get("memory_bytes_used") or 0),
            gpu_total=int(values.get("gpu_total") or 0),
            gpu_used=int(values.get("gpu_used") or 0),
        )
    except (TypeError, ValueError) as exc:
        raise ReservationRejected("host_capacity_invalid") from exc
    if capacity.invalid():
        raise ReservationRejected("host_capacity_invalid")
    return capacity


def _sum_resources(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    total = {field: 0 for field in RESOURCE_FIELDS}
    for row in rows:
        resources = normalize_resources(row.get("resource_json") or {})
        for field in RESOURCE_FIELDS:
            total[field] += resources[field]
    return total


def _lease_valid(row: Mapping[str, Any], *, now: datetime) -> bool:
    expires_at = row.get("lease_expires_at")
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return row.get("state") == "RESERVED" and expires_at > now


def public_reservation(
    row: Mapping[str, Any],
    *,
    host_key: str | None = None,
    now: datetime | None = None,
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    """Project a reservation without connection, credential, or payload data."""

    current = now or datetime.now(tz=UTC)
    state = str(row.get("state") or "")
    return {
        "id": row.get("id"),
        "host_uuid": row.get("host_id"),
        "host_id": host_key,
        "reservation_key": str(row.get("reservation_key") or ""),
        "owner": str(row.get("owner") or ""),
        "resources": dict(row.get("resource_json") or {}),
        "state": state,
        "lease_valid": _lease_valid(row, now=current),
        "created_at": row.get("created_at"),
        "committed_at": row.get("committed_at"),
        "released_at": row.get("released_at"),
        "metadata": dict(row.get("metadata") or {}),
        "idempotent_replay": idempotent_replay,
    }


class HostCapacityReservationLedger:
    """Durable reservation authority layered on ``AgentStorage.engine``."""

    def __init__(self, storage: AgentStorage) -> None:
        self._storage = storage

    @staticmethod
    def _validate_identity(reservation_key: str, owner: str) -> tuple[str, str]:
        key = str(reservation_key or "").strip()
        actor = str(owner or "").strip()
        if not key:
            raise ValueError("reservation_key is required")
        if not actor:
            raise ValueError("owner is required")
        return key, actor

    async def _host_and_reservation(
        self,
        connection: Any,
        reservation_id: UUID,
        *,
        for_update: bool = True,
    ) -> Mapping[str, Any] | None:
        query = (
            sa.select(t.worker_host_reservations, t.worker_hosts.c.host_id.label("host_key"))
            .select_from(
                t.worker_host_reservations.join(
                    t.worker_hosts,
                    t.worker_hosts.c.id == t.worker_host_reservations.c.host_id,
                )
            )
            .where(t.worker_host_reservations.c.id == reservation_id)
        )
        if for_update:
            query = query.with_for_update()
        return (await connection.execute(query)).mappings().first()

    async def reserve(
        self,
        *,
        host_id: str,
        reservation_key: str,
        owner: str,
        resources: Mapping[str, Any] | None = None,
        lease_seconds: int = 60,
        metadata: Mapping[str, Any] | None = None,
        reservation_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Atomically reserve host capacity with idempotent replay."""

        host_key = str(host_id or "").strip()
        if not host_key:
            raise ValueError("host_id is required")
        reservation_key_value, actor = self._validate_identity(reservation_key, owner)
        request = normalize_resources(resources)
        seconds = int(lease_seconds)
        if seconds < 1 or seconds > 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        now = datetime.now(tz=UTC)
        async with self._storage.engine.begin() as connection:
            host = (
                await connection.execute(
                    t.worker_hosts.select()
                    .where(t.worker_hosts.c.host_id == host_key)
                    .with_for_update()
                )
            ).mappings().first()
            if host is None:
                raise ReservationRejected("host_not_registered")
            existing = (
                await connection.execute(
                    t.worker_host_reservations.select()
                    .where(
                        t.worker_host_reservations.c.reservation_key == reservation_key_value
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if existing is not None:
                existing_request = normalize_resources(existing["resource_json"] or {})
                if (
                    existing["host_id"] != host["id"]
                    or existing["owner"] != actor
                    or existing_request != request
                ):
                    raise ReservationRejected("reservation_key_conflict")
                return public_reservation(
                    {**dict(existing), "host_key": host_key},
                    host_key=host_key,
                    now=now,
                    idempotent_replay=True,
                )
            if host["status"] != "READY":
                raise ReservationRejected("host_not_ready")
            host_lease = host["lease_expires_at"]
            if not isinstance(host_lease, datetime) or host_lease <= now:
                raise ReservationRejected("host_lease_invalid")
            capacity = _host_capacity(host)
            active_rows = (
                await connection.execute(
                    t.worker_host_reservations.select()
                    .where(
                        sa.and_(
                            t.worker_host_reservations.c.host_id == host["id"],
                            t.worker_host_reservations.c.state.in_(ACTIVE_STATES),
                        )
                    )
                    .with_for_update()
                )
            ).mappings().all()
            reserved = _sum_resources(list(active_rows))
            available = {
                "slots": capacity.free_slots - reserved["slots"],
                "memory_bytes": capacity.free_memory_bytes - reserved["memory_bytes"],
                "gpu_count": capacity.free_gpus - reserved["gpu_count"],
            }
            for field in RESOURCE_FIELDS:
                if request[field] > available[field]:
                    raise ReservationRejected(f"capacity_{field}_exhausted")
            row_id = reservation_id or uuid4()
            await connection.execute(
                t.worker_host_reservations.insert().values(
                    id=row_id,
                    host_id=host["id"],
                    reservation_key=reservation_key_value,
                    owner=actor,
                    resource_json=request,
                    state="RESERVED",
                    lease_expires_at=now + timedelta(seconds=seconds),
                    metadata=dict(metadata or {}),
                    created_at=now,
                )
            )
            inserted = (
                await connection.execute(
                    t.worker_host_reservations.select().where(t.worker_host_reservations.c.id == row_id)
                )
            ).mappings().one()
        return public_reservation(inserted, host_key=host_key, now=now)

    async def _transition(
        self,
        reservation_id: UUID,
        *,
        owner: str,
        target: str,
    ) -> dict[str, Any]:
        actor = str(owner or "").strip()
        if not actor:
            raise ValueError("owner is required")
        if target not in {"COMMITTED", "RELEASED"}:
            raise ValueError("unsupported reservation transition")
        now = datetime.now(tz=UTC)
        async with self._storage.engine.begin() as connection:
            row = await self._host_and_reservation(connection, reservation_id)
            if row is None:
                raise ReservationRejected("reservation_not_found")
            if row["owner"] != actor:
                raise PermissionError("reservation owner mismatch")
            state = str(row["state"])
            if state == target or (state in {"RELEASED", "EXPIRED"} and target == "RELEASED"):
                return public_reservation(row, host_key=str(row["host_key"]), now=now, idempotent_replay=True)
            if target == "COMMITTED" and state != "RESERVED":
                raise ReservationRejected("reservation_not_transitionable")
            if target == "RELEASED" and state not in {"RESERVED", "COMMITTED"}:
                raise ReservationRejected("reservation_not_transitionable")
            if state == "RESERVED" and not _lease_valid(row, now=now):
                await connection.execute(
                    t.worker_host_reservations.update()
                    .where(t.worker_host_reservations.c.id == reservation_id)
                    .values(state="EXPIRED", released_at=now)
                )
                raise ReservationRejected("reservation_lease_expired")
            values: dict[str, Any] = {"state": target}
            if target == "COMMITTED":
                values.update(committed_at=now, lease_expires_at=None)
            else:
                values.update(released_at=now, lease_expires_at=None)
            await connection.execute(
                t.worker_host_reservations.update()
                .where(t.worker_host_reservations.c.id == reservation_id)
                .values(**values)
            )
            updated = await self._host_and_reservation(connection, reservation_id, for_update=False)
        if updated is None:
            raise ReservationRejected("reservation_not_found")
        return public_reservation(updated, host_key=str(updated["host_key"]), now=now)

    async def commit(self, reservation_id: UUID, *, owner: str) -> dict[str, Any]:
        return await self._transition(reservation_id, owner=owner, target="COMMITTED")

    async def release(self, reservation_id: UUID, *, owner: str) -> dict[str, Any]:
        return await self._transition(reservation_id, owner=owner, target="RELEASED")

    async def expire_reservations(self, *, limit: int = 100) -> int:
        now = datetime.now(tz=UTC)
        bounded_limit = max(1, min(int(limit), 1_000))
        async with self._storage.engine.begin() as connection:
            rows = (
                await connection.execute(
                    t.worker_host_reservations.select()
                    .where(
                        sa.and_(
                            t.worker_host_reservations.c.state == "RESERVED",
                            t.worker_host_reservations.c.lease_expires_at < now,
                        )
                    )
                    .order_by(t.worker_host_reservations.c.lease_expires_at.asc())
                    .limit(bounded_limit)
                    .with_for_update(skip_locked=True)
                )
            ).mappings().all()
            if not rows:
                return 0
            result = await connection.execute(
                t.worker_host_reservations.update()
                .where(t.worker_host_reservations.c.id.in_([row["id"] for row in rows]))
                .values(state="EXPIRED", released_at=now, lease_expires_at=None)
            )
        return int(result.rowcount or 0)

    async def get(self, reservation_id: UUID) -> dict[str, Any] | None:
        async with self._storage.engine.connect() as connection:
            row = await self._host_and_reservation(connection, reservation_id, for_update=False)
        return public_reservation(row, host_key=str(row["host_key"])) if row else None

    async def list_for_host(self, host_id: str) -> list[dict[str, Any]]:
        async with self._storage.engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.select(t.worker_host_reservations, t.worker_hosts.c.host_id.label("host_key"))
                    .select_from(
                        t.worker_host_reservations.join(
                            t.worker_hosts,
                            t.worker_hosts.c.id == t.worker_host_reservations.c.host_id,
                        )
                    )
                    .where(t.worker_hosts.c.host_id == str(host_id or "").strip())
                    .order_by(t.worker_host_reservations.c.created_at.asc())
                )
            ).mappings().all()
        return [public_reservation(row, host_key=str(row["host_key"])) for row in rows]

    async def capacity_projection(self, host_id: str) -> dict[str, Any]:
        """Return total/base/reserved/available scalar capacity for one host."""

        async with self._storage.engine.connect() as connection:
            host = (
                await connection.execute(
                    t.worker_hosts.select().where(t.worker_hosts.c.host_id == str(host_id or "").strip())
                )
            ).mappings().first()
            if host is None:
                raise ReservationRejected("host_not_registered")
            rows = (
                await connection.execute(
                    t.worker_host_reservations.select().where(
                        sa.and_(
                            t.worker_host_reservations.c.host_id == host["id"],
                            t.worker_host_reservations.c.state.in_(ACTIVE_STATES),
                        )
                    )
                )
            ).mappings().all()
        capacity = _host_capacity(host)
        reserved = _sum_resources(list(rows))
        return {
            "host_id": str(host["host_id"]),
            "state": str(host["status"]),
            "total": {
                "slots": capacity.slots_total,
                "memory_bytes": capacity.memory_bytes_total,
                "gpu_count": capacity.gpu_total,
            },
            "base_used": {
                "slots": capacity.slots_used,
                "memory_bytes": capacity.memory_bytes_used,
                "gpu_count": capacity.gpu_used,
            },
            "reserved": reserved,
            "available": {
                "slots": capacity.free_slots - reserved["slots"],
                "memory_bytes": capacity.free_memory_bytes - reserved["memory_bytes"],
                "gpu_count": capacity.free_gpus - reserved["gpu_count"],
            },
        }


__all__ = [
    "ACTIVE_STATES",
    "HostCapacityReservationLedger",
    "RESERVATION_SCHEMA",
    "RESERVATION_STATES",
    "ReservationRejected",
    "normalize_resources",
    "public_reservation",
]
