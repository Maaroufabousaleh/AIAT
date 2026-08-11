"""Tests for the secret-safe release environment manifest."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_release_environment.py"


def _run_manifest(*extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    environment = os.environ.copy()
    environment["AIAT_API_KEY"] = "secret-test-value"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", *extra],
        cwd=SCRIPT.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, json.loads(result.stdout)


def test_release_environment_manifest_is_secret_safe_and_deterministic() -> None:
    first_result, first = _run_manifest()
    second_result, second = _run_manifest()

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert first["schema_version"] == "aiat.release-environment.v1"
    assert first["status"] == "pass"
    assert first["manifest_digest"] == second["manifest_digest"]
    assert re.fullmatch(r"[0-9a-f]{64}", str(first["manifest_digest"]))
    assert first["git"]["revision"]
    assert len(first["tracked_inputs"]) == 13
    assert "docs/provenance/operator_pins.yaml" in {
        str(item["path"]) for item in first["tracked_inputs"]
    }
    assert "docs/provenance/security_scan_evidence.yaml" in {
        str(item["path"]) for item in first["tracked_inputs"]
    }
    assert "secret-test-value" not in json.dumps(first)
    assert all("value" not in item for item in first["environment_presence"])


def test_release_environment_manifest_reports_dirty_worktree_without_failing_default() -> None:
    result, report = _run_manifest()
    assert result.returncode == 0
    assert isinstance(report["git"]["working_tree_clean"], bool)
    if report["git"]["working_tree_clean"]:
        assert report["errors"] == []
    else:
        assert "working tree is dirty" not in report["errors"]
