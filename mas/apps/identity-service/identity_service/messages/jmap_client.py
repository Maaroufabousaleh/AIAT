"""Small ownership-agnostic wrapper around the Stalwart JMAP adapter."""

from __future__ import annotations

from typing import Any

from ..providers.stalwart import StalwartAdapter


class JmapClient:
    def __init__(self, provider: StalwartAdapter) -> None:
        self.provider = provider

    async def list(self, account_id: str, *, limit: int, query: str | None = None) -> dict[str, Any]:
        return await self.provider.list_messages(account_id, limit=limit, query=query)

    async def read(self, account_id: str, message_id: str) -> dict[str, Any]:
        return await self.provider.read_message(account_id, message_id)
