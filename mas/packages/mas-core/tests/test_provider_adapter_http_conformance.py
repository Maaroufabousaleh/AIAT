"""Tests for the real-adapter mocked HTTP conformance fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_provider_adapter_http_conformance.py"


def test_provider_adapter_http_conformance_fixture_passes_without_network() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["provider_count"] == 2
    assert report["case_count"] == 8
    assert report["passed_case_count"] == 8
    assert report["network_access_performed"] is False
    assert report["mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False
    assert all(case["passed"] for case in report["cases"])


def test_provider_adapter_http_conformance_live_mode_is_explicitly_blocked() -> None:
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
