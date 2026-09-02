from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mas_core.observability.postgres_retention import (
    PostgresNativeTraceRetentionStore,
    _manifest_digest,
    _sync_dsn,
)
from mas_core.observability.retention_execution import (
    RetentionAction,
    RetentionExecutionError,
)


def _row(record_id: str) -> dict[str, object]:
    return {
        "id": record_id,
        "trace_id": "trace-1",
        "span_id": "span-1",
        "parent_span_id": None,
        "source_kind": "tool",
        "operation": "fixture",
        "service": "test",
        "status": "success",
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "ended_at": datetime(2026, 1, 1, tzinfo=UTC),
        "duration_ms": "1",
        "sampled": True,
        "retention_until": None,
        "attributes_json": {"fixture": True},
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


def test_sync_dsn_converts_supported_sqlalchemy_schemes() -> None:
    assert _sync_dsn("postgresql+asyncpg://user:pass@db/mas") == "postgresql://user:pass@db/mas"
    assert _sync_dsn("postgres://user:pass@db/mas") == "postgresql://user:pass@db/mas"


def test_manifest_digest_is_order_independent() -> None:
    first = _row("00000000-0000-4000-a000-000000000001")
    second = _row("00000000-0000-4000-a000-000000000002")
    assert _manifest_digest([first, second]) == _manifest_digest([second, first])


def test_local_adapter_rejects_archive_before_opening_database() -> None:
    store = PostgresNativeTraceRetentionStore(
        "postgresql+asyncpg://user:pass@db/mas",
        trace_id="trace-1",
    )
    with pytest.raises(RetentionExecutionError, match="does not archive"):
        store.apply_retention_actions(
            [
                RetentionAction(
                    record_id="00000000-0000-4000-a000-000000000001",
                    action="archive",
                )
            ],
            audit={},
        )
