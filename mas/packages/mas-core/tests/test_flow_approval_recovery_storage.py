from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage


class _Result:
    def __init__(self, *, first=None, rows=None, rowcount: int = 1):
        self._first = first
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._rows


def _storage_with_results(results: list[object]) -> tuple[AgentStorage, AsyncMock, AsyncMock]:
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=results)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=conn)
    tx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = tx
    storage = AgentStorage("postgresql+asyncpg://unused:unused@localhost/unused")
    storage._engine = engine
    return storage, conn.execute, tx.__aexit__


def _legacy_rows(*, instance_count: int = 1, execution_count: int = 1):
    project_id = uuid4()
    instance_id = uuid4()
    gate_id = uuid4()
    selected_gate = {
        "id": gate_id,
        "project_id": project_id,
        "gate_type": "human",
        "status": "PENDING",
        "flow_node_execution_id": None,
    }
    instances = [
        {
            "id": instance_id if index == 0 else uuid4(),
            "flow_id": uuid4(),
            "flow_version": 1,
            "project_id": project_id,
            "active_node_ids": ["approval" if index == 0 else "approval_2"],
            "status": "RUNNING",
        }
        for index in range(instance_count)
    ]
    executions = [
        {
            "id": 80 + index,
            "instance_id": instances[index % instance_count]["id"],
            "node_id": "approval" if index == 0 else "approval_2",
            "node_type": "approval",
            "status": "RUNNING",
        }
        for index in range(execution_count)
    ]
    flow = {
        "version": 1,
        "definition_json": {
            "nodes": [
                {"id": "start", "type": "start", "label": "Start", "config": {}},
                {
                    "id": "approval",
                    "type": "approval",
                    "label": "Human Approval",
                    "config": {"approver_user": "human"},
                },
                {
                    "id": "approval_2",
                    "type": "approval",
                    "label": "Second Human Approval",
                    "config": {"approver_user": "human"},
                },
                {"id": "done", "type": "end", "label": "Done", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "approval"},
                {"id": "e2", "source": "approval", "target": "done"},
                {"id": "e3", "source": "start", "target": "approval_2"},
                {"id": "e4", "source": "approval_2", "target": "done"},
            ],
        },
    }
    return project_id, gate_id, selected_gate, instances, executions, flow


@pytest.mark.anyio
async def test_flow_activation_locks_project_before_any_activation_and_rejects_terminal() -> None:
    project_id = uuid4()
    instance_id = uuid4()
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first={"id": project_id, "state": "COMPLETED"}),
        ]
    )

    result = await storage.activate_flow_nodes(
        instance_id,
        expected_statuses={"NOT_STARTED"},
        instance_updates={"status": "RUNNING", "active_node_ids": ["task"]},
        activations=[
            {
                "node_id": "task",
                "node_type": "task",
                "node_label": "Task",
                "status": "RUNNING",
            }
        ],
    )

    assert result == {"outcome": "project_terminal"}
    assert execute.await_count == 2
    hint_sql = str(execute.await_args_list[0].args[0]).upper()
    project_lock_sql = str(execute.await_args_list[1].args[0]).upper()
    assert "FROM FLOW_INSTANCES" in hint_sql
    assert "FROM PROJECTS" in project_lock_sql and "FOR UPDATE" in project_lock_sql


@pytest.mark.anyio
async def test_flow_activation_rejects_security_blocked_project_under_the_same_lock() -> None:
    project_id = uuid4()
    instance_id = uuid4()
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first={"id": project_id, "state": "SECURITY_BLOCKED"}),
        ]
    )

    result = await storage.activate_flow_nodes(
        instance_id,
        expected_statuses={"NOT_STARTED"},
        instance_updates={"status": "RUNNING", "active_node_ids": ["task"]},
        activations=[
            {
                "node_id": "task",
                "node_type": "task",
                "node_label": "Task",
                "status": "RUNNING",
            }
        ],
    )

    assert result == {"outcome": "project_not_dispatchable"}
    assert execute.await_count == 2
    project_lock_sql = str(execute.await_args_list[1].args[0]).upper()
    assert "FROM PROJECTS" in project_lock_sql and "FOR UPDATE" in project_lock_sql


@pytest.mark.anyio
async def test_flow_node_settlement_rechecks_terminal_project_before_any_write() -> None:
    project_id = uuid4()
    instance_id = uuid4()
    execution = {
        "id": 23,
        "instance_id": instance_id,
        "node_id": "work",
        "node_type": "task",
        "status": "RUNNING",
    }
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first={"id": project_id, "state": "COMPLETED"}),
        ]
    )

    result = await storage.activate_flow_nodes(
        instance_id,
        expected_statuses={"RUNNING"},
        expected_active_node_ids=["work"],
        instance_updates={"status": "COMPLETED", "active_node_ids": []},
        activations=[],
        execution_updates=[
            {
                "expected": execution,
                "values": {"status": "COMPLETED", "completed_at": "now"},
            }
        ],
    )

    assert result == {"outcome": "project_terminal"}
    assert execute.await_count == 2
    assert "FOR UPDATE" in str(execute.await_args_list[1].args[0]).upper()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [
        {"context_json": {"late": True}},
        {"escalated_to": "exec_ceo", "escalation_reason": "late"},
        {"status": "COMPLETED", "active_node_ids": []},
    ],
)
async def test_generic_flow_instance_update_locks_project_before_context_escalation_or_terminal_write(
    updates: dict[str, object],
) -> None:
    project_id = uuid4()
    instance_id = uuid4()
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"project_id": project_id}),
            _Result(first={"id": project_id, "state": "COMPLETED"}),
        ]
    )

    result = await storage.update_flow_instance(instance_id, **updates)

    assert result is None
    assert execute.await_count == 2
    assert "FROM FLOW_INSTANCES" in str(execute.await_args_list[0].args[0]).upper()
    project_lock = str(execute.await_args_list[1].args[0]).upper()
    assert "FROM PROJECTS" in project_lock
    assert "FOR UPDATE" in project_lock


@pytest.mark.anyio
async def test_flow_node_settlement_rolls_back_if_locked_instance_cas_write_fails() -> None:
    project_id = uuid4()
    instance_id = uuid4()
    execution = {
        "id": 24,
        "instance_id": instance_id,
        "node_id": "work",
        "node_type": "task",
        "status": "RUNNING",
    }
    instance = {
        "id": instance_id,
        "project_id": project_id,
        "flow_id": uuid4(),
        "flow_version": 1,
        "status": "RUNNING",
        "active_node_ids": ["work"],
        "context_json": {},
        "updated_at": "before",
        "retry_count": 0,
    }
    storage, execute, tx_exit = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(rows=[]),  # no pending approval gate
            _Result(rows=[execution]),
            _Result(first=instance),
            _Result(),  # settle the node execution
            _Result(rowcount=0),  # force the locked flow instance update to lose
        ]
    )

    with pytest.raises(RuntimeError, match="flow instance changed"):
        await storage.activate_flow_nodes(
            instance_id,
            expected_statuses={"RUNNING"},
            expected_active_node_ids=["work"],
            expected_instance_snapshot={
                "id": instance_id,
                "project_id": project_id,
                "status": "RUNNING",
                "active_node_ids": ["work"],
                "context_json": {},
                "updated_at": "before",
                "retry_count": 0,
            },
            instance_updates={"status": "FAILED", "active_node_ids": []},
            activations=[],
            execution_updates=[
                {
                    "expected": execution,
                    "values": {"status": "FAILED", "error": "test", "completed_at": "now"},
                }
            ],
        )

    assert execute.await_count == 7
    assert "FOR UPDATE" in str(execute.await_args_list[1].args[0]).upper()
    assert "FOR UPDATE" in str(execute.await_args_list[2].args[0]).upper()
    assert "FOR UPDATE" in str(execute.await_args_list[3].args[0]).upper()
    assert "FOR UPDATE" in str(execute.await_args_list[4].args[0]).upper()
    assert "UPDATE FLOW_NODE_EXECUTIONS" in str(execute.await_args_list[5].args[0]).upper()
    assert "UPDATE FLOW_INSTANCES" in str(execute.await_args_list[6].args[0]).upper()
    assert tx_exit.await_args.args[0] is RuntimeError


@pytest.mark.anyio
async def test_timeout_project_event_preserves_revision_and_projection_in_atomic_write() -> None:
    project_id = uuid4()
    instance_id = uuid4()
    execution = {
        "id": 25,
        "instance_id": instance_id,
        "node_id": "work",
        "node_type": "task",
        "status": "RUNNING",
    }
    instance = {
        "id": instance_id,
        "project_id": project_id,
        "flow_id": uuid4(),
        "flow_version": 1,
        "status": "RUNNING",
        "active_node_ids": ["work"],
        "context_json": {},
        "retry_count": 0,
    }
    project = {
        "id": project_id,
        "state": "IN_PROGRESS",
        "revision": 7,
        "name": "Flow project",
    }
    updated_instance = {**instance, "status": "FAILED", "active_node_ids": []}
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first=project),
            _Result(rows=[]),
            _Result(rows=[execution]),
            _Result(first=instance),
            _Result(),  # settle the node execution
            _Result(),  # update the flow instance
            _Result(),  # increment project revision
            _Result(),  # append project history
            _Result(),  # append transition outbox
            _Result(first=updated_instance),
        ]
    )
    storage._enqueue_project_projections_tx = AsyncMock(return_value=[])

    result = await storage.activate_flow_nodes(
        instance_id,
        expected_statuses={"RUNNING"},
        expected_active_node_ids=["work"],
        expected_instance_snapshot=instance,
        instance_updates={"status": "FAILED", "active_node_ids": []},
        activations=[],
        execution_updates=[
            {
                "expected": execution,
                "values": {"status": "FAILED", "error": "Timed out"},
            }
        ],
        project_event={
            "event": "flow_node_escalated",
            "triggered_by": "system",
            "payload": {"instance_id": str(instance_id), "node_id": "work"},
        },
    )

    assert result["outcome"] == "applied"
    project_update = execute.await_args_list[7].args[0]
    assert "UPDATE PROJECTS" in str(project_update).upper()
    assert "PROJECTS.REVISION +" in str(project_update).upper()
    history = execute.await_args_list[8].args[0]
    outbox = execute.await_args_list[9].args[0]
    assert "INSERT INTO PROJECT_STATE_HISTORY" in str(history).upper()
    assert "INSERT INTO PROJECT_TRANSITION_OUTBOX" in str(outbox).upper()
    history_values = history.compile().params
    outbox_values = outbox.compile().params
    assert history_values["from_state"] == history_values["to_state"] == "IN_PROGRESS"
    assert outbox_values["from_state"] == outbox_values["to_state"] == "IN_PROGRESS"
    storage._enqueue_project_projections_tx.assert_awaited_once()
    projection = storage._enqueue_project_projections_tx.await_args.args[1]
    assert projection["revision"] == 8


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["switch_flow_instance", "migrate_flow_instance"])
async def test_flow_definition_mutation_rechecks_project_state_under_lock(mutation: str) -> None:
    project_id = uuid4()
    instance_id = uuid4()
    new_flow_id = uuid4()
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"project_id": project_id}),
            _Result(first={"id": project_id, "state": "COMPLETED"}),
        ]
    )
    storage.get_flow = AsyncMock(return_value={"id": new_flow_id, "version": 2})

    method = getattr(storage, mutation)
    kwargs = (
        {"active_node_ids": ["task"], "migration_record": {"to_flow_id": str(new_flow_id)}}
        if mutation == "migrate_flow_instance"
        else {}
    )
    result = await method(
        instance_id,
        new_flow_id,
        expected_instance_snapshot={"id": instance_id, "project_id": project_id},
        **kwargs,
    )

    assert result is None
    assert execute.await_count == 2
    assert "FROM FLOW_INSTANCES" in str(execute.await_args_list[0].args[0]).upper()
    project_lock_sql = str(execute.await_args_list[1].args[0]).upper()
    assert "FROM PROJECTS" in project_lock_sql and "FOR UPDATE" in project_lock_sql


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["switch_flow_instance", "migrate_flow_instance"])
async def test_flow_definition_mutation_rejects_stale_snapshot_without_writes(mutation: str) -> None:
    project_id = uuid4()
    instance_id = uuid4()
    new_flow_id = uuid4()
    current_instance = {
        "id": instance_id,
        "project_id": project_id,
        "flow_id": uuid4(),
        "flow_version": 1,
        "status": "RUNNING",
        "active_node_ids": ["advanced-task"],
        "context_json": {},
        "retry_count": 0,
    }
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"project_id": project_id}),
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(first=current_instance),
        ]
    )
    storage.get_flow = AsyncMock(return_value={"id": new_flow_id, "version": 2})
    method = getattr(storage, mutation)
    kwargs = (
        {"active_node_ids": ["task"], "migration_record": {"to_flow_id": str(new_flow_id)}}
        if mutation == "migrate_flow_instance"
        else {}
    )

    result = await method(
        instance_id,
        new_flow_id,
        expected_instance_snapshot={
            **current_instance,
            "active_node_ids": ["stale-task"],
        },
        **kwargs,
    )

    assert result is None
    assert execute.await_count == 3
    assert "FOR UPDATE" in str(execute.await_args_list[2].args[0]).upper()


@pytest.mark.anyio
async def test_legacy_flow_gate_binds_only_unique_active_execution_and_replays():
    project_id, gate_id, gate, instances, executions, flow = _legacy_rows()
    bound = {**gate, "flow_node_execution_id": executions[0]["id"]}
    storage, execute, _ = _storage_with_results(
        [
            _Result(first=gate),
            _Result(rows=[gate]),
            _Result(rows=instances),
            _Result(rows=executions),
            _Result(first=flow),
            _Result(first=executions[0]),
            _Result(first=instances[0]),
            _Result(first=flow),
            _Result(rows=[gate]),
            _Result(first=None),
            _Result(first=bound),
        ]
    )

    result = await storage.bind_legacy_flow_approval_gate(gate_id, project_id=project_id)

    assert result == {"outcome": "bound", "gate": bound, "execution": executions[0]}
    assert execute.await_count == 11
    update_stmt = execute.await_args_list[-1].args[0]
    assert "flow_node_execution_id" in update_stmt._values

    # A retry sees the already-bound row and performs no second write.
    execute.reset_mock(side_effect=True)
    execute.side_effect = None
    execute.return_value = _Result(first=bound)
    replay = await storage.bind_legacy_flow_approval_gate(gate_id, project_id=project_id)
    assert replay == {"outcome": "already_bound", "gate": bound}
    assert execute.await_count == 1


@pytest.mark.anyio
async def test_legacy_flow_gate_refuses_multiple_active_execution_matches_without_write():
    project_id, gate_id, gate, instances, executions, flow = _legacy_rows(execution_count=2)
    storage, execute, _ = _storage_with_results(
        [
            _Result(first=gate),
            _Result(rows=[gate]),
            _Result(rows=instances),
            _Result(rows=executions),
            _Result(first=flow),
        ]
    )

    result = await storage.bind_legacy_flow_approval_gate(gate_id, project_id=project_id)

    assert result == {"outcome": "ambiguous"}
    assert execute.await_count == 5


@pytest.mark.anyio
async def test_legacy_flow_gate_refuses_multiple_unbound_gates_without_write():
    project_id, gate_id, gate, instances, executions, flow = _legacy_rows()
    second_gate = {**gate, "id": uuid4()}
    storage, execute, _ = _storage_with_results(
        [
            _Result(first=gate),
            _Result(rows=[gate, second_gate]),
            _Result(rows=instances),
            _Result(rows=executions),
            _Result(first=flow),
        ]
    )

    result = await storage.bind_legacy_flow_approval_gate(gate_id, project_id=project_id)

    assert result == {"outcome": "ambiguous"}
    assert execute.await_count == 5


@pytest.mark.anyio
async def test_legacy_unbound_generic_gate_stays_on_generic_approval_path():
    project_id, gate_id, gate, _, _, _ = _legacy_rows()
    storage, execute, _ = _storage_with_results(
        [_Result(first=gate), _Result(rows=[gate]), _Result(rows=[])]
    )

    result = await storage.bind_legacy_flow_approval_gate(gate_id, project_id=project_id)

    assert result == {"outcome": "generic", "gate": gate}
    assert execute.await_count == 3


@pytest.mark.anyio
async def test_approval_activation_failure_rolls_back_transaction_before_instance_update():
    instance_id = uuid4()
    project_id = uuid4()
    selected_gate_id = uuid4()
    instance = {
        "id": instance_id,
        "project_id": project_id,
        "status": "NOT_STARTED",
        "active_node_ids": [],
    }
    storage, execute, tx_exit = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(first=instance),
            _Result(),
            _Result(first=(100,)),
            RuntimeError("duplicate approval gate identity"),
        ]
    )

    with pytest.raises(RuntimeError, match="duplicate approval gate"):
        await storage.activate_flow_nodes(
            instance_id,
            expected_statuses={"NOT_STARTED"},
            instance_updates={"status": "RUNNING", "active_node_ids": ["approval"]},
            activations=[
                {
                    "node_id": "approval",
                    "node_type": "approval",
                    "node_label": "Approval",
                    "status": "RUNNING",
                    "gate_type": "human",
                    "gate_id": selected_gate_id,
                },
                {
                    "node_id": "approval_2",
                    "node_type": "approval",
                    "node_label": "Approval 2",
                    "status": "RUNNING",
                    "gate_type": "human",
                    "gate_id": selected_gate_id,
                },
            ],
        )

    assert execute.await_count == 6
    assert tx_exit.await_args.args[0] is RuntimeError


@pytest.mark.anyio
async def test_flow_approval_rejects_stale_sibling_snapshot_without_mutation():
    # Keep the gate/execution/instance ownership coherent; the mismatch under
    # test is that the sibling progressed after the caller computed a branch.
    gate_id = uuid4()
    project_id = uuid4()
    instance_id = uuid4()
    execution_id = 81
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(
                first={
                    "id": gate_id,
                    "project_id": project_id,
                    "flow_node_execution_id": execution_id,
                    "status": "PENDING",
                }
            ),
            _Result(
                first={
                    "id": execution_id,
                    "instance_id": instance_id,
                    "node_id": "approval_a",
                    "node_type": "approval",
                    "status": "RUNNING",
                }
            ),
            _Result(
                first={
                    "id": instance_id,
                    "project_id": project_id,
                    "status": "RUNNING",
                    "active_node_ids": ["approval_b"],
                    "context_json": {"progress": "sibling advanced"},
                    "updated_at": "fresh",
                    "retry_count": 0,
                }
            ),
        ]
    )

    result = await storage.resolve_flow_approval_gate(
        gate_id,
        project_id=project_id,
        instance_id=instance_id,
        execution_id=execution_id,
        node_id="approval_a",
        status="APPROVED",
        decided_by="operator",
        justification=None,
        human_input=None,
        execution_output={"approval": "approved"},
        instance_updates={"active_node_ids": []},
        activated_executions=[],
        expected_instance_snapshot={
            "status": "RUNNING",
            "active_node_ids": ["approval_a", "approval_b"],
            "context_json": {"progress": "initial"},
            "updated_at": "old",
            "retry_count": 0,
        },
    )

    assert result["outcome"] == "stale_instance"
    assert execute.await_count == 4


@pytest.mark.anyio
async def test_approval_retry_atomically_supersedes_old_gate_and_activates_replacement():
    instance_id = uuid4()
    project_id = uuid4()
    gate_id = uuid4()
    old_execution = {
        "id": 41,
        "instance_id": instance_id,
        "node_id": "approval",
        "node_type": "approval",
        "status": "RUNNING",
    }
    old_gate = {
        "id": gate_id,
        "flow_node_execution_id": 41,
        "status": "PENDING",
    }
    failed_instance = {
        "id": instance_id,
        "project_id": project_id,
        "status": "FAILED",
        "active_node_ids": [],
        "context_json": {"last_safe_node_id": "approval"},
        "retry_count": 2,
        "updated_at": "before-retry",
    }
    retried_instance = {
        **failed_instance,
        "status": "RUNNING",
        "active_node_ids": ["approval"],
        "retry_count": 3,
        "updated_at": "after-retry",
    }
    insert_execution = _Result(first=(99,))
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(rows=[(41,)]),
            _Result(rows=[old_gate]),
            _Result(rows=[old_execution]),
            _Result(first=failed_instance),
            _Result(),  # close old gate
            _Result(),  # supersede old execution
            _Result(),  # update instance
            insert_execution,
            _Result(),  # insert replacement gate
            _Result(first=retried_instance),
        ]
    )

    result = await storage.activate_flow_nodes(
        instance_id,
        expected_statuses={"FAILED", "CANCELLED"},
        expected_active_node_ids=[],
        expected_instance_snapshot={
            "status": "FAILED",
            "active_node_ids": [],
            "context_json": {"last_safe_node_id": "approval"},
            "retry_count": 2,
            "updated_at": "before-retry",
        },
        instance_updates={
            "status": "RUNNING",
            "active_node_ids": ["approval"],
            "retry_count": 3,
        },
        activations=[
            {
                "node_id": "approval",
                "node_type": "approval",
                "node_label": "Approval",
                "status": "RUNNING",
                "gate_type": "human",
            }
        ],
        supersede_existing=True,
    )

    assert result["outcome"] == "applied"
    assert result["instance"] == retried_instance
    assert result["executions"][0]["id"] == 99
    locked_tables = [str(execute.await_args_list[index].args[0]).upper() for index in (3, 4, 5)]
    assert "APPROVAL_GATES" in locked_tables[0] and "FOR UPDATE" in locked_tables[0]
    assert "FLOW_NODE_EXECUTIONS" in locked_tables[1] and "FOR UPDATE" in locked_tables[1]
    assert "FLOW_INSTANCES" in locked_tables[2] and "FOR UPDATE" in locked_tables[2]
    assert execute.await_count == 12


@pytest.mark.anyio
async def test_overlapping_retry_loser_cannot_supersede_new_active_execution():
    instance_id = uuid4()
    project_id = uuid4()
    execution = {"id": 52, "instance_id": instance_id, "status": "RUNNING"}
    gate = {"id": uuid4(), "flow_node_execution_id": 52, "status": "PENDING"}
    winner_instance = {
        "id": instance_id,
        "project_id": project_id,
        "status": "RUNNING",
        "active_node_ids": ["approval"],
        "context_json": {},
        "retry_count": 3,
    }
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": project_id}),
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(rows=[(52,)]),
            _Result(rows=[gate]),
            _Result(rows=[execution]),
            _Result(first=winner_instance),
        ]
    )

    result = await storage.activate_flow_nodes(
        instance_id,
        expected_statuses={"FAILED", "CANCELLED"},
        expected_active_node_ids=[],
        instance_updates={"status": "RUNNING", "active_node_ids": ["approval"]},
        activations=[
            {
                "node_id": "approval",
                "node_type": "approval",
                "node_label": "Approval",
                "gate_type": "human",
            }
        ],
        supersede_existing=True,
    )

    assert result["outcome"] == "conflict"
    assert execute.await_count == 6


@pytest.mark.anyio
async def test_cancel_flow_instance_closes_pending_bound_gate_in_same_transaction():
    instance_id = uuid4()
    project_id = uuid4()
    flow_id = uuid4()
    gate = {
        "id": uuid4(),
        "flow_node_execution_id": 61,
        "status": "PENDING",
    }
    execution = {
        "id": 61,
        "instance_id": instance_id,
        "node_id": "approval",
        "status": "RUNNING",
        "node_type": "approval",
    }
    active_instance = {
        "id": instance_id,
        "project_id": project_id,
        "flow_id": flow_id,
        "flow_version": 1,
        "status": "RUNNING",
        "active_node_ids": ["approval"],
        "context_json": {},
        "updated_at": "before-cancel",
    }
    cancelled_instance = {
        **active_instance,
        "status": "CANCELLED",
        "active_node_ids": [],
    }
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": project_id, "state": "COMPLETED"}),
            _Result(
                first={
                    "id": flow_id,
                    "version": 1,
                    "definition_json": {
                        "nodes": [
                            {"id": "start", "type": "start", "label": "Start", "config": {}},
                            {
                                "id": "approval",
                                "type": "approval",
                                "label": "Approval",
                                "config": {"approver_user": "human"},
                            },
                            {"id": "end", "type": "end", "label": "End", "config": {}},
                        ],
                        "edges": [
                            {"id": "e1", "source": "start", "target": "approval"},
                            {"id": "e2", "source": "approval", "target": "end"},
                        ],
                    },
                }
            ),
            _Result(rows=[execution]),
            _Result(rows=[]),  # no matching legacy unbound gate
            _Result(rows=[(61,)]),
            _Result(rows=[gate]),
            _Result(rows=[execution]),
            _Result(first=active_instance),
            _Result(),
            _Result(),
            _Result(),
            _Result(first=cancelled_instance),
        ]
    )

    result = await storage.cancel_flow_instance(
        instance_id,
        expected_instance_snapshot={
            "status": "RUNNING",
            "active_node_ids": ["approval"],
            "context_json": {},
            "updated_at": "before-cancel",
            "project_id": project_id,
            "flow_id": flow_id,
            "flow_version": 1,
        },
    )

    assert result["outcome"] == "applied"
    assert result["instance"]["status"] == "CANCELLED"
    assert result["cancelled_gate_ids"] == [gate["id"]]
    assert execute.await_count == 12


@pytest.mark.anyio
async def test_cancel_flow_fails_closed_on_matching_legacy_unbound_approval_gate():
    instance_id = uuid4()
    project_id = uuid4()
    flow_id = uuid4()
    legacy_gate = {
        "id": uuid4(),
        "project_id": project_id,
        "gate_type": "human",
        "status": "PENDING",
        "flow_node_execution_id": None,
    }
    active_execution = {
        "id": 72,
        "instance_id": instance_id,
        "node_id": "approval",
        "node_type": "approval",
        "status": "RUNNING",
    }
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": project_id, "state": "IN_PROGRESS"}),
            _Result(
                first={
                    "id": flow_id,
                    "version": 1,
                    "definition_json": {
                        "nodes": [
                            {"id": "start", "type": "start", "label": "Start", "config": {}},
                            {"id": "approval", "type": "approval", "label": "Approval", "config": {"approver_user": "human"}},
                            {"id": "end", "type": "end", "label": "End", "config": {}},
                        ],
                        "edges": [
                            {"id": "e1", "source": "start", "target": "approval"},
                            {"id": "e2", "source": "approval", "target": "end"},
                        ],
                    },
                }
            ),
            _Result(rows=[active_execution]),
            _Result(rows=[legacy_gate]),
        ]
    )

    result = await storage.cancel_flow_instance(
        instance_id,
        expected_instance_snapshot={
            "id": instance_id,
            "project_id": project_id,
            "flow_id": flow_id,
            "flow_version": 1,
            "status": "RUNNING",
            "active_node_ids": ["approval"],
            "context_json": {},
        },
    )

    assert result == {
        "outcome": "legacy_gate_unresolved",
        "pending_legacy_gate_count": 1,
    }
    assert execute.await_count == 4


@pytest.mark.anyio
async def test_retry_without_safe_node_atomically_closes_pending_gate_and_supersedes_history():
    instance_id = uuid4()
    gate = {"id": uuid4(), "flow_node_execution_id": 71, "status": "PENDING"}
    failed_instance = {
        "id": instance_id,
        "project_id": uuid4(),
        "status": "FAILED",
        "active_node_ids": [],
        "retry_count": 1,
        "updated_at": "before-retry",
    }
    retried_instance = {
        **failed_instance,
        "status": "NOT_STARTED",
        "retry_count": 2,
    }
    storage, execute, _ = _storage_with_results(
        [
            _Result(first={"id": instance_id, "project_id": failed_instance["project_id"]}),
            _Result(first={"id": failed_instance["project_id"], "state": "IN_PROGRESS"}),
            _Result(rows=[(71,)]),
            _Result(rows=[gate]),
            _Result(rows=[{"id": 71, "status": "RUNNING"}]),
            _Result(first=failed_instance),
            _Result(),
            _Result(),
            _Result(),
            _Result(first=retried_instance),
        ]
    )

    result = await storage.retry_flow_instance(
        instance_id,
        expected_instance_snapshot={
            "status": "FAILED",
            "active_node_ids": [],
            "retry_count": 1,
            "updated_at": "before-retry",
        },
    )

    assert result == retried_instance
    assert execute.await_count == 10
