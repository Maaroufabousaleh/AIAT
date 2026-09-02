"""Durable worker-host lease-loss recovery and fencing.

The recovery boundary is deliberately AIAT-owned.  It converts an expired
READY/DRAINING host lease into OFFLINE, advances the host lease generation,
and expires reservations from the lost incarnation in the same transaction.
That fencing prevents a delayed heartbeat or reservation settlement from
reviving a host after it has been replaced or recovered.  It does not start a
worker, call an external provider, or infer licence permissions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from ..memory import models as t

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..memory.storage import AgentStorage

HOST_RECOVERY_SCHEMA = "aiat.worker-host-recovery.v1"
RECOVERABLE_HOST_STATUSES = ("READY", "DRAINING")
ACTIVE_RESERVATION_STATES = ("RESERVED", "COMMITTED")


class HostLeaseRecovery:
    """Reconcile expired host leases with durable fencing semantics."""

    def __init__(self, storage: AgentStorage) -> None:
        self._storage = storage

    async def reconcile_expired_hosts(
        self,
        *,
        limit: int = 100,
        host_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fence expired hosts, optionally limited to an explicit host set."""

        bounded_limit = max(1, min(int(limit), 1_000))
        normalized_host_ids = tuple(
            dict.fromkeys(str(host_id or "").strip() for host_id in host_ids or ())
        )
        normalized_host_ids = tuple(host_id for host_id in normalized_host_ids if host_id)
        now = datetime.now(tz=UTC)
        recovered_hosts: list[dict[str, Any]] = []
        expired_reservations = 0
        async with self._storage.engine.begin() as connection:
            clauses = [
                t.worker_hosts.c.status.in_(RECOVERABLE_HOST_STATUSES),
                t.worker_hosts.c.lease_expires_at.is_not(None),
                t.worker_hosts.c.lease_expires_at <= now,
            ]
            if host_ids is not None:
                clauses.append(t.worker_hosts.c.host_id.in_(normalized_host_ids))
            rows = (
                await connection.execute(
                    t.worker_hosts.select()
                    .where(sa.and_(*clauses))
                    .order_by(t.worker_hosts.c.host_id.asc())
                    .limit(bounded_limit)
                    .with_for_update(skip_locked=True)
                )
            ).mappings().all()
            for row in rows:
                previous_generation = max(1, int(row.get("lease_generation") or 1))
                next_generation = previous_generation + 1
                await connection.execute(
                    t.worker_hosts.update()
                    .where(t.worker_hosts.c.id == row["id"])
                    .values(
                        status="OFFLINE",
                        lease_generation=next_generation,
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=now,
                    )
                )
                result = await connection.execute(
                    t.worker_host_reservations.update()
                    .where(
                        sa.and_(
                            t.worker_host_reservations.c.host_id == row["id"],
                            t.worker_host_reservations.c.host_lease_generation
                            == previous_generation,
                            t.worker_host_reservations.c.state.in_(ACTIVE_RESERVATION_STATES),
                        )
                    )
                    .values(state="EXPIRED", released_at=now, lease_expires_at=None)
                )
                expired_for_host = int(result.rowcount or 0)
                expired_reservations += expired_for_host
                recovered_hosts.append(
                    {
                        "host_id": str(row["host_id"]),
                        "from_status": str(row["status"]),
                        "previous_lease_generation": previous_generation,
                        "lease_generation": next_generation,
                        "expired_reservation_count": expired_for_host,
                    }
                )

        return {
            "schema_version": HOST_RECOVERY_SCHEMA,
            "status": "RECOVERED" if recovered_hosts else "NOOP",
            "recovered_host_count": len(recovered_hosts),
            "expired_reservation_count": expired_reservations,
            "hosts": recovered_hosts,
            "limit": bounded_limit,
            "host_filter": list(normalized_host_ids) if host_ids is not None else None,
            "mutation_performed": bool(recovered_hosts),
            "worker_dispatch_performed": False,
            "external_provider_mutation_performed": False,
            "licence_metadata_is_gate": False,
            "scope": (
                "expired READY/DRAINING host leases are durably fenced and marked OFFLINE; "
                "reservations from the lost host incarnation are expired"
            ),
        }


__all__ = [
    "ACTIVE_RESERVATION_STATES",
    "HOST_RECOVERY_SCHEMA",
    "HostLeaseRecovery",
    "RECOVERABLE_HOST_STATUSES",
]
