"""Tests for the deterministic external-account lifecycle verifier."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_external_account_lifecycle.py"


def test_external_account_lifecycle_fixture_passes_without_external_state() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        env={**os.environ, "PYTHONPATH": "apps/identity-service"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["case_count"] == 8
    assert report["passed_case_count"] == 8
    assert report["external_provider_calls"] == 0
    assert report["network_access_performed"] is False
    assert report["mutation_performed"] is False
    assert report["secret_safe_report"] is True
    assert report["licence_metadata_is_gate"] is False


def test_external_account_lifecycle_live_mode_is_explicitly_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert report["network_access_performed"] is False
    assert report["licence_metadata_is_gate"] is False
