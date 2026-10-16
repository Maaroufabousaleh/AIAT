"""Storage compares manifest revisions while holding the company row lock."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from mas_core.company_manifest import CompanyManifestError, compile_company_manifest
from mas_core.memory.storage import AgentStorage


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _Connection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        try:
            row = next(self.rows)
        except StopIteration:
            row = None
        return _Result(row)


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_exc):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return _Transaction(self.connection)


@pytest.mark.anyio
async def test_stale_manifest_version_is_rejected_before_insert_under_row_lock():
    company_id = uuid4()
    active_manifest_id = uuid4()
    connection = _Connection(
        [
            {"id": company_id, "active_manifest_version_id": active_manifest_id},
            None,  # This digest has not already been applied.
            {"manifest_version": 2},
        ]
    )
    storage = AgentStorage(dsn="postgresql+asyncpg://unused:unused@localhost/unused")
    storage._engine = _Engine(connection)  # type: ignore[assignment]

    with pytest.raises(ValueError, match="COMPANY_MANIFEST_VERSION_CONFLICT"):
        await storage.apply_company_manifest(
            company_id=company_id,
            manifest=object(),
            digest="validated-against-version-1",
            canonical={"slug": "team"},
            source="dashboard_operator",
            actor="operator",
            expected_manifest_version=1,
        )

    assert len(connection.queries) == 3
    locked_company_query = str(connection.queries[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in locked_company_query.upper()
    assert all("INSERT" not in str(query).upper() for query in connection.queries)


@pytest.mark.anyio
async def test_manifest_materialization_requires_operator_for_external_approval():
    worker_id = uuid4()
    company_id = uuid4()
    manifest, _digest, _canonical = compile_company_manifest(
        {
            "slug": "external-approval-test",
            "name": "External Approval Test",
            "ceo_worker_id": "external_worker",
            "departments": [
                {
                    "id": "department",
                    "name": "Department",
                    "chief_worker_id": "external_worker",
                    "worker_ids": ["external_worker"],
                }
            ],
            "worker_assignments": [
                {
                    "worker_id": "external_worker",
                    "department_id": "department",
                    "approval_required": False,
                }
            ],
            "policy": {"require_human_approval_for_external_workers": True},
        }
    )
    connection = _Connection(
        [
            {
                "id": worker_id,
                "name": "external_worker",
                "source_repo": "https://example.invalid/worker",
                "adapter_config": {},
            }
        ]
    )
    storage = AgentStorage(dsn="postgresql+asyncpg://unused:unused@localhost/unused")

    with pytest.raises(CompanyManifestError, match="approval must be applied by the operator"):
        await storage._materialize_company_manifest_tx(
            connection,
            company_id=company_id,
            manifest=manifest,
            manifest_id=uuid4(),
        )

    assert len(connection.queries) == 1

    operator_connection = _Connection(
        [
            {
                "id": worker_id,
                "name": "external_worker",
                "source_repo": "https://example.invalid/worker",
                "adapter_config": {},
            }
        ]
    )
    await storage._materialize_company_manifest_tx(
        operator_connection,
        company_id=company_id,
        manifest=manifest,
        manifest_id=uuid4(),
        actor="operator",
    )
    assert len(operator_connection.queries) > 1
