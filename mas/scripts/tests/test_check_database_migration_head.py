"""Tests for the read-only database migration-head checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_database_migration_head.py"
SPEC = importlib.util.spec_from_file_location("check_database_migration_head", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_source_graph_has_one_current_head() -> None:
    revisions, heads = MODULE.source_migration_heads()

    assert len(revisions) >= 45
    assert heads == {MODULE.EXPECTED_MIGRATION_HEAD}


def test_static_report_is_payload_free_and_passes() -> None:
    report = MODULE.build_report()

    assert report["status"] == "pass"
    assert report["migration_source_heads"] == [MODULE.EXPECTED_MIGRATION_HEAD]
    assert report["live_database_head"] is None
    assert report["local_database_access_performed"] is False
    assert report["mutation_performed"] is False


def test_live_report_blocks_without_a_dsn() -> None:
    report = MODULE.build_report(live=True, dsn=None)

    assert report["status"] == "blocked"
    assert "DSN" in report["reason"]
    assert report["local_database_access_performed"] is False


def test_dsn_normalization_rejects_templates_and_preserves_scheme() -> None:
    assert MODULE._normalize_dsn("postgresql://user:pass@localhost/db").startswith(
        "postgresql+asyncpg://"
    )
    assert MODULE._normalize_dsn("${POSTGRES_DSN}") is None
    assert MODULE._normalize_dsn("sqlite:///tmp/db") is None
