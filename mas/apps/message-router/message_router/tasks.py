"""Background tasks for the message-router.

Two long-running asyncio tasks:

1. **reclaim_loop** — Runs every ``settings.reclaim_interval_seconds``.
   Calls ``XAUTOCLAIM`` on every team stream.  For each reclaimed entry:
   - Increments ``retry_count`` in the envelope.
   - If ``retry_count >= max_delivery_attempts`` OR the message has expired
     (TTL elapsed), writes to the ``dead_letters`` Postgres table, ACKs + DELs
     the stream entry, and publishes a ``SYSTEM_EVENT`` DLQ notification to
     ``stream:exec_ceo``.
   - Otherwise, the re-claimed entry stays in the stream and will be
     re-delivered to a connecting subscriber.

2. **trim_loop** — Runs every ``settings.trim_interval_seconds``.
   Calls ``XTRIM … MAXLEN ~ 50000`` on every known team stream to prevent
   unbounded Redis memory growth.
"""

from __future__ import annotations

import asyncio
import logging

from .config import settings

logger = logging.getLogger(__name__)


async def reclaim_loop() -> None:
    """Background task: XAUTOCLAIM idle PEL entries every N seconds."""
    from .dlq import make_dlq_system_event_fields, write_dead_letter
    from .redis_client import (
        get_redis,
        reclaim_idle_messages,
        xack,
        xadd_message,
        xdel,
    )

    logger.info("Reclaim loop started (interval=%ds).", settings.reclaim_interval_seconds)

    while True:
        await asyncio.sleep(settings.reclaim_interval_seconds)
        try:
            redis = get_redis()
            for team_id in settings.known_teams:
                try:
                    entries = await reclaim_idle_messages(team_id)
                    if not entries:
                        continue

                    for entry_id, fields in entries:
                        await _handle_reclaimed_entry(
                            team_id,
                            entry_id,
                            fields,
                            redis,
                            write_dead_letter,
                            make_dlq_system_event_fields,
                            xack,
                            xdel,
                            xadd_message,
                        )
                except Exception:
                    logger.exception("Reclaim error for team=%s", team_id)
        except Exception:
            logger.exception("Reclaim loop iteration error")


async def _handle_reclaimed_entry(
    team_id: str,
    entry_id: str,
    fields: dict[str, str],
    redis,
    write_dead_letter,
    make_dlq_system_event_fields,
    xack,
    xdel,
    xadd_message,
) -> None:
    """Process a single reclaimed PEL entry."""
    from mas_core.protocols.envelope import MessageEnvelope

    envelope_json = fields.get("envelope", "")
    if not envelope_json:
        logger.warning(
            "Reclaimed entry has no 'envelope' field: team=%s entry_id=%s",
            team_id,
            entry_id,
        )
        await xack(team_id, entry_id, redis)
        return

    try:
        envelope = MessageEnvelope.model_validate_json(envelope_json)
    except Exception:
        logger.exception(
            "Failed to parse envelope from reclaimed entry: team=%s entry_id=%s",
            team_id,
            entry_id,
        )
        await xack(team_id, entry_id, redis)
        return

    new_retry_count = envelope.retry_count + 1
    envelope = envelope.model_copy(update={"retry_count": new_retry_count})

    is_exhausted = new_retry_count >= settings.max_delivery_attempts
    is_expired = envelope.is_expired()

    if is_exhausted or is_expired:
        reason = "max_attempts_exceeded" if is_exhausted else "ttl_expired"
        logger.warning(
            "DLQ: message_id=%s team=%s reason=%s retries=%d",
            envelope.message_id,
            team_id,
            reason,
            new_retry_count,
        )
        try:
            dlq_id = await write_dead_letter(
                message_id=str(envelope.message_id),
                team_id=team_id,
                entry_id=entry_id,
                envelope_json=envelope_json,
                reason=reason,
                retry_count=new_retry_count,
            )
            await xack(team_id, entry_id, redis)
            await xdel(team_id, entry_id, redis)
            notify_fields = make_dlq_system_event_fields(
                dlq_id=dlq_id,
                message_id=str(envelope.message_id),
                team_id=team_id,
                reason=reason,
            )
            await xadd_message("exec_ceo", notify_fields, redis)
        except Exception:
            logger.exception("Failed to process DLQ for message_id=%s", envelope.message_id)
    else:
        updated_fields = {"envelope": envelope.model_dump_json()}
        try:
            new_entry_id = await xadd_message(team_id, updated_fields, redis)
            await xack(team_id, entry_id, redis)
            await xdel(team_id, entry_id, redis)
            logger.debug(
                "Re-queued reclaimed message: message_id=%s team=%s retry=%d new_entry=%s",
                envelope.message_id,
                team_id,
                new_retry_count,
                new_entry_id,
            )
        except Exception:
            logger.exception(
                "Failed to re-queue reclaimed message: message_id=%s", envelope.message_id
            )


async def trim_loop() -> None:
    """Background task: XTRIM all team streams every N seconds."""
    from .redis_client import trim_all_streams

    logger.info("Trim loop started (interval=%ds).", settings.trim_interval_seconds)

    while True:
        await asyncio.sleep(settings.trim_interval_seconds)
        try:
            await trim_all_streams()
        except Exception:
            logger.exception("Stream trim loop error")
