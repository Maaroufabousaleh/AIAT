"""Deterministic API and protocol contract checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_api_contract.py"
TYPESCRIPT_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "generate_typescript_api.py"
PYTHON_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "generate_python_api.py"


def test_checked_in_api_and_protocol_contracts_match_runtime() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["openapi"]["path_count"] >= 200
    assert report["protocol"]["protocol_version"] == "aiat.v1"
    assert report["python_sdk"]["model_count"] == 130
    assert report["python_sdk"]["operation_count"] == 268


def test_dashboard_typescript_contract_is_not_stale() -> None:
    result = subprocess.run(
        [sys.executable, str(TYPESCRIPT_SCRIPT), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "130 models" in result.stdout
    assert "268 operations" in result.stdout


def test_python_sdk_contract_is_not_stale() -> None:
    result = subprocess.run(
        [sys.executable, str(PYTHON_SCRIPT), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "130 models" in result.stdout
    assert "268 operations" in result.stdout
