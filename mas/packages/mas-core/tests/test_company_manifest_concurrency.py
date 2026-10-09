"""Storage compares manifest revisions while holding the company row lock."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

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
        return _Result(next(self.rows))


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
