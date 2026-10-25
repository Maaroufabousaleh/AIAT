from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from mas_core.memory.storage import AgentStorage

RUN_ID = UUID("00000000-0000-4000-a000-000000000901")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000902")
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000903")
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000904")
SHELL_ID = UUID("00000000-0000-4000-a000-000000000905")
ADAPTER_ID = UUID("00000000-0000-4000-a000-000000000906")
SKILL_ID = UUID("00000000-0000-4000-a000-000000000907")
STEWARD_ID = UUID("00000000-0000-4000-a000-000000000908")


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
    return {
        "id": SHELL_ID,
        "worker_id": WORKER_ID,
        "status": "active",
        **overrides,
    }


def _adapter(**overrides: object) -> dict[str, object]:
    return {
        "id": ADAPTER_ID,
        "worker_id": WORKER_ID,
        "status": "active",
        "conformance_status": "passed",
        "transport_type": "native",
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
async def test_claim_locks_current_scope_before_persisting_claim() -> None:
    storage, statements = _storage_for_claim()

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is not None
    assert claimed["state"] == "CLAIMED"
    assert claimed["claim_owner"] == "host-executor"
    sql = [str(statement) for statement in statements]
    assert "FROM projects" in sql[1] and "FOR UPDATE" in sql[1]
    assert "FROM company_worker_assignments" in sql[2] and "FOR UPDATE" in sql[2]
    assert "FROM worker_registry" in sql[3] and "FOR UPDATE" in sql[3]
    assert "FROM worker_shell_versions" in sql[4] and "FOR UPDATE" in sql[4]
    assert "FROM runtime_adapters" in sql[5] and "FOR UPDATE" in sql[5]
    assert "FROM skill_bundles" in sql[6] and "FOR UPDATE" in sql[6]
    assert sql[7].lstrip().startswith("UPDATE worker_runs")
    assert sql[8].lstrip().startswith("INSERT INTO worker_run_transitions")


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
