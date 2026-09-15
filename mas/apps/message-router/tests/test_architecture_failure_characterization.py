"""Redis atomicity characterization and regression tests.

These tests pin the message-router's Redis boundary: publication and reclaim
requeue use one Redis-side operation, while compatibility helpers remain
available for older direct callers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.protocols.envelope import MessageEnvelope


def _envelope(*, retry_count: int = 0) -> MessageEnvelope:
    return MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="ceo-agent",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team="dept_production",
        project_id="project-1",
        retry_count=retry_count,
    )


@pytest.mark.asyncio
async def test_publish_uses_one_atomic_redis_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from message_router import routes_publish

    envelope = _envelope()
    calls: list[tuple[str, dict[str, str], str]] = []

    async def atomic_publish(
        team_id: str,
        fields: dict[str, str],
        message_id: str,
    ) -> tuple[str, bool]:
        calls.append((team_id, fields, message_id))
        return "1700000000000-0", False

    monkeypatch.setattr(routes_publish, "atomic_publish_message", atomic_publish)

    result = await routes_publish.publish_message(envelope, "test-publisher")

    assert result.entry_id == "1700000000000-0"
    assert calls[0][0] == "dept_production"
    assert calls[0][1].keys() == {"envelope"}
    assert calls[0][2] == str(envelope.message_id)


@pytest.mark.asyncio
async def test_atomic_publish_failure_has_no_follow_up_cleanup_window() -> None:
    """A Redis script failure cannot leave a client-side cleanup sequence."""

    from message_router.redis_client import atomic_publish_message

    redis = MagicMock()
    redis.eval = AsyncMock(side_effect=RuntimeError("Redis script failure"))

    with pytest.raises(RuntimeError, match="Redis script failure"):
        await atomic_publish_message(
            "dept_production",
            {"envelope": _envelope().model_dump_json()},
            str(_envelope().message_id),
            redis=redis,
        )

    redis.eval.assert_awaited_once()
    redis.delete.assert_not_called()


@pytest.mark.asyncio
async def test_atomic_publish_decodes_new_and_duplicate_results() -> None:
    from message_router.redis_client import atomic_publish_message

    redis = MagicMock()
    redis.eval = AsyncMock(side_effect=[[1, "1-0"], [0, "1-0"]])
    fields = {"envelope": _envelope().model_dump_json()}
    message_id = str(_envelope().message_id)

    assert await atomic_publish_message("dept_production", fields, message_id, redis=redis) == (
        "1-0",
        False,
    )
    assert await atomic_publish_message("dept_production", fields, message_id, redis=redis) == (
        "1-0",
        True,
    )
    assert redis.eval.await_count == 2
    script, numkeys, stream, dedupe, *_ = redis.eval.await_args_list[0].args
    assert numkeys == 2
    assert stream == "stream:dept_production"
    assert dedupe.startswith("dedupe:")
    assert "XADD" in script
    assert "SET" in script


@pytest.mark.asyncio
async def test_reclaim_uses_one_atomic_requeue_operation() -> None:
    from message_router.tasks import _handle_reclaimed_entry

    requeue = AsyncMock(return_value=("2-0", False))
    xadd = AsyncMock()
    xack = AsyncMock()
    xdel = AsyncMock()

    await _handle_reclaimed_entry(
        team_id="dept_production",
        entry_id="1-0",
        fields={"envelope": _envelope().model_dump_json()},
        redis=MagicMock(),
        write_dead_letter=AsyncMock(),
        make_dlq_system_event_fields=MagicMock(return_value={}),
        xack=xack,
        xdel=xdel,
        xadd_message=xadd,
        requeue_message=requeue,
    )

    requeue.assert_awaited_once()
    assert requeue.await_args.kwargs["team_id"] == "dept_production"
    assert requeue.await_args.kwargs["entry_id"] == "1-0"
    updated = requeue.await_args.kwargs["fields"]
    assert MessageEnvelope.model_validate_json(updated["envelope"]).retry_count == 1
    xadd.assert_not_awaited()
    xack.assert_not_awaited()
    xdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_atomic_requeue_failure_leaves_primitives_uninvoked() -> None:
    from message_router.tasks import _handle_reclaimed_entry

    requeue = AsyncMock(side_effect=RuntimeError("Redis requeue script failure"))
    xadd = AsyncMock()
    xack = AsyncMock()
    xdel = AsyncMock()
    await _handle_reclaimed_entry(
        team_id="dept_production",
        entry_id="1-0",
        fields={"envelope": _envelope().model_dump_json()},
        redis=MagicMock(),
        write_dead_letter=AsyncMock(),
        make_dlq_system_event_fields=MagicMock(return_value={}),
        xack=xack,
        xdel=xdel,
        xadd_message=xadd,
        requeue_message=requeue,
    )

    requeue.assert_awaited_once()
    xadd.assert_not_awaited()
    xack.assert_not_awaited()
    xdel.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomic_requeue_script_carries_replacement_and_cleanup() -> None:
    from message_router.redis_client import atomic_requeue_message

    redis = MagicMock()
    redis.eval = AsyncMock(return_value=[1, "2-0"])
    fields = {"envelope": _envelope(retry_count=1).model_dump_json()}

    assert await atomic_requeue_message(
        "dept_production",
        "1-0",
        fields,
        redis=redis,
    ) == ("2-0", False)

    script, numkeys, stream, requeue_key, ttl, field_count, group, old_entry, *_ = (
        redis.eval.await_args.args
    )
    assert numkeys == 2
    assert stream == "stream:dept_production"
    assert requeue_key == "dedupe:requeue:dept_production:1-0"
    assert int(ttl) == 86_400
    assert field_count == "1"
    assert group == "group:dept_production"
    assert old_entry == "1-0"
    assert "XADD" in script
    assert "XACK" in script
    assert "XDEL" in script
