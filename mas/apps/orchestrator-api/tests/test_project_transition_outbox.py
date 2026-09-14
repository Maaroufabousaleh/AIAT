"""Tests for durable project-transition notification delivery."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

PROJECT_ID = "00000000-0000-4000-a000-000000000302"


@pytest.mark.asyncio
async def test_publish_system_event_uses_stable_transition_id_and_reports_acceptance() -> None:
    from orchestrator_api import main

    transition_id = uuid4()
    with patch(
        "orchestrator_api.main._publish_router_envelope",
        new=AsyncMock(return_value=True),
    ) as publish:
        delivered = await main.publish_system_event(
            PROJECT_ID,
            "INIT",
            "FEASIBILITY_CHECK",
            "project_created",
            "orchestrator",
            {"source": "test"},
            transition_id=transition_id,
            include_stage_directive=False,
        )

    assert delivered is True
    envelope = publish.await_args.args[0]
    assert envelope["message_id"] == str(transition_id)
    assert envelope["msg_type"] == "SYSTEM_EVENT"
    assert publish.await_count == 1


@pytest.mark.asyncio
async def test_publish_system_event_reports_router_rejection_without_claiming_delivery() -> None:
    from orchestrator_api import main

    with patch(
        "orchestrator_api.main._publish_router_envelope",
        new=AsyncMock(return_value=False),
    ):
        delivered = await main.publish_system_event(
            PROJECT_ID,
            "INIT",
            "FEASIBILITY_CHECK",
            "project_created",
            "orchestrator",
            {},
            transition_id=uuid4(),
            include_stage_directive=False,
        )

    assert delivered is False


class _OutboxStorage:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.claimed: list[UUID] = []
        self.marked: list[tuple[UUID, dict[str, object]]] = []

    async def list_project_transition_outbox(self, *, limit: int) -> list[dict[str, object]]:
        assert limit == 100
        return [self.row]

    async def claim_project_transition_outbox(self, outbox_id: UUID) -> dict[str, object] | None:
        self.claimed.append(outbox_id)
        return self.row

    async def mark_project_transition_outbox(
        self,
        outbox_id: UUID,
        **kwargs: object,
    ) -> dict[str, object]:
        self.marked.append((outbox_id, kwargs))
        return {**self.row, **kwargs}


@pytest.mark.asyncio
async def test_drain_project_transition_outbox_replays_only_system_event_and_marks_success() -> None:
    from orchestrator_api import main

    outbox_id = uuid4()
    storage = _OutboxStorage(
        {
            "id": outbox_id,
            "project_id": UUID(PROJECT_ID),
            "from_state": "INIT",
            "to_state": "FEASIBILITY_CHECK",
            "event": "project_created",
            "triggered_by": "orchestrator",
            "payload": {"source": "outbox"},
        }
    )
    with patch(
        "orchestrator_api.main.publish_system_event",
        new=AsyncMock(return_value=True),
    ) as publish:
        result = await main.drain_project_transition_outbox(storage)

    assert result == {"claimed": 1, "published": 1, "failed": 0}
    assert storage.claimed == [outbox_id]
    assert storage.marked == [(outbox_id, {"status": "PUBLISHED"})]
    assert publish.await_args.kwargs == {
        "transition_id": outbox_id,
        "include_stage_directive": False,
    }


@pytest.mark.asyncio
async def test_drain_project_transition_outbox_returns_failed_row_to_retry_queue() -> None:
    from orchestrator_api import main

    outbox_id = uuid4()
    storage = _OutboxStorage(
        {
            "id": outbox_id,
            "project_id": UUID(PROJECT_ID),
            "from_state": "INIT",
            "to_state": "FEASIBILITY_CHECK",
            "event": "project_created",
            "triggered_by": "orchestrator",
            "payload": {},
        }
    )
    with patch(
        "orchestrator_api.main.publish_system_event",
        new=AsyncMock(return_value=False),
    ):
        result = await main.drain_project_transition_outbox(storage)

    assert result == {"claimed": 1, "published": 0, "failed": 1}
    assert storage.marked[0][0] == outbox_id
    assert storage.marked[0][1]["status"] == "PENDING"
    assert "router did not accept" in str(storage.marked[0][1]["error"])


@pytest.mark.asyncio
async def test_project_transition_outbox_loop_runs_one_bounded_iteration() -> None:
    from orchestrator_api import main

    outbox_id = uuid4()
    storage = _OutboxStorage(
        {
            "id": outbox_id,
            "project_id": UUID(PROJECT_ID),
            "from_state": "INIT",
            "to_state": "FEASIBILITY_CHECK",
            "event": "project_created",
            "triggered_by": "orchestrator",
            "payload": {},
        }
    )
    with patch(
        "orchestrator_api.main.publish_system_event",
        new=AsyncMock(return_value=True),
    ):
        await main.project_transition_outbox_loop(
            storage,
            asyncio.Event(),
            interval_seconds=0,
            max_iterations=1,
        )

    assert storage.marked == [(outbox_id, {"status": "PUBLISHED"})]
