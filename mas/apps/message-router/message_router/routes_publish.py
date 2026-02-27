"""HTTP routes for the message-router — publish endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from mas_core.protocols.envelope import MessageEnvelope
from mas_core.policy.engine import CommunicationPolicy

from .config import settings
from .redis_client import (
    check_and_set_dedupe,
    dedupe_key,
    get_redis,
    wait_for_dedupe_resolution,
    xadd_message,
)

logger = logging.getLogger(__name__)

router = APIRouter()
policy = CommunicationPolicy()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PublishResponse(BaseModel):
    """Returned by POST /messages/publish."""

    entry_id: str
    deduplicated: bool = False


# ---------------------------------------------------------------------------
# Publish endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/messages/publish",
    response_model=PublishResponse,
    status_code=status.HTTP_200_OK,
    summary="Publish a MessageEnvelope to a team stream",
    description=(
        "Validates the envelope against CommunicationPolicy, checks publish-side "
        "idempotency (dedupe:{message_id} in Redis, 300 s TTL), and enqueues to "
        "stream:{recipient_team} via XADD.  Returns the Redis stream entry ID."
    ),
)
async def publish_message(envelope: MessageEnvelope) -> PublishResponse:
    """Publish a validated MessageEnvelope to the target team's Redis stream."""

    # ── 1. Policy check ───────────────────────────────────────────────────────
    policy_result = policy.can(
        sender_role=envelope.sender_role,
        sender_team=envelope.sender_team,
        recipient_id=envelope.recipient_id,
        recipient_team=envelope.recipient_team,
        msg_type=envelope.msg_type,
    )
    if policy_result is not True:
        logger.warning(
            "Policy denied publish: sender=%s role=%s → team=%s type=%s reason=%s",
            envelope.sender_id,
            envelope.sender_role,
            envelope.recipient_team,
            envelope.msg_type,
            policy_result,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(policy_result),
        )

    # ── 2. Resolve target team ─────────────────────────────────────────────────
    target_team = envelope.recipient_team
    if target_team is None:
        # Intra-team message: deliver to the sender's own stream.
        target_team = envelope.sender_team

    if target_team not in settings.known_teams:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown recipient_team: {target_team!r}",
        )

    # ── 3. TTL check ──────────────────────────────────────────────────────────
    if envelope.is_expired():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message has already exceeded its TTL; rejected.",
        )

    # ── 4. Publish-side idempotency ───────────────────────────────────────────
    message_id_str = str(envelope.message_id)
    # We first optimistically XADD, then store dedupe key.
    # Because Redis is single-threaded we use SET NX with a sentinel.
    pending_marker = "_pending_"
    existing_entry_id = await check_and_set_dedupe(message_id_str, pending_marker)
    if existing_entry_id is not None:
        if existing_entry_id == pending_marker:
            resolved_entry_id = await wait_for_dedupe_resolution(
                message_id_str,
                pending_value=pending_marker,
            )
            if resolved_entry_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Duplicate publish already in progress; retry shortly.",
                )
            logger.debug(
                "Duplicate publish resolved after pending marker: message_id=%s original_entry=%s",
                message_id_str,
                resolved_entry_id,
            )
            return PublishResponse(entry_id=resolved_entry_id, deduplicated=True)
        logger.debug(
            "Duplicate publish ignored: message_id=%s original_entry=%s",
            message_id_str,
            existing_entry_id,
        )
        return PublishResponse(entry_id=existing_entry_id, deduplicated=True)

    # ── 5. XADD to team stream ─────────────────────────────────────────────────
    fields = {"envelope": envelope.model_dump_json()}
    try:
        entry_id = await xadd_message(target_team, fields)
    except Exception:
        # Clear pending marker so retries can proceed after a failed XADD.
        redis = get_redis()
        await redis.delete(dedupe_key(message_id_str))
        raise

    # ── 6. Update dedupe key with real entry_id ────────────────────────────────
    # The initial SET NX stored "_pending_"; overwrite with real entry_id.
    redis = get_redis()
    await redis.set(
        f"{settings.dedupe_prefix}:{message_id_str}",
        entry_id,
        ex=settings.dedupe_ttl_seconds,
    )

    logger.debug(
        "Published: message_id=%s team=%s type=%s entry_id=%s",
        message_id_str,
        target_team,
        envelope.msg_type,
        entry_id,
    )
    return PublishResponse(entry_id=entry_id)


# ---------------------------------------------------------------------------
# Broadcast endpoint
# ---------------------------------------------------------------------------


class BroadcastResponse(BaseModel):
    """Returned by POST /messages/broadcast."""

    entry_ids: dict[str, str]  # team_id → stream entry_id


@router.post(
    "/messages/broadcast",
    response_model=BroadcastResponse,
    status_code=status.HTTP_200_OK,
    summary="Broadcast a MessageEnvelope to all team streams",
    description=(
        "Same policy validation as publish, but enqueues to ALL 11 team streams.  "
        "Used for SHUTDOWN broadcasts and system-wide announcements.  "
        "Only orchestrator and executive roles may broadcast."
    ),
)
async def broadcast_message(envelope: MessageEnvelope) -> BroadcastResponse:
    """Broadcast an envelope to every known team stream."""
    from mas_core.protocols.enums import MessageType

    # ── 1. Policy check ───────────────────────────────────────────────────────
    policy_result = policy.can(
        sender_role=envelope.sender_role,
        sender_team=envelope.sender_team,
        recipient_id=envelope.recipient_id,
        recipient_team=envelope.recipient_team,
        msg_type=envelope.msg_type,
    )
    if policy_result is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(policy_result),
        )

    # ── 2. Fan-out to all teams ────────────────────────────────────────────────
    entry_ids: dict[str, str] = {}
    message_id_str = str(envelope.message_id)

    for team_id in settings.known_teams:
        # Build a per-team copy with recipient_team set
        per_team = envelope.model_copy(update={"recipient_team": team_id})
        fields = {"envelope": per_team.model_dump_json()}
        eid = await xadd_message(team_id, fields)
        entry_ids[team_id] = eid

    logger.info(
        "Broadcast: message_id=%s type=%s → %d streams",
        message_id_str,
        envelope.msg_type,
        len(entry_ids),
    )
    return BroadcastResponse(entry_ids=entry_ids)
