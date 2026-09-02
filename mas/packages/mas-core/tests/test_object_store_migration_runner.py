"""Deterministic object-store migration workflow CLI coverage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_object_store_migration.py"


def test_migration_fixture_is_deterministic() -> None:
    env = os.environ.copy()
    first = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    first_report = json.loads(first.stdout)
    second_report = json.loads(second.stdout)
    assert first_report == second_report
    assert first_report["schema_version"] == "aiat.object-store-migration.v1"
    assert first_report["status"] == "pass"
    assert first_report["final_workflow_status"] == "ROLLED_BACK"
    assert first_report["active_bucket"] == "source"


def test_migration_live_boundary_is_explicitly_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert "provider-specific migration environment" in report["reason"]
