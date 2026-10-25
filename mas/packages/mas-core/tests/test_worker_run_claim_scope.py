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
) -> tuple[AgentStorage, list[object]]:
    selected_run = run or _run_row()
    selected_project = project or {
        "id": PROJECT_ID,
        "company_id": COMPANY_ID,
        "state": "IN_PROGRESS",
    }
    selected_assignments = assignments if assignments is not None else [_assignment()]
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
        ("IN_PROGRESS", None, 3),
        ("IN_PROGRESS", _assignment(status="INACTIVE"), 3),
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
    assert sql[3].lstrip().startswith("UPDATE worker_runs")
    assert sql[4].lstrip().startswith("INSERT INTO worker_run_transitions")


@pytest.mark.asyncio
async def test_projectless_claim_preserves_existing_path_without_company_reads() -> None:
    storage, statements = _storage_for_claim(run=_run_row(project_id=None))

    claimed = await storage.claim_worker_run(owner="host-executor", run_id=RUN_ID)

    assert claimed is not None
    sql = [str(statement) for statement in statements]
    assert not any("FROM projects" in statement for statement in sql)
    assert not any("FROM company_worker_assignments" in statement for statement in sql)
    assert sql[1].lstrip().startswith("UPDATE worker_runs")
