"""Contract tests for the bounded live flow-runtime certificate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_flow_runtime_live.py"


def _run(*args: str) -> tuple[int, dict[str, object]]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(result.stdout)


def test_fixture_report_is_scalar_and_complete() -> None:
    code, report = _run()

    assert code == 0
    assert report["schema_version"] == "aiat.flow-runtime-live.v1"
    assert report["status"] == "pass"
    assert report["case_count"] == report["passed_case_count"] == 8
    assert report["failed_case_count"] == 0
    assert report["payload_free"] is True
    assert report["secret_free"] is True
    assert report["licence_metadata_is_gate"] is False
    assert report["mutation_performed"] is False


def test_live_requires_explicit_confirmation() -> None:
    code, report = _run("--live")

    assert code == 2
    assert report["status"] == "blocked"
    assert report["failure_classification"]["harness_configuration_failure"]
