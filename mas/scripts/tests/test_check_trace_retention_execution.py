from __future__ import annotations

import json

from check_trace_retention_execution import _blocked, build_report


def test_fixture_execution_check_rehearses_preview_and_apply() -> None:
    report = build_report()

    assert report["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False
    assert report["preview"] == {
        "status": "preview",
        "mutation_performed": False,
        "action_count": 2,
        "held_count": 2,
        "invalid_count": 1,
    }
    assert report["apply"] == {
        "status": "applied",
        "mutation_performed": True,
        "archived_count": 1,
        "deleted_count": 1,
        "held_count": 2,
        "audit_count": 1,
    }
    assert "backup://fixture" not in json.dumps(report)


def test_live_execution_check_fails_closed_without_mutation() -> None:
    report = _blocked("adapter unavailable")

    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert "no storage mutation" in str(report["scope"])
