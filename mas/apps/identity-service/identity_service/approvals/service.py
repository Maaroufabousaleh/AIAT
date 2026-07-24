"""Durable approval workflow helper."""

from __future__ import annotations

from uuid import UUID

from ..models import ApprovalState
from ..store import IdentityStore


class ApprovalService:
    def __init__(self, store: IdentityStore) -> None:
        self.store = store

    async def request(self, *, worker_id: UUID, kind: str, target_id: UUID, idempotency_key: str) -> dict:
        return await self.store.create_approval(worker_id=worker_id, kind=kind, target_id=target_id, idempotency_key=idempotency_key)

    async def decide(self, approval_id: UUID, *, actor_id: str, approved: bool, reason: str) -> dict | None:
        return await self.store.decide_approval(approval_id, ApprovalState.APPROVED if approved else ApprovalState.REJECTED, actor_id, reason)
