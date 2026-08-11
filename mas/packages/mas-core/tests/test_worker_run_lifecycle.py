"""Deterministic worker-run lifecycle release evidence."""

import json
import subprocess
import sys
from pathlib import Path


def test_worker_run_lifecycle_fixture_covers_controller_invariants() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_run_lifecycle.py", "--json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["schema_version"] == "aiat.worker-run-lifecycle-check.v1"
    assert report["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False
    assert report["failed_checks"] == []
    assert set(report["checks"]) == {
        "checkpoint_and_artifact_order",
        "pause_resume_checkpoint_restore",
        "cold_cancellation",
        "cold_crash_failure",
        "lease_expiry_recovery",
    }
    assert all(row["status"] == "pass" for row in report["checks"].values())
    assert report["checks"]["checkpoint_and_artifact_order"]["artifact_before_terminal"] is True
    assert report["checks"]["checkpoint_and_artifact_order"]["usage_before_terminal"] is True
    assert report["checks"]["cold_crash_failure"]["error_code"] == "RUNTIME_ERROR"
    assert report["certification_boundary"]["database"] == "not_checked"
    assert report["certification_boundary"]["live_worker_run"] == "not_checked"


def test_worker_run_lifecycle_live_mode_is_explicitly_blocked_without_mutation() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_run_lifecycle.py", "--live", "--json"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert report["licence_metadata_is_gate"] is False
    assert "no live worker-run mutation was attempted" in report["scope"]
