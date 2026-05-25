"""HTTP routes for the message-router — publish endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from mas_core.observability.metrics import MAS_MESSAGES_TOTAL, MESSAGES_PUBLISHED_TOTAL
from mas_core.observability.tracing import bind_trace_id
from mas_core.policy.engine import CommunicationPolicy
from mas_core.protocols.envelope import MessageEnvelope

from .config import settings
from .redis_client import (
    check_and_set_dedupe,
    dedupe_key,
    get_redis,
    wait_for_dedupe_resolution,
    xadd_message,
)

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()
policy = CommunicationPolicy()


class PublishResponse(BaseModel):
    """Returned by POST /messages/publish."""

    entry_id: str
    deduplicated: bool = False


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

    target_team = envelope.recipient_team
    if target_team is None:
        target_team = envelope.sender_team

    if target_team not in settings.known_teams:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown recipient_team: {target_team!r}",
        )

    if envelope.is_expired():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message has already exceeded its TTL; rejected.",
        )

    if envelope.correlation_id:
        bind_trace_id(str(envelope.correlation_id))

    message_id_str = str(envelope.message_id)
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

    fields = {"envelope": envelope.model_dump_json()}
    try:
        entry_id = await xadd_message(target_team, fields)
    except Exception:
        redis = get_redis()
        await redis.delete(dedupe_key(message_id_str))
        raise

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

    MAS_MESSAGES_TOTAL.labels(
        direction="outbound",
        team=target_team,
        msg_type=str(envelope.msg_type),
    ).inc()
    MESSAGES_PUBLISHED_TOTAL.labels(team=target_team).inc()

    return PublishResponse(entry_id=entry_id)


@router.post(
    "/publish",
    response_model=PublishResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def publish_message_compat(envelope: MessageEnvelope) -> PublishResponse:
    """Compatibility alias matching the shorter Phase 3 route from the plan."""
    return await publish_message(envelope)


class BroadcastResponse(BaseModel):
    """Returned by POST /messages/broadcast."""

    entry_ids: dict[str, str]


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

    entry_ids: dict[str, str] = {}
    message_id_str = str(envelope.message_id)

    for team_id in settings.known_teams:
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

    for team_id in entry_ids:
        MAS_MESSAGES_TOTAL.labels(
            direction="broadcast",
            team=team_id,
            msg_type=str(envelope.msg_type),
        ).inc()
        MESSAGES_PUBLISHED_TOTAL.labels(team=team_id).inc()

    return BroadcastResponse(entry_ids=entry_ids)


@router.post(
    "/broadcast",
    response_model=BroadcastResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def broadcast_message_compat(envelope: MessageEnvelope) -> BroadcastResponse:
    """Compatibility alias matching the shorter Phase 3 route from the plan."""
    return await broadcast_message(envelope)
