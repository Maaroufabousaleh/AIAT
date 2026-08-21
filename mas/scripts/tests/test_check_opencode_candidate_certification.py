"""Tests for the retained OpenCode candidate evidence boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_opencode_candidate_certification.py"
EVIDENCE = SCRIPT.parents[1] / "docs" / "provenance" / "opencode-candidate" / "2026-08-21-v1.18.21" / "candidate-certification.json"


def test_current_candidate_evidence_is_structurally_valid_but_technical_gate_blocked() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "--json"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["candidate_status"] == "blocked"
    assert report["technical_gate_status"] == "blocked"
    assert report["scanner_error_count"] == 3
    assert report["active_worker_status"] == "inactive_until_certification_passes"


def test_candidate_checker_rejects_mutable_image(tmp_path: Path) -> None:
    value = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    value["candidate_image_ref"] = "ghcr.io/anomalyco/opencode:latest"
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), "--path", str(path), "--json"], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert "candidate image must be digest pinned" in report["errors"]
