"""Durable per-worker tool grants.

The registry keeps an in-memory copy for the hot path, while this store is
the source of truth across tool-service restarts.  The grant API writes here
before changing the live allowlist so a successful response is never merely
ephemeral in a production deployment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import asyncpg


class ToolGrantStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> ToolGrantStore:
        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        pool = await asyncpg.create_pool(
            normalized, min_size=1, max_size=2, statement_cache_size=0
        )
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def healthcheck(self) -> bool:
        return bool(await self._pool.fetchval("SELECT 1"))

    async def load_all(self) -> dict[str, set[str]]:
        rows = await self._pool.fetch(
            "SELECT worker_id, tool_name FROM worker_tool_grants ORDER BY worker_id, tool_name"
        )
        grants: dict[str, set[str]] = {}
        for row in rows:
            grants.setdefault(str(row["worker_id"]), set()).add(str(row["tool_name"]))
        return grants

    async def grant(self, worker_id: str, tool_name: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO worker_tool_grants (worker_id, tool_name)
            VALUES ($1, $2)
            ON CONFLICT (worker_id, tool_name) DO NOTHING
            """,
            worker_id,
            tool_name,
        )

    async def revoke(self, worker_id: str, tool_name: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM worker_tool_grants WHERE worker_id = $1 AND tool_name = $2",
            worker_id,
            tool_name,
        )
        return result.endswith(" 1")

    async def revoke_identity_grants(self, worker_id: str) -> int:
        result = await self._pool.execute(
            """DELETE FROM worker_tool_grants
               WHERE worker_id = $1
                 AND (tool_name LIKE 'identity.%' OR tool_name LIKE 'mail.%')""",
            worker_id,
        )
        return int(result.rsplit(" ", 1)[-1])

    async def ensure_browser_identity(self, worker_id: str) -> dict[str, str]:
        namespace_ref = "worker-" + uuid5(NAMESPACE_URL, worker_id).hex
        row = await self._pool.fetchrow(
            """INSERT INTO worker_browser_identities
                 (worker_id, namespace_ref, state)
               VALUES ($1, $2, 'ACTIVE')
               ON CONFLICT (worker_id) DO UPDATE
               SET state = 'ACTIVE', updated_at = now()
               RETURNING worker_id, namespace_ref, state""",
            worker_id,
            namespace_ref,
        )
        if row is None:
            raise RuntimeError("browser identity persistence failed")
        return dict(row)

    async def revoke_browser_identity(self, worker_id: str, *, retired: bool) -> dict[str, str] | None:
        row = await self._pool.fetchrow(
            """UPDATE worker_browser_identities
               SET state = $2, updated_at = now()
               WHERE worker_id = $1
               RETURNING worker_id, namespace_ref, state""",
            worker_id,
            "RETIRED" if retired else "SUSPENDED",
        )
        return dict(row) if row else None

    async def consume_signature_nonce(
        self, client_id: str, nonce: str, expires_at_epoch: int
    ) -> bool:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM tool_signature_nonces WHERE expires_at <= now()"
                )
                row = await conn.fetchrow(
                    """INSERT INTO tool_signature_nonces
                         (client_id, nonce, expires_at)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (client_id, nonce) DO NOTHING
                       RETURNING nonce""",
                    client_id,
                    nonce,
                    datetime.fromtimestamp(expires_at_epoch, tz=UTC),
                )
        return row is not None
