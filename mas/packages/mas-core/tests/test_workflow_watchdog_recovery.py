"""Tests for deterministic watchdog and safe-retry evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_workflow_watchdog_recovery.py"


def test_watchdog_recovery_fixture_passes_without_mutation() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.workflow-watchdog-recovery.v1"
    assert report["status"] == "pass"
    assert report["controller"] == {
        "storage": False,
        "worker_dispatch": False,
        "mutation": False,
    }
    assert report["licence_metadata"]["affects_discovery_install_activation_or_execution"] is False


def test_watchdog_recovery_live_boundary_is_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked"
