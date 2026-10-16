"""Atomic model-profile version approval persistence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage


def _result(*, row=None, rowcount: int | None = None):
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    result.rowcount = rowcount
    return result


def _storage_with_connection(conn: AsyncMock) -> AgentStorage:
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=conn)
    begin.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = begin
    storage = AgentStorage("postgresql+asyncpg://unused")
    storage._engine = engine
    return storage


def _statement_values(statement) -> dict[str, object]:
    values = {}
    for column, value in statement._values.items():
        name = column.name if hasattr(column, "name") else str(column)
        values[name] = value.value if hasattr(value, "value") else value
    return values


@pytest.mark.anyio
async def test_activation_atomically_updates_exact_version_and_records_audit() -> None:
    profile_id = uuid4()
    version_id = uuid4()
    profile = {
        "id": profile_id,
        "logical_profile_id": "coding",
        "purpose": "Governed coding tasks",
        "approved_provider_ids": ["local-fixture"],
        "required_capabilities": ["tool_calling"],
        "fallback_profile_ids": [],
        "status": "draft",
    }
    version = {
        "id": version_id,
        "profile_id": profile_id,
        "version": "v1",
        "provider_id": "local-fixture",
        "exact_model_id": "fixture/no-dispatch",
        "api_version": None,
        "capabilities": ["tool_calling"],
        "constraints_json": {"context_window": 8192},
        "provider_settings": {"mode": "local-fixture"},
        "status": "draft",
        "effective_from": None,
        "effective_until": None,
    }
    approval = {
        "id": uuid4(),
        "scope_type": "model_profile_version",
        "scope_id": version_id,
        "decision": "APPROVED",
        "decided_by": "operator",
        "reason": "Reviewed exact local model version",
        "evidence": {},
        "expires_at": None,
        "created_at": datetime(2026, 10, 7, tzinfo=UTC),
    }
    conn = AsyncMock()
    results = [
        _result(row=profile),
        _result(row=version),
        _result(rowcount=1),
        _result(rowcount=1),
    ]

    async def execute(statement):
        if statement.is_insert:
            evidence = _statement_values(statement)["evidence"]
            return _result(row={**approval, "evidence": evidence})
        return results.pop(0)

    conn.execute.side_effect = execute
    storage = _storage_with_connection(conn)

    result = await storage.activate_model_profile_version(
        logical_profile_id="coding",
        version_id=version_id,
        decided_by="operator",
        reason="Reviewed exact local model version",
    )

    assert result is not None
    assert result["profile"]["status"] == "approved"
    assert result["version"]["id"] == version_id
    assert result["version"]["status"] == "approved"
    assert result["approval_record"]["scope_id"] == version_id
    assert result["approval_record"]["decided_by"] == "operator"
    assert result["approval_record"]["reason"] == "Reviewed exact local model version"
    assert result["approval_record"]["created_at"] == approval["created_at"]
    evidence = result["approval_record"]["evidence"]
    assert evidence["profile_id"] == "coding"
    assert evidence["version_id"] == str(version_id)
    assert evidence["provider_id"] == "local-fixture"
    assert evidence["exact_model_id"] == "fixture/no-dispatch"
    assert len(evidence["version_snapshot_sha256"]) == 64

    assert conn.execute.await_count == 5
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert statements[0]._for_update_arg is not None
    assert statements[1]._for_update_arg is not None
    assert _statement_values(statements[2]) == {"status": "approved"}
    assert _statement_values(statements[3]) == {"status": "approved"}
    audit_values = _statement_values(statements[4])
    assert audit_values["scope_id"] == version_id
    assert audit_values["decided_by"] == "operator"
    assert audit_values["reason"] == "Reviewed exact local model version"
    assert audit_values["evidence"]["version_snapshot_sha256"] == evidence["version_snapshot_sha256"]
