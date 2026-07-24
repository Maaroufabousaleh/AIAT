"""Reserve/commit/release accounting with idempotent holds."""

from __future__ import annotations

from uuid import UUID

from ..models import UsageHoldState
from ..store import IdentityStore

IDENTITY_USAGE_KINDS = frozenset({
    "mailbox_provisioning",
    "mailbox_storage_mb",
    "outbound_message",
    "browser_minute",
    "signup_attempt",
    "provider_api_call",
    "mfa_provider",
})


class UsageLedger:
    def __init__(self, store: IdentityStore) -> None:
        self.store = store

    async def reserve(self, *, worker_id: UUID, kind: str, idempotency_key: str, units: int = 1) -> dict:
        if kind not in IDENTITY_USAGE_KINDS:
            raise ValueError("unknown identity usage category is denied")
        if units <= 0:
            raise ValueError("identity usage units must be positive")
        hold, _created = await self.store.reserve_hold(worker_id=worker_id, kind=kind, idempotency_key=idempotency_key, units=units)
        return hold

    async def commit(self, hold_id: UUID) -> dict | None:
        return await self.store.settle_hold(hold_id, UsageHoldState.COMMITTED)

    async def release(self, hold_id: UUID) -> dict | None:
        return await self.store.settle_hold(hold_id, UsageHoldState.RELEASED)
