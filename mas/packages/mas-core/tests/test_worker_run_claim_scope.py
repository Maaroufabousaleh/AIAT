from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from mas_core.memory.storage import AgentStorage
from mas_core.worker_registry.execution_lineage import (
    SHELL_EXECUTION_LINEAGE_KEY,
    build_worker_execution_config_snapshot,
    worker_execution_config_snapshot_sha256,
    worker_shell_content_hash,
)

RUN_ID = UUID("00000000-0000-4000-a000-000000000901")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000902")
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000903")
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000904")
SHELL_ID = UUID("00000000-0000-4000-a000-000000000905")
ADAPTER_ID = UUID("00000000-0000-4000-a000-000000000906")
SKILL_ID = UUID("00000000-0000-4000-a000-000000000907")
STEWARD_ID = UUID("00000000-0000-4000-a000-000000000908")
ADAPTER_VERSION = "fixture-adapter-v1"
ADAPTER_TYPE = "fixture"
ADAPTER_IMPLEMENTATION_REF = "fixture.worker:run"
ADAPTER_CONTENT_HASH = "fixture-adapter-content-hash"


class _Result:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, object]]:
        return self.rows


def _run_row(*, project_id: UUID | None = PROJECT_ID) -> dict[str, object]:
    return {
        "id": RUN_ID,
        "worker_id": WORKER_ID,
        "worker_shell_version_id": SHELL_ID,
        "adapter_id": ADAPTER_ID,
        "skill_bundle_id": SKILL_ID,
        "steward_id": STEWARD_ID,
        "project_id": project_id,
        "request_json": {
            "worker_id": str(WORKER_ID),
            "project_id": str(project_id) if project_id else None,
            "tool_grants": ["file_write"],
            "permission_requirements": ["artifact.write"],
        },
        "state": "QUEUED",
        "cancel_requested_at": None,
        "next_attempt_at": None,
        "lease_expires_at": None,
        "queue_priority": 0,
        "created_at": datetime.now(UTC),
        "attempt_count": 0,
    }


def _worker(**overrides: object) -> dict[str, object]:
    return {
        "id": WORKER_ID,
        "status": "ACTIVE",
        "evaluation_status": "approved",
        "source_repo": None,
        "sandbox_profile": "trusted",
        "adapter_config": {
            "execution_policy": {
                "trust": "trusted",
                "minimum_isolation": "trusted",
            }
        },
        **overrides,
    }


def _shell(**overrides: object) -> dict[str, object]:
    snapshot = build_worker_execution_config_snapshot("native", {})
    shell: dict[str, object] = {
        "id": SHELL_ID,
        "worker_id": WORKER_ID,
        "version": "fixture-shell-v1",
        "identity_json": {"worker_id": str(WORKER_ID)},
        "capabilities_json": {},
        "permissions_json": {},
        "model_mode": "none",
        "status": "active",
        "provenance_json": {
            "runtime_adapter_id": str(ADAPTER_ID),
            "runtime_adapter": {
                "id": str(ADAPTER_ID),
                "version": ADAPTER_VERSION,
                "adapter_type": ADAPTER_TYPE,
                "transport_type": "native",
                "runtime_api_version": None,
                "implementation_ref": ADAPTER_IMPLEMENTATION_REF,
                "content_hash": ADAPTER_CONTENT_HASH,
            },
            SHELL_EXECUTION_LINEAGE_KEY: {
                "sha256": worker_execution_config_snapshot_sha256(snapshot),
                "snapshot": snapshot,
            },
        },
    }
    shell["content_hash"] = worker_shell_content_hash(shell)
    shell.update(overrides)
    return shell


def _adapter(**overrides: object) -> dict[str, object]:
    return {
        "id": ADAPTER_ID,
        "worker_id": WORKER_ID,
        "version": ADAPTER_VERSION,
        "adapter_type": ADAPTER_TYPE,
        "status": "active",
        "conformance_status": "passed",
        "transport_type": "native",
        "runtime_api_version": None,
        "implementation_ref": ADAPTER_IMPLEMENTATION_REF,
        "content_hash": ADAPTER_CONTENT_HASH,
        **overrides,
    }


def _skill_bundle(**overrides: object) -> dict[str, object]:
    return {
        "id": SKILL_ID,
        "worker_id": WORKER_ID,
        "steward_id": STEWARD_ID,
        "status": "APPROVED",
        **overrides,
    }


def _assignment(**overrides: object) -> dict[str, object]:
    return {
        "company_id": COMPANY_ID,
        "worker_id": WORKER_ID,
        "status": "ACTIVE",
        "approval_required": False,
        "tool_grants": ["file_write"],
        "permission_grants": ["artifact.write"],
        **overrides,
    }


def _storage_for_claim(
    *,
    run: dict[str, object] | None = None,
    project: dict[str, object] | None = None,
    assignments: list[dict[str, object]] | None = None,
    worker: dict[str, object] | None = None,
    shell: dict[str, object] | None = None,
    adapter: dict[str, object] | None = None,
    skill_bundle: dict[str, object] | None = None,
) -> tuple[AgentStorage, list[object]]:
    selected_run = run or _run_row()
    selected_project = project or {
        "id": PROJECT_ID,
        "company_id": COMPANY_ID,
        "state": "IN_PROGRESS",
    }
    selected_assignments = assignments if assignments is not None else [_assignment()]
    selected_worker = worker if worker is not None else _worker()
    selected_shell = shell if shell is not None else _shell()
    selected_adapter = adapter if adapter is not None else _adapter()
    selected_skill_bundle = skill_bundle if skill_bundle is not None else _skill_bundle()
    statements: list[object] = []
    worker_run_selects = 0
    claimed = {
        **selected_run,
        "state": "CLAIMED",
        "claim_owner": "host-executor",
        "attempt_count": 1,
    }

    async def execute(statement: object) -> _Result | SimpleNamespace:
        nonlocal worker_run_selects
        statements.append(statement)
        sql = str(statement)
        if "FROM worker_runs" in sql and sql.lstrip().startswith("SELECT"):
            worker_run_selects += 1
            return _Result([selected_run if worker_run_selects == 1 else claimed])
        if "FROM projects" in sql:
            return _Result([selected_project] if selected_project is not None else [])
        if "FROM company_worker_assignments" in sql:
            return _Result(selected_assignments)
        if "FROM worker_registry" in sql:
            return _Result([selected_worker] if selected_worker is not None else [])
        if "FROM worker_shell_versions" in sql:
            return _Result([selected_shell] if selected_shell is not None else [])
        if "FROM runtime_adapters" in sql:
            return _Result([selected_adapter] if selected_adapter is not None else [])
        if "FROM skill_bundles" in sql:
            return _Result([selected_skill_bundle] if selected_skill_bundle is not None else [])
        if sql.lstrip().startswith(("UPDATE worker_runs", "INSERT INTO worker_run_transitions")):
            return SimpleNamespace(rowcount=1)
        raise AssertionError("unexpected SQL statement in claim test")

    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=execute)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine
    return storage, statements


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_state", "assignment", "expected_statement_count"),
    [
        ("COMPLETED", _assignment(), 2),
        ("SECURITY_BLOCKED", _assignment(), 2),
        (" IN_PROGRESS ", _assignment(), 2),
        ("IN_PROGRESS", None, 3),
        ("IN_PROGRESS", _assignment(status="INACTIVE"), 3),
        ("IN_PROGRESS", _assignment(status="active"), 3),
        ("IN_PROGRESS", _assignment(approval_required=True), 3),
        ("IN_PROGRESS", _assignment(tool_grants=[]), 3),
        ("IN_PROGRESS", _assignment(permission_grants=[]), 3),
    ],
)
async def test_claim_rechecks_project_and_assignment_before_any_write(
    project_state: str,
    assignment: dict[str, object] | None,
    expected_statement_count: int,
) -> None:
    storage, statements = _storage_for_claim(
        project={"id": PROJECT_ID, "company_id": COMPANY_ID, "state": project_state},
        assignments=[] if assignment is None else [assignment],
    )

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is None
    assert len(statements) == expected_statement_count
    assert not any(
        str(statement).lstrip().startswith(("UPDATE worker_runs", "INSERT INTO worker_run_transitions"))
        for statement in statements
    )
    assert any("FROM projects" in str(statement) and "FOR UPDATE" in str(statement) for statement in statements)
    if expected_statement_count == 3:
        assert any(
            "FROM company_worker_assignments" in str(statement)
            and "FOR UPDATE" in str(statement)
            for statement in statements
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("project_state", ["COMPLETED", "SECURITY_BLOCKED"])
async def test_new_worker_run_locks_project_and_rejects_nondispatchable_state(
    project_state: str,
) -> None:
    statements: list[object] = []

    async def execute(statement: object) -> _Result:
        statements.append(statement)
        sql = str(statement)
        if "FROM worker_runs" in sql:
            return _Result()
        if "FROM projects" in sql:
            return _Result([{"id": PROJECT_ID, "state": project_state}])
        raise AssertionError("worker or run writes must not be reached for stale project state")

    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=execute)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    with pytest.raises(ValueError, match="project state changed before WorkerRun dispatch"):
        await storage.create_worker_run(
            run_id=RUN_ID,
            worker_id=WORKER_ID,
            idempotency_key="project-state-race",
            task_type="test",
            request={"worker_id": str(WORKER_ID), "project_id": str(PROJECT_ID)},
            project_id=PROJECT_ID,
        )

    assert len(statements) == 2
    assert "FROM worker_runs" in str(statements[0])
    assert "FROM projects" in str(statements[1])
    assert "FOR UPDATE" in str(statements[1]).upper()


@pytest.mark.asyncio
async def test_worker_run_idempotency_recheck_does_not_lock_after_project() -> None:
    statements: list[object] = []
    run_select_count = 0
    concurrent_run = {
        "id": UUID("00000000-0000-4000-a000-000000000909"),
        "worker_id": WORKER_ID,
        "project_id": PROJECT_ID,
        "idempotency_key": "same-request",
        "state": "QUEUED",
    }

    async def execute(statement: object) -> _Result:
        nonlocal run_select_count
        statements.append(statement)
        sql = str(statement)
        if "FROM worker_runs" in sql and sql.lstrip().startswith("SELECT"):
            run_select_count += 1
            return _Result([] if run_select_count == 1 else [concurrent_run])
        if "FROM projects" in sql:
            return _Result([{"id": PROJECT_ID, "state": "IN_PROGRESS"}])
        if "FROM worker_registry" in sql:
            return _Result([_worker(active_skill_bundle_id=None)])
        raise AssertionError("an existing idempotent run should return before writes")

    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=execute)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    result = await storage.create_worker_run(
        run_id=RUN_ID,
        worker_id=WORKER_ID,
        idempotency_key="same-request",
        task_type="test",
        request={"worker_id": str(WORKER_ID), "project_id": str(PROJECT_ID)},
        project_id=PROJECT_ID,
    )

    assert result == concurrent_run
    sql = [str(statement) for statement in statements]
    assert "FOR UPDATE" in sql[0].upper()  # lookup before acquiring project
    project_index = next(i for i, value in enumerate(sql) if "FROM projects" in value)
    worker_index = next(i for i, value in enumerate(sql) if "FROM worker_registry" in value)
    recheck_index = max(i for i, value in enumerate(sql) if "FROM worker_runs" in value)
    assert "FOR UPDATE" in sql[project_index].upper()
    assert "FOR UPDATE" in sql[worker_index].upper()
    assert project_index < worker_index < recheck_index
    assert "FOR UPDATE" not in sql[recheck_index].upper()


@pytest.mark.asyncio
async def test_retry_lookup_does_not_lock_claimable_existing_retry() -> None:
    statements: list[object] = []
    worker_run_select_count = 0
    existing_retry = {
        "id": UUID("00000000-0000-4000-a000-000000000910"),
        "worker_id": WORKER_ID,
        "project_id": PROJECT_ID,
        "idempotency_key": "retry-request",
        "retry_of_run_id": UUID("00000000-0000-4000-a000-000000000911"),
        "state": "QUEUED",
    }

    async def execute(statement: object) -> _Result:
        nonlocal worker_run_select_count
        statements.append(statement)
        sql = str(statement)
        if "FROM worker_runs" in sql and sql.lstrip().startswith("SELECT"):
            worker_run_select_count += 1
            if worker_run_select_count == 1:
                return _Result([])
            if worker_run_select_count == 2:
                return _Result(
                    [
                        {
                            "id": existing_retry["retry_of_run_id"],
                            "worker_id": WORKER_ID,
                            "state": "FAILED",
                        }
                    ]
                )
            if worker_run_select_count == 3:
                return _Result([existing_retry])
            raise AssertionError("unexpected worker-run lookup")
        if "FROM projects" in sql:
            return _Result([{"id": PROJECT_ID, "state": "IN_PROGRESS"}])
        if "FROM worker_registry" in sql:
            return _Result([_worker(active_skill_bundle_id=None)])
        raise AssertionError("the existing retry should return before writes")

    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=execute)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    result = await storage.create_worker_run(
        run_id=RUN_ID,
        worker_id=WORKER_ID,
        idempotency_key="retry-request",
        task_type="test",
        request={"worker_id": str(WORKER_ID), "project_id": str(PROJECT_ID)},
        project_id=PROJECT_ID,
        retry_of_run_id=existing_retry["retry_of_run_id"],
    )

    assert result == existing_retry
    sql = [str(statement) for statement in statements]
    worker_run_indexes = [
        i for i, value in enumerate(sql)
        if "FROM worker_runs" in value and value.lstrip().startswith("SELECT")
    ]
    project_index = next(i for i, value in enumerate(sql) if "FROM projects" in value)
    worker_index = next(i for i, value in enumerate(sql) if "FROM worker_registry" in value)
    initial_index, source_index, retry_index = worker_run_indexes
    assert initial_index < project_index < worker_index < source_index < retry_index
    assert "FOR UPDATE" in sql[initial_index].upper()
    assert "FOR UPDATE" in sql[source_index].upper()  # terminal retry source
    assert "FOR UPDATE" not in sql[retry_index].upper()  # may be claimed concurrently


@pytest.mark.asyncio
async def test_claim_locks_current_scope_before_persisting_claim() -> None:
    storage, statements = _storage_for_claim()

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is not None
    assert claimed["state"] == "CLAIMED"
    assert claimed["claim_owner"] == "host-executor"
    sql = [str(statement) for statement in statements]
    assert "FROM projects" in sql[1] and "FOR UPDATE" in sql[1]
    assert "FROM company_worker_assignments" in sql[2] and "FOR UPDATE" in sql[2]
    update_index = next(
        index
        for index, statement in enumerate(sql)
        if statement.lstrip().startswith("UPDATE worker_runs")
    )
    transition_index = next(
        index
        for index, statement in enumerate(sql)
        if statement.lstrip().startswith("INSERT INTO worker_run_transitions")
    )
    locked_registry_reads = sql[:update_index]
    for table in (
        "worker_registry",
        "worker_shell_versions",
        "runtime_adapters",
        "skill_bundles",
    ):
        assert any(f"FROM {table}" in statement and "FOR UPDATE" in statement for statement in locked_registry_reads)
    assert transition_index > update_index


@pytest.mark.asyncio
async def test_projectless_claim_preserves_existing_path_without_company_reads() -> None:
    storage, statements = _storage_for_claim(run=_run_row(project_id=None))

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is not None
    sql = [str(statement) for statement in statements]
    assert not any("FROM projects" in statement for statement in sql)
    assert not any("FROM company_worker_assignments" in statement for statement in sql)
    assert all(
        "FOR UPDATE" in sql[index]
        for index in (1, 2, 3, 4)
    )
    assert sql[5].lstrip().startswith("UPDATE worker_runs")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table_row", "changed"),
    [
        ("worker", {"status": "INACTIVE"}),
        ("worker", {"evaluation_status": "pending"}),
        ("shell", {"status": "revoked"}),
        ("adapter", {"status": "revoked"}),
        ("adapter", {"conformance_status": "failed"}),
        ("skill_bundle", {"status": "REVOKED"}),
    ],
)
async def test_claim_revalidates_locked_worker_and_pins_before_any_write(
    table_row: str,
    changed: dict[str, object],
) -> None:
    rows = {
        "worker": _worker(),
        "shell": _shell(),
        "adapter": _adapter(),
        "skill_bundle": _skill_bundle(),
    }
    rows[table_row].update(changed)
    storage, statements = _storage_for_claim(
        worker=rows["worker"],
        shell=rows["shell"],
        adapter=rows["adapter"],
        skill_bundle=rows["skill_bundle"],
    )

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is None
    sql = [str(statement) for statement in statements]
    assert not any(
        statement.lstrip().startswith(("UPDATE worker_runs", "INSERT INTO worker_run_transitions"))
        for statement in sql
    )
    assert all("FOR UPDATE" in statement for statement in sql[1:])


@pytest.mark.asyncio
async def test_claim_keeps_draining_worker_and_superseded_pins_eligible() -> None:
    storage, statements = _storage_for_claim(
        worker=_worker(status="DRAINING"),
        shell=_shell(status="superseded"),
        adapter=_adapter(status="superseded"),
        skill_bundle=_skill_bundle(status="SUPERSEDED"),
    )

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is not None
    sql = [str(statement) for statement in statements]
    assert sql[7].lstrip().startswith("UPDATE worker_runs")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shell_changes", "adapter_changes"),
    [
        ({"provenance_json": {"runtime_adapter_id": str(ADAPTER_ID)}}, {}),
        ({}, {"content_hash": "different-immutable-adapter"}),
    ],
)
async def test_claim_rejects_missing_or_changed_execution_lineage_before_write(
    shell_changes: dict[str, object],
    adapter_changes: dict[str, object],
) -> None:
    shell = _shell(**shell_changes)
    adapter = _adapter(**adapter_changes)
    storage, statements = _storage_for_claim(shell=shell, adapter=adapter)

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is None
    sql = [str(statement).lstrip() for statement in statements]
    assert not any(
        statement.startswith(("UPDATE worker_runs", "INSERT INTO worker_run_transitions"))
        for statement in sql
    )
