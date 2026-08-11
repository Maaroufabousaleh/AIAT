"""Tests for the deterministic default-runtime adapter conformance probe."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAS_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = MAS_ROOT / "scripts" / "check_runtime_adapter_conformance.py"


def test_runtime_adapter_fixture_conformance_exercises_both_default_adapters() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.runtime-adapter-conformance.v1"
    assert report["mode"] == "fixture"
    assert report["status"] == "pass"
    assert report["certification_boundary"]["framework_execution"] == "fixture_only"
    assert {row["runtime_id"] for row in report["runtimes"]} == {"langgraph", "crewai"}
    assert all(row["status"] == "pass" for row in report["runtimes"])
    assert all(row["external_model_call"] is False for row in report["runtimes"])


def test_runtime_adapter_live_mode_is_blocked_when_packages_are_absent() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    # The host development venv intentionally does not install the optional
    # framework packages; the Compose image is the environment for live import
    # evidence. If a developer has installed both packages locally, live mode
    # should pass instead of being treated as a failure.
    if report["status"] == "blocked":
        assert result.returncode == 2
        assert any(row["status"] == "blocked" for row in report["runtimes"])
    else:
        assert result.returncode == 0
        assert report["status"] == "pass"
