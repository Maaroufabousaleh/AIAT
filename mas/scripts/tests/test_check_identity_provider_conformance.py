"""Tests for the payload-free identity-provider conformance checker."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_identity_provider_conformance.py"


def test_mocked_identity_provider_conformance_passes_without_external_access() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.identity-provider-conformance.v1"
    assert report["status"] == "pass"
    assert report["providers"] == ["resend", "stalwart"]
    assert report["case_count"] == report["passed_case_count"] == 11
    assert report["error_count"] == 0
    assert report["external_network_access_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["payload_free"] is True
    assert report["secret_safe_report"] is True
    assert "fixture body" not in result.stdout
    assert "fixture-management-token" not in result.stdout


def test_live_identity_provider_conformance_is_explicitly_blocked() -> None:
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
    assert report["external_network_access_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False
