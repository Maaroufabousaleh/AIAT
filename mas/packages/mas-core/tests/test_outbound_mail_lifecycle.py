"""Fixture contract for outbound-mail approval, retry, and outage state."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_outbound_mail_lifecycle.py"


def test_outbound_mail_lifecycle_fixture_passes_without_external_mutation() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.outbound-mail-lifecycle.v1"
    assert report["status"] == "pass"
    assert report["case_count"] == report["passed_case_count"] == 6
    assert report["external_relay_calls"] == 0
    assert report["network_access_performed"] is False
    assert report["mutation_performed"] is False
    assert report["secret_safe_report"] is True
    assert report["licence_metadata_is_gate"] is False
    rendered = json.dumps(report, sort_keys=True)
    assert "recipient@example.net" not in rendered
    assert "fixture body must not enter the report" not in rendered


def test_outbound_mail_lifecycle_live_profile_is_explicitly_blocked() -> None:
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
    assert report["licence_metadata_is_gate"] is False
