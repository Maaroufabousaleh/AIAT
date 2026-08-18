"""Durable, authenticated worker-host registration and lease state.

The registry keeps host authority in AIAT while treating a host process as an
untrusted execution boundary.  Registration credentials are accepted only to
authenticate the caller and are persisted as SHA-256 digests; public rows and
placement snapshots never expose the credential or its digest.  This module
does not reserve capacity or dispatch work.  It provides the durable host
facts that the deterministic placement policy can evaluate.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from mas_core.worker_registry.placement import HostCapacity, mapping_to_host_snapshot

from ..memory import models as t

if TYPE_CHECKING:
    from ..memory.storage import AgentStorage

HOST_REGISTRY_SCHEMA = "aiat.worker-host-registry.v1"
HOST_STATUSES = frozenset({"REGISTERING", "READY", "DRAINING", "OFFLINE", "REVOKED"})
_CAPACITY_FIELDS = (
    "slots_total",
    "slots_used",
    "memory_bytes_total",
    "memory_bytes_used",
    "gpu_total",
    "gpu_used",
)


def token_sha256(token: str) -> str:
    """Return the credential digest used by the durable registry."""

    value = str(token or "")
    if not value:
        raise ValueError("registration_token is required")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_status(status: str) -> str:
    normalized = str(status or "").strip().upper()
    if normalized not in HOST_STATUSES:
        raise ValueError(f"unsupported worker-host status: {status!r}")
    return normalized


def _normalize_labels(labels: Mapping[str, Any] | None) -> dict[str, str]:
    if labels is None:
        return {}
    if not isinstance(labels, Mapping):
        raise TypeError("labels must be a mapping")
    normalized: dict[str, str] = {}
    for key, value in labels.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if not key_text or not value_text:
            raise ValueError("host labels require non-empty keys and values")
        normalized[key_text] = value_text
    return dict(sorted(normalized.items()))


def _normalize_capabilities(capabilities: Sequence[str] | None) -> list[str]:
    if capabilities is None:
        return []
    if isinstance(capabilities, (str, bytes)):
        raise TypeError("capabilities must be a sequence of strings")
    values = {str(value).strip() for value in capabilities}
    if "" in values:
        raise ValueError("host capabilities cannot contain empty values")
    return sorted(values)


def _normalize_capacity(capacity: Mapping[str, Any] | None) -> dict[str, int]:
    if capacity is None:
        capacity = {}
    if not isinstance(capacity, Mapping):
        raise TypeError("capacity must be a mapping")
    values: dict[str, int] = {}
    for field in _CAPACITY_FIELDS:
        try:
            value = int(capacity.get(field, 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"capacity field {field!r} must be an integer") from exc
        if value < 0:
            raise ValueError(f"capacity field {field!r} cannot be negative")
        values[field] = value
    capacity_model = HostCapacity(**values)
    if capacity_model.invalid():
        raise ValueError("capacity used values cannot exceed totals")
    return values


def _normalize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")
    return dict(metadata)


def _lease_generation(value: Any, *, required: bool = True) -> int:
    if value is None and not required:
        return 0
    try:
        generation = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("lease_generation must be a positive integer") from exc
    if generation < 1:
        raise ValueError("lease_generation must be a positive integer")
    return generation


async def _expire_active_reservations(
    connection: Any,
    *,
    host_uuid: UUID,
    lease_generation: int,
    now: datetime,
) -> int:
    """Fence reservations belonging to one host incarnation."""

    result = await connection.execute(
        t.worker_host_reservations.update()
        .where(
            sa.and_(
                t.worker_host_reservations.c.host_id == host_uuid,
                t.worker_host_reservations.c.host_lease_generation == lease_generation,
                t.worker_host_reservations.c.state.in_(("RESERVED", "COMMITTED")),
            )
        )
        .values(state="EXPIRED", released_at=now, lease_expires_at=None)
    )
    return int(result.rowcount or 0)


def _lease_valid(row: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = row.get("lease_expires_at")
    if not isinstance(expires_at, datetime):
        return False
    current = now or datetime.now(tz=UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return row.get("status") != "REVOKED" and expires_at > current


def public_host_row(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Project a database row without credential or lease-owner secrets."""

    current = now or datetime.now(tz=UTC)
    return {
        "id": row.get("id"),
        "host_id": str(row.get("host_id") or ""),
        "status": str(row.get("status") or ""),
        "labels": dict(row.get("labels") or {}),
        "capabilities": sorted(str(value) for value in (row.get("capabilities") or [])),
        "sandbox_profile": str(row.get("sandbox_profile") or ""),
        "isolation_mode": str(row.get("isolation_mode") or ""),
        "capacity": dict(row.get("capacity") or {}),
        "priority": int(row.get("priority") or 0),
        "lease_generation": int(row.get("lease_generation") or 1),
        "lease_valid": _lease_valid(row, now=current),
        "heartbeat_at": row.get("heartbeat_at"),
        "last_seen_at": row.get("last_seen_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "metadata": dict(row.get("metadata") or {}),
    }


def host_snapshot_row(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Project one durable host into the placement adapter shape."""

    public = public_host_row(row, now=now)
    public.update(
        {
            "sandbox_profiles": [public["sandbox_profile"]],
            "isolation_modes": [public["isolation_mode"]],
        }
    )
    return public


class WorkerHostRegistry:
    """Storage-backed host registration, authentication, and heartbeat API."""

    def __init__(self, storage: AgentStorage) -> None:
        self._storage = storage

    async def register_host(
        self,
        *,
        host_id: str,
        registration_token: str,
        labels: Mapping[str, Any] | None = None,
        capabilities: Sequence[str] | None = None,
        sandbox_profile: str = "standard",
        isolation_mode: str = "native",
        capacity: Mapping[str, Any] | None = None,
        priority: int = 0,
        metadata: Mapping[str, Any] | None = None,
        host_uuid: UUID | None = None,
        status: str = "REGISTERING",
    ) -> dict[str, Any]:
        """Create or re-register a host after authenticating its credential."""

        host_key = str(host_id or "").strip()
        if not host_key:
            raise ValueError("host_id is required")
        if int(priority) < 0:
            raise ValueError("priority cannot be negative")
        normalized_status = _validate_status(status)
        normalized_labels = _normalize_labels(labels)
        normalized_capabilities = _normalize_capabilities(capabilities)
        normalized_capacity = _normalize_capacity(capacity)
        normalized_metadata = _normalize_metadata(metadata)
        digest = token_sha256(registration_token)
        now = datetime.now(tz=UTC)
        async with self._storage.engine.begin() as connection:
            existing = (
                await connection.execute(
                    t.worker_hosts.select()
                    .where(t.worker_hosts.c.host_id == host_key)
                    .with_for_update()
                )
            ).mappings().first()
            if existing is not None:
                if not hmac.compare_digest(str(existing["auth_token_sha256"]), digest):
                    raise PermissionError("worker host registration authentication failed")
                previous_generation = _lease_generation(existing.get("lease_generation") or 1)
                next_generation = previous_generation + 1
                await connection.execute(
                    t.worker_hosts.update()
                    .where(t.worker_hosts.c.id == existing["id"])
                    .values(
                        labels=normalized_labels,
                        capabilities=normalized_capabilities,
                        sandbox_profile=str(sandbox_profile).strip() or "standard",
                        isolation_mode=str(isolation_mode).strip() or "native",
                        capacity=normalized_capacity,
                        priority=int(priority),
                        metadata=normalized_metadata,
                        status=normalized_status,
                        lease_generation=next_generation,
                        lease_owner=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        updated_at=now,
                    )
                )
                await _expire_active_reservations(
                    connection,
                    host_uuid=existing["id"],
                    lease_generation=previous_generation,
                    now=now,
                )
                host_id_value = existing["id"]
            else:
                host_id_value = host_uuid or uuid4()
                await connection.execute(
                    t.worker_hosts.insert().values(
                        id=host_id_value,
                        host_id=host_key,
                        status=normalized_status,
                        auth_token_sha256=digest,
                        labels=normalized_labels,
                        capabilities=normalized_capabilities,
                        sandbox_profile=str(sandbox_profile).strip() or "standard",
                        isolation_mode=str(isolation_mode).strip() or "native",
                        capacity=normalized_capacity,
                        priority=int(priority),
                        lease_generation=1,
                        metadata=normalized_metadata,
                        created_at=now,
                        updated_at=now,
                    )
                )
            row = (
                await connection.execute(
                    t.worker_hosts.select().where(t.worker_hosts.c.id == host_id_value)
                )
            ).mappings().one()
        return public_host_row(row, now=now)

    async def authenticate_host(self, host_id: str, registration_token: str) -> bool:
        """Check a host credential without exposing stored credential material."""

        digest = token_sha256(registration_token)
        async with self._storage.engine.connect() as connection:
            stored = await connection.scalar(
                sa.select(t.worker_hosts.c.auth_token_sha256).where(
                    t.worker_hosts.c.host_id == str(host_id or "").strip()
                )
            )
        return stored is not None and hmac.compare_digest(str(stored), digest)

    async def heartbeat(
        self,
        *,
        host_id: str,
        registration_token: str,
        lease_generation: int | None = None,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        """Authenticate a host and renew its AIAT-owned liveness lease."""

        seconds = int(lease_seconds)
        if seconds < 1 or seconds > 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        generation = _lease_generation(lease_generation)
        digest = token_sha256(registration_token)
        host_key = str(host_id or "").strip()
        now = datetime.now(tz=UTC)
        async with self._storage.engine.begin() as connection:
            row = (
                await connection.execute(
                    t.worker_hosts.select()
                    .where(t.worker_hosts.c.host_id == host_key)
                    .with_for_update()
                )
            ).mappings().first()
            if row is None or not hmac.compare_digest(str(row["auth_token_sha256"]), digest):
                raise PermissionError("worker host heartbeat authentication failed")
            if row["status"] == "REVOKED":
                raise PermissionError("revoked worker host cannot heartbeat")
            current_generation = _lease_generation(row.get("lease_generation") or 1)
            if generation != current_generation:
                raise PermissionError("worker host lease generation mismatch")
            next_status = "READY" if row["status"] in {"REGISTERING", "OFFLINE"} else row["status"]
            await connection.execute(
                t.worker_hosts.update()
                .where(t.worker_hosts.c.id == row["id"])
                .values(
                    status=next_status,
                    lease_owner=host_key,
                    lease_expires_at=now + timedelta(seconds=seconds),
                    heartbeat_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
            )
            updated = (
                await connection.execute(
                    t.worker_hosts.select().where(t.worker_hosts.c.id == row["id"])
                )
            ).mappings().one()
        return public_host_row(updated, now=now)

    async def set_status(
        self,
        *,
        host_id: str,
        registration_token: str,
        lease_generation: int | None = None,
        status: str,
    ) -> dict[str, Any]:
        """Set a host status with host authentication; terminal revoke is sticky."""

        target = _validate_status(status)
        generation = _lease_generation(lease_generation)
        digest = token_sha256(registration_token)
        now = datetime.now(tz=UTC)
        async with self._storage.engine.begin() as connection:
            row = (
                await connection.execute(
                    t.worker_hosts.select()
                    .where(t.worker_hosts.c.host_id == str(host_id or "").strip())
                    .with_for_update()
                )
            ).mappings().first()
            if row is None or not hmac.compare_digest(str(row["auth_token_sha256"]), digest):
                raise PermissionError("worker host status authentication failed")
            if row["status"] == "REVOKED" and target != "REVOKED":
                raise PermissionError("revoked worker host status is immutable")
            current_generation = _lease_generation(row.get("lease_generation") or 1)
            if generation != current_generation:
                raise PermissionError("worker host lease generation mismatch")
            next_generation = current_generation + 1 if target in {"OFFLINE", "REVOKED"} else current_generation
            await connection.execute(
                t.worker_hosts.update()
                .where(t.worker_hosts.c.id == row["id"])
                .values(
                    status=target,
                    lease_generation=next_generation,
                    lease_owner=None if target in {"OFFLINE", "REVOKED"} else row["lease_owner"],
                    lease_expires_at=None if target in {"OFFLINE", "REVOKED"} else row["lease_expires_at"],
                    updated_at=now,
                )
            )
            if next_generation != current_generation:
                await _expire_active_reservations(
                    connection,
                    host_uuid=row["id"],
                    lease_generation=current_generation,
                    now=now,
                )
            updated = (
                await connection.execute(
                    t.worker_hosts.select().where(t.worker_hosts.c.id == row["id"])
                )
            ).mappings().one()
        return public_host_row(updated, now=now)

    async def get_host(self, host_id: str) -> dict[str, Any] | None:
        async with self._storage.engine.connect() as connection:
            row = (
                await connection.execute(
                    t.worker_hosts.select().where(t.worker_hosts.c.host_id == str(host_id or "").strip())
                )
            ).mappings().first()
        return public_host_row(row) if row else None

    async def list_hosts(self) -> list[dict[str, Any]]:
        async with self._storage.engine.connect() as connection:
            rows = (
                await connection.execute(t.worker_hosts.select().order_by(t.worker_hosts.c.host_id))
            ).mappings().all()
        return [public_host_row(row) for row in rows]

    async def list_placement_snapshots(self) -> tuple[Any, ...]:
        async with self._storage.engine.connect() as connection:
            rows = (
                await connection.execute(t.worker_hosts.select().order_by(t.worker_hosts.c.host_id))
            ).mappings().all()
        return tuple(mapping_to_host_snapshot(host_snapshot_row(row)) for row in rows)


__all__ = [
    "HOST_REGISTRY_SCHEMA",
    "HOST_STATUSES",
    "WorkerHostRegistry",
    "host_snapshot_row",
    "public_host_row",
    "token_sha256",
]
