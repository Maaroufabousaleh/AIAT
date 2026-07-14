"""Dead-Letter Queue (DLQ) writer — Postgres ``dead_letters`` table.

When a message exhausts its delivery attempts (``retry_count >= max_delivery_attempts``)
or has expired (``ttl_seconds`` elapsed), the router:
  1. Writes a row to ``dead_letters`` via this module.
  2. Calls ``XACK`` + ``XDEL`` to remove the entry from the stream.
  3. Publishes a ``SYSTEM_EVENT { event: "DLQ_ENTRY" }`` to ``stream:exec_ceo``
     so the CEO is notified.

``statement_cache_size=0`` is mandatory for PgBouncer transaction pooling compatibility.
"""

from __future__ import annotations

import logging
import json
import uuid
from datetime import UTC, datetime

import asyncpg  # type: ignore[import-untyped]

from .config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return (or lazily create) the asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.postgres_dsn,
            min_size=1,
            max_size=5,
            statement_cache_size=0,
        )
        logger.info("DLQ Postgres pool created.")
    return _pool


async def close_pool() -> None:
    """Close the asyncpg pool.  Called from lifespan shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("DLQ Postgres pool closed.")


async def write_dead_letter(
    message_id: str,
    team_id: str,
    entry_id: str,
    envelope_json: str,
    reason: str,
    retry_count: int,
) -> str:
    """Insert a message into the ``dead_letters`` table.

    Parameters
    ----------
    message_id:    ``MessageEnvelope.message_id`` (UUID string).
    team_id:       Target team the message was routed to.
    entry_id:      Redis stream entry ID (for forensic lookup).
    envelope_json: Full serialised ``MessageEnvelope`` as a JSON string.
    reason:        Human-readable DLQ reason (e.g. "max_attempts_exceeded").
    retry_count:   Delivery attempt count at time of DLQ.

    Returns the new auto-incremented ``dead_letters.id`` as a string.
    """
    pool = await get_pool()
    envelope = json.loads(envelope_json)
    project_id: uuid.UUID | None = None
    if envelope.get("project_id"):
        try:
            project_id = uuid.UUID(str(envelope["project_id"]))
        except ValueError:
            logger.warning("DLQ envelope has non-UUID project_id; storing NULL")
    now = datetime.now(tz=UTC)

    try:
        dlq_id = await pool.fetchval(
            """
            INSERT INTO dead_letters
                (message_id, recipient_team, sender_id, msg_type, project_id,
                 retry_count, failure_reason, envelope_json, dead_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
            RETURNING id
            """,
            message_id,
            team_id,
            envelope.get("sender_id"),
            envelope.get("msg_type"),
            project_id,
            retry_count,
            reason,
            envelope_json,
            now,
        )
        if dlq_id is None:
            raise RuntimeError("dead-letter INSERT returned no id")
        logger.warning(
            "DLQ entry written: message_id=%s team=%s reason=%s retries=%d",
            message_id,
            team_id,
            reason,
            retry_count,
        )
    except Exception:
        logger.exception("Failed to write DLQ entry for message_id=%s", message_id)
        raise

    return str(dlq_id)


def make_dlq_system_event_fields(
    dlq_id: str,
    message_id: str,
    team_id: str,
    reason: str,
) -> dict[str, str]:
    """Build Redis stream fields for a DLQ_ENTRY SYSTEM_EVENT notification.

    Published to ``stream:exec_ceo`` after a dead-letter is written so the CEO
    agent can inspect the DLQ and take corrective action.
    """
    from mas_core.protocols.enums import AgentRole, MessageType
    from mas_core.protocols.envelope import MessageEnvelope

    notification = MessageEnvelope(
        msg_type=MessageType.SYSTEM_EVENT,
        sender_id="message-router",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team="exec_ceo",
        payload={
            "event": "DLQ_ENTRY",
            "dlq_id": dlq_id,
            "original_message_id": message_id,
            "team_id": team_id,
            "reason": reason,
        },
    )
    return {"envelope": notification.model_dump_json()}
