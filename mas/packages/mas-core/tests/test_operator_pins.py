"""Tests for exact operator-managed runtime/CLI pin declarations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_operator_pins.py"


def test_operator_pin_contract_is_exact_or_explicitly_unavailable() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.operator-pin-contract.v1"
    assert report["status"] == "pass"
    assert report["locked_count"] >= 8
    assert report["unavailable_count"] >= 5
    assert report["policy"]["licence_metadata_is_gate"] is False
    uv = next(row for row in report["pins"] if row["id"] == "uv")
    assert uv["version"] == "0.4.30"
    assert uv["checked_assertion_count"] == 4


def test_operator_pin_contract_rejects_unavailable_without_reason(tmp_path: Path) -> None:
    pins = tmp_path / "pins.yaml"
    pins.write_text(
        """
schema_version: aiat.operator-pin-contract.v1
programme_scope: personal-internal-only
policy:
  exact_pin_required: true
  unavailable_is_explicit: true
  licence_metadata_is_gate: false
pins:
  - id: missing-reason
    kind: test
    version: null
    status: unavailable
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--pins", str(pins), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "fail"
    assert "unavailable pins require a reason" in " ".join(report["errors"])
