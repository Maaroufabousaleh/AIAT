"""CLI evidence tests for the provider conformance fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_provider_conformance.py"


def test_provider_conformance_fixture_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.provider-conformance-runner.v1"
    assert report["mode"] == "fixture"
    assert report["status"] == "pass"
    assert report["fixture_report"]["fixture_version"] == "aiat.provider-conformance.v1"
    assert report["fixture_report"]["counts"]["FAIL"] == 0


def test_provider_conformance_live_mode_is_explicitly_blocked() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["mode"] == "live"
    assert report["status"] == "blocked"
    assert report["fixture_report"] is None
