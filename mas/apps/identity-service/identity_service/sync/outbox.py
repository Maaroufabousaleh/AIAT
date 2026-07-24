"""Cursor-based, at-least-once event replay."""

from __future__ import annotations

from typing import Any

from ..models import redact
from ..store import IdentityStore


class OutboxService:
    def __init__(self, store: IdentityStore) -> None:
        self.store = store

    async def append(self, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.store.create_outbox(event_type, aggregate_type, aggregate_id, redact(payload))

    async def reconcile(self, client_id: str, cursor: int, limit: int) -> dict[str, Any]:
        acknowledged = await self.store.get_client_cursor(client_id)
        # The server cursor is authoritative. If local state advanced but its
        # acknowledgement failed, replay from the older server cursor so the
        # laptop applies the event idempotently instead of silently skipping it.
        effective_cursor = min(cursor, acknowledged)
        events = await self.store.list_outbox(effective_cursor, limit)
        next_cursor = effective_cursor if not events else int(events[-1]["sequence"])
        return {
            "events": events,
            "cursor": effective_cursor,
            "requested_cursor": cursor,
            "acknowledged_cursor": acknowledged,
            "next_cursor": next_cursor,
            "has_more": len(events) == limit,
        }

    async def acknowledge(self, client_id: str, cursor: int) -> dict[str, int | str]:
        max_sequence = await self.store.get_max_outbox_sequence()
        if cursor > max_sequence:
            raise ValueError("identity reconciliation cursor exceeds the durable outbox")
        acknowledged = await self.store.advance_client_cursor(client_id, cursor)
        return {"client_id": client_id, "acknowledged_cursor": acknowledged}
