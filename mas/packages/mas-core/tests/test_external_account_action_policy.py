"""Tests for the deterministic external-account action-policy verifier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_external_account_action_policy_fixture_passes() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_external_account_action_policy.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["catalog_schema"] == "aiat.external-account-action-policy.v1"
    assert report["action_count"] == 5
    assert report["unknown_action_denied"] is True
    assert report["unknown_category_denied"] is True
    assert report["licence_metadata_is_gate"] is False


def test_external_account_action_policy_live_mode_is_explicitly_blocked() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_external_account_action_policy.py"
    result = subprocess.run(
        [sys.executable, str(script), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked"
