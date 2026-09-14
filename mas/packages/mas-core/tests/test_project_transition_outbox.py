"""Characterization and contract tests for the project-transition outbox."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from mas_core.memory.models import project_transition_outbox
from mas_core.memory.storage import AgentStorage
from mas_core.workflow import ProjectState, WorkflowController, WorkflowEvent

PROJECT_ID = UUID("00000000-0000-4000-a000-000000000301")


class _Result:
    def __init__(self, *, row: dict | None = None, rows: list[dict] | None = None) -> None:
        self._row = row
        self._rows = rows or ([] if row is None else [row])
        self.rowcount = 1

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict | None:
        return self._row

    def all(self) -> list[dict]:
        return self._rows


class _RecordingControllerStorage:
    def __init__(self) -> None:
        self.transition_kwargs: dict[str, object] | None = None
        self.published: list[UUID] = []

    async def transition_project(self, _project_id: UUID, **kwargs: object) -> dict[str, object]:
        self.transition_kwargs = kwargs
        return {"id": PROJECT_ID, "state": str(kwargs["new_state"])}

    async def mark_project_transition_published(self, transition_id: UUID) -> None:
        self.published.append(transition_id)


@pytest.mark.asyncio
async def test_controller_propagates_one_stable_transition_id_to_storage_and_publisher() -> None:
    storage = _RecordingControllerStorage()
    publisher = AsyncMock(return_value=True)

    result = await WorkflowController(
        storage=storage,
        event_publisher=publisher,
    ).transition(
        project_id=str(PROJECT_ID),
        current_state=ProjectState.INIT,
        event=WorkflowEvent.PROJECT_CREATED,
        actor_id="orchestrator",
    )

    transition_id = result.transition_id
    assert transition_id is not None
    assert storage.transition_kwargs is not None
    assert storage.transition_kwargs["transition_id"] == transition_id
    assert publisher.await_args.kwargs["transition_id"] == transition_id
    assert storage.published == [transition_id]


@pytest.mark.asyncio
async def test_controller_leaves_notification_pending_when_publisher_reports_failure() -> None:
    storage = _RecordingControllerStorage()
    publisher = AsyncMock(return_value=False)

    result = await WorkflowController(
        storage=storage,
        event_publisher=publisher,
    ).transition(
        project_id=str(PROJECT_ID),
        current_state=ProjectState.INIT,
        event=WorkflowEvent.PROJECT_CREATED,
        actor_id="orchestrator",
    )

    assert result.transition_id is not None
    assert storage.published == []


@pytest.mark.asyncio
async def test_agent_storage_writes_outbox_intent_inside_transition_transaction() -> None:
    storage = AgentStorage.__new__(AgentStorage)
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            _Result(row={"id": PROJECT_ID, "state": "INIT"}),
            _Result(),
            _Result(),
            _Result(),
            _Result(row={"id": PROJECT_ID, "state": "FEASIBILITY_CHECK"}),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=conn)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage._engine = engine
    storage._enqueue_project_projections_tx = AsyncMock(return_value=[])
    storage.get_project = AsyncMock(
        return_value={"id": PROJECT_ID, "state": "FEASIBILITY_CHECK"}
    )

    transition_id = uuid4()
    result = await storage.transition_project(
        PROJECT_ID,
        new_state="FEASIBILITY_CHECK",
        event="project_created",
        triggered_by="orchestrator",
        expected_state="INIT",
        transition_id=transition_id,
    )

    assert result == {"id": PROJECT_ID, "state": "FEASIBILITY_CHECK"}
    assert conn.execute.await_count == 5
    outbox_insert = conn.execute.await_args_list[3].args[0]
    assert outbox_insert.table is project_transition_outbox
    assert outbox_insert.compile().params["id"] == transition_id
    assert outbox_insert.compile().params["project_id"] == PROJECT_ID
    assert outbox_insert.compile().params["event"] == "project_created"
