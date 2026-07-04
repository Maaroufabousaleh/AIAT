"""Redis connection management for the message-router.

Provides:
- A lazily-initialised async Redis client (``redis.asyncio``) shared across
  the whole application.
- Stream / consumer-group helpers (create, trim, reclaim).
- Publish-side idempotency (dedupe key TTL).

All stream key names follow the convention ``stream:{team_id}``.
All consumer-group names follow ``group:{team_id}``.
All dedupe keys follow ``dedupe:{message_id}``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import redis.asyncio as aioredis
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .config import settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Module-level Redis client — set during lifespan startup.
_redis_client: Redis | None = None


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


async def connect_redis() -> Redis:
    """Create and store the shared Redis connection pool.

    Called once from the FastAPI lifespan startup handler.
    """
    global _redis_client
    client: Redis = aioredis.from_url(
        settings.redis_url,
        username=settings.redis_username,
        password=settings.redis_password,
        decode_responses=True,
        encoding="utf-8",
        socket_timeout=max(10, settings.read_block_ms / 1000 + 5),
    )
    await client.ping()
    _redis_client = client
    redis_target = urlparse(settings.redis_url)
    logger.info(
        "Redis connected: %s/%s",
        redis_target.hostname or "<unknown>",
        redis_target.path.lstrip("/") or "0",
    )
    return client


async def close_redis() -> None:
    """Close the Redis connection pool.  Called from lifespan shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed.")


def get_redis() -> Redis:
    """Return the shared Redis client.  Raises RuntimeError if not connected."""
    if _redis_client is None:
        raise RuntimeError("Redis is not connected.  Call connect_redis() first.")
    return _redis_client


# ---------------------------------------------------------------------------
# Stream / consumer-group helpers
# ---------------------------------------------------------------------------


def stream_key(team_id: str) -> str:
    """Return the Redis stream key for a team (e.g. ``stream:exec_ceo``)."""
    return f"{settings.stream_prefix}:{team_id}"


def group_name(team_id: str) -> str:
    """Return the consumer-group name for a team (e.g. ``group:exec_ceo``)."""
    return f"{settings.group_prefix}:{team_id}"


def dedupe_key(message_id: str) -> str:
    """Return the Redis key used for publish-side idempotency."""
    return f"{settings.dedupe_prefix}:{message_id}"


async def ensure_consumer_group(team_id: str, redis: Redis | None = None) -> None:
    """Create the consumer group for *team_id* if it does not already exist.

    Uses ``XGROUP CREATE … MKSTREAM`` so the stream itself is created on first
    use.  Idempotent: the ``BUSYGROUP`` ResponseError is silently swallowed.
    """
    r = redis or get_redis()
    key = stream_key(team_id)
    grp = group_name(team_id)
    try:
        await r.xgroup_create(key, grp, id="$", mkstream=True)
        logger.debug("Consumer group created: stream=%s group=%s", key, grp)
    except ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            pass  # Already exists — fine
        else:
            raise


async def ensure_all_consumer_groups(redis: Redis | None = None) -> None:
    """Create consumer groups for all 11 known teams."""
    r = redis or get_redis()
    for team_id in settings.known_teams:
        await ensure_consumer_group(team_id, r)
    logger.info("Consumer groups ensured for %d teams.", len(settings.known_teams))


# ---------------------------------------------------------------------------
# XADD helper
# ---------------------------------------------------------------------------


async def xadd_message(
    team_id: str,
    fields: dict[str, str],
    redis: Redis | None = None,
) -> str:
    """Append *fields* to the team stream and return the Redis entry ID."""
    r = redis or get_redis()
    entry_id: str = await r.xadd(stream_key(team_id), fields)  # type: ignore[assignment]
    return entry_id


# ---------------------------------------------------------------------------
# Publish-side idempotency (dedupe key)
# ---------------------------------------------------------------------------


async def check_and_set_dedupe(
    message_id: str,
    entry_id: str,
    redis: Redis | None = None,
) -> str | None:
    """Atomically check for an existing dedupe key, set it if absent.

    Returns
    -------
    ``None``
        First time we see this *message_id* — the caller should XADD.
    ``str``
        The previously stored *entry_id* — this is a duplicate; skip XADD.
    """
    r = redis or get_redis()
    key = dedupe_key(message_id)
    # SET key value EX ttl NX — only sets if key does NOT exist.
    result = await r.set(key, entry_id, ex=settings.dedupe_ttl_seconds, nx=True)
    if result:
        # Key was set — first publish.
        return None
    # Key already existed — return the original entry_id.
    existing: str | None = await r.get(key)  # type: ignore[assignment]
    return existing


async def wait_for_dedupe_resolution(
    message_id: str,
    *,
    pending_value: str = "_pending_",
    timeout_ms: int = 2_000,
    poll_ms: int = 20,
    redis: Redis | None = None,
) -> str | None:
    """Wait for a pending dedupe key to resolve to a concrete stream entry id.

    Returns the resolved entry id, or ``None`` if the key remains pending past
    ``timeout_ms`` or disappears.
    """
    r = redis or get_redis()
    key = dedupe_key(message_id)
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)

    while True:
        value: str | None = await r.get(key)  # type: ignore[assignment]
        if value is None:
            return None
        if value != pending_value:
            return value
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(poll_ms / 1000.0)


# ---------------------------------------------------------------------------
# XAUTOCLAIM — reclaim idle PEL entries
# ---------------------------------------------------------------------------


async def reclaim_idle_messages(
    team_id: str,
    consumer_id: str = "reclaimer",
    redis: Redis | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """Run XAUTOCLAIM on the team stream and return reclaimed entries.

    Each entry is ``(entry_id, fields_dict)``.  Entries that have exceeded
    ``settings.max_delivery_attempts`` are returned here for DLQ processing
    by the caller.

    XAUTOCLAIM was introduced in Redis 6.2 — it atomically claims messages
    idle > ``settings.reclaim_idle_ms`` and reassigns them to *consumer_id*.
    """
    r = redis or get_redis()
    key = stream_key(team_id)
    grp = group_name(team_id)
    # Returns: [next_start_id, [[id, fields], ...], [deleted_ids]]
    result: Any = await r.xautoclaim(
        key,
        grp,
        consumer_id,
        min_idle_time=settings.reclaim_idle_ms,
        start_id="0-0",
        count=100,
    )
    # result[1] is the list of reclaimed entries
    reclaimed: list[tuple[str, dict[str, str]]] = []
    if result and len(result) > 1 and result[1]:
        for entry in result[1]:
            entry_id, fields = entry
            reclaimed.append((entry_id, fields))
    return reclaimed


# ---------------------------------------------------------------------------
# Stream trimming
# ---------------------------------------------------------------------------


async def trim_all_streams(redis: Redis | None = None) -> None:
    """Run ``XTRIM … MAXLEN ~ <max_len>`` on every known team stream."""
    r = redis or get_redis()
    for team_id in settings.known_teams:
        key = stream_key(team_id)
        await r.xtrim(key, maxlen=settings.stream_max_len, approximate=True)
    logger.debug("Stream trim complete (maxlen~%d).", settings.stream_max_len)


# ---------------------------------------------------------------------------
# XACK / XDEL helpers
# ---------------------------------------------------------------------------


async def xack(team_id: str, entry_id: str, redis: Redis | None = None) -> None:
    """Acknowledge a message — removes it from the PEL."""
    r = redis or get_redis()
    await r.xack(stream_key(team_id), group_name(team_id), entry_id)


async def xdel(team_id: str, entry_id: str, redis: Redis | None = None) -> None:
    """Hard-delete a message from the stream (used after DLQ insert)."""
    r = redis or get_redis()
    await r.xdel(stream_key(team_id), entry_id)


# ---------------------------------------------------------------------------
# XREADGROUP — read pending + new messages
# ---------------------------------------------------------------------------


async def xreadgroup_pending(
    team_id: str,
    consumer_id: str,
    redis: Redis | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """Read all messages currently in the PEL for *consumer_id* (start_id='0').

    Used on agent reconnect to replay in-flight messages that were not ACKed
    before the previous disconnect.
    """
    r = redis or get_redis()
    key = stream_key(team_id)
    grp = group_name(team_id)
    result: Any = await r.xreadgroup(
        grp,
        consumer_id,
        {key: "0"},
        count=settings.read_count,
    )
    entries: list[tuple[str, dict[str, str]]] = []
    if result:
        for _stream, messages in result:
            for entry_id, fields in messages:
                entries.append((entry_id, fields))
    return entries


async def xreadgroup_new(
    team_id: str,
    consumer_id: str,
    block_ms: int | None = None,
    redis: Redis | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """Read new (un-delivered) messages from the team stream (start_id='>').

    Blocks up to *block_ms* milliseconds waiting for messages.
    """
    r = redis or get_redis()
    key = stream_key(team_id)
    grp = group_name(team_id)
    block = block_ms if block_ms is not None else settings.read_block_ms
    try:
        result: Any = await r.xreadgroup(
            grp,
            consumer_id,
            {key: ">"},
            count=settings.read_count,
            block=block,
        )
    except RedisTimeoutError:
        return []
    entries: list[tuple[str, dict[str, str]]] = []
    if result:
        for _stream, messages in result:
            for entry_id, fields in messages:
                entries.append((entry_id, fields))
    return entries
