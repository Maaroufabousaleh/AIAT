"""Tests for deterministic evidence-policy scope resolution."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_evidence_policy_resolution_fixture_passes() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_evidence_policy_resolution.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["case_count"] == 7
    assert report["licence_metadata_is_gate"] is False


def test_evidence_policy_resolution_live_mode_is_explicitly_blocked() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_evidence_policy_resolution.py"
    result = subprocess.run(
        [sys.executable, str(script), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
