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
async def test_controller_forwards_completion_policy_to_locked_storage_transition() -> None:
    storage = _RecordingControllerStorage()
    await WorkflowController(storage=storage).transition(
        project_id=str(PROJECT_ID),
        current_state=ProjectState.KPI_PERSISTENCE,
        event=WorkflowEvent.KPI_SAVED,
        actor_id="operator",
        completion_policy={"requires_flow_terminal": True, "allow_active_runs": False},
        expected_revision=21,
    )

    assert storage.transition_kwargs is not None
    assert storage.transition_kwargs["new_state"] == ProjectState.COMPLETED
    assert storage.transition_kwargs["completion_policy"] == {
        "requires_flow_terminal": True,
        "allow_active_runs": False,
    }
    assert storage.transition_kwargs["expected_revision"] == 21


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


@pytest.mark.asyncio
async def test_project_completion_rechecks_live_flow_under_project_lock() -> None:
    storage = AgentStorage.__new__(AgentStorage)
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            _Result(row={"id": PROJECT_ID, "state": "KPI_PERSISTENCE"}),
            _Result(rows=[]),
            _Result(row={"id": uuid4(), "project_id": PROJECT_ID, "status": "RUNNING"}),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=conn)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage._engine = engine

    result = await storage.transition_project(
        PROJECT_ID,
        new_state="COMPLETED",
        event="kpi_saved",
        triggered_by="operator",
        expected_state="KPI_PERSISTENCE",
        completion_policy={"requires_flow_terminal": False, "allow_active_runs": False},
    )

    assert result is None
    assert conn.execute.await_count == 3
    project_lock = str(conn.execute.await_args_list[0].args[0]).upper()
    gate_lock = str(conn.execute.await_args_list[1].args[0]).upper()
    flow_lock = str(conn.execute.await_args_list[2].args[0]).upper()
    assert "FROM PROJECTS" in project_lock and "FOR UPDATE" in project_lock
    assert "FROM APPROVAL_GATES" in gate_lock and "FOR UPDATE" in gate_lock
    assert "FROM FLOW_INSTANCES" in flow_lock and "FOR UPDATE" in flow_lock
    assert not any(
        str(call.args[0]).lstrip().upper().startswith(("UPDATE PROJECTS", "INSERT INTO PROJECT_STATE_HISTORY"))
        for call in conn.execute.await_args_list[3:]
    )


@pytest.mark.asyncio
async def test_project_completion_rejects_evidence_from_an_old_project_revision() -> None:
    storage = AgentStorage.__new__(AgentStorage)
    conn = MagicMock()
    conn.execute = AsyncMock(
        return_value=_Result(row={"id": PROJECT_ID, "state": "KPI_PERSISTENCE", "revision": 8})
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=conn)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage._engine = engine

    result = await storage.transition_project(
        PROJECT_ID,
        new_state="COMPLETED",
        event="kpi_saved",
        triggered_by="operator",
        expected_state="KPI_PERSISTENCE",
        expected_revision=7,
        completion_policy={"requires_flow_terminal": False, "allow_active_runs": False},
    )

    assert result is None
    assert conn.execute.await_count == 1
    assert "FOR UPDATE" in str(conn.execute.await_args.args[0]).upper()


@pytest.mark.asyncio
async def test_project_completion_rechecks_active_worker_runs_after_evidence_read() -> None:
    storage = AgentStorage.__new__(AgentStorage)
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            _Result(row={"id": PROJECT_ID, "state": "KPI_PERSISTENCE"}),
            _Result(rows=[]),
            _Result(row=None),
            _Result(row={"id": uuid4()}),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=conn)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage._engine = engine

    result = await storage.transition_project(
        PROJECT_ID,
        new_state="COMPLETED",
        event="kpi_saved",
        triggered_by="operator",
        expected_state="KPI_PERSISTENCE",
        completion_policy={"requires_flow_terminal": False, "allow_active_runs": False},
    )

    assert result is None
    assert conn.execute.await_count == 4
    project_lock = str(conn.execute.await_args_list[0].args[0]).upper()
    gate_lock = str(conn.execute.await_args_list[1].args[0]).upper()
    active_run_check = str(conn.execute.await_args_list[3].args[0]).upper()
    assert "FROM PROJECTS" in project_lock and "FOR UPDATE" in project_lock
    assert "FROM APPROVAL_GATES" in gate_lock and "FOR UPDATE" in gate_lock
    assert "FROM WORKER_RUNS" in active_run_check
    assert "PROJECT_ID" in active_run_check and "STATE NOT IN" in active_run_check
    assert "FOR UPDATE" not in active_run_check


@pytest.mark.asyncio
async def test_project_completion_requires_live_successful_flow_when_policy_requires_it() -> None:
    storage = AgentStorage.__new__(AgentStorage)
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            _Result(row={"id": PROJECT_ID, "state": "KPI_PERSISTENCE"}),
            _Result(rows=[]),
            _Result(row={"id": uuid4(), "project_id": PROJECT_ID, "status": "FAILED"}),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=conn)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage._engine = engine

    result = await storage.transition_project(
        PROJECT_ID,
        new_state="COMPLETED",
        event="kpi_saved",
        triggered_by="operator",
        expected_state="KPI_PERSISTENCE",
        completion_policy={"requires_flow_terminal": True, "allow_active_runs": False},
    )

    assert result is None
    assert conn.execute.await_count == 3


@pytest.mark.asyncio
async def test_project_completion_accepts_current_completed_flow_and_no_active_runs() -> None:
    storage = AgentStorage.__new__(AgentStorage)
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            _Result(row={"id": PROJECT_ID, "state": "KPI_PERSISTENCE"}),
            _Result(rows=[]),
            _Result(row={"id": uuid4(), "project_id": PROJECT_ID, "status": "COMPLETED"}),
            _Result(row=None),
            _Result(),
            _Result(),
            _Result(),
            _Result(),
            _Result(row={"id": PROJECT_ID, "state": "COMPLETED"}),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=conn)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage._engine = engine
    storage._enqueue_project_projections_tx = AsyncMock(return_value=[])
    storage.get_project = AsyncMock(return_value={"id": PROJECT_ID, "state": "COMPLETED"})

    result = await storage.transition_project(
        PROJECT_ID,
        new_state="COMPLETED",
        event="kpi_saved",
        triggered_by="operator",
        expected_state="KPI_PERSISTENCE",
        completion_policy={"requires_flow_terminal": True, "allow_active_runs": False},
    )

    assert result == {"id": PROJECT_ID, "state": "COMPLETED"}
    assert conn.execute.await_count == 9
    assert "UPDATE PROJECTS" in str(conn.execute.await_args_list[4].args[0]).upper()
