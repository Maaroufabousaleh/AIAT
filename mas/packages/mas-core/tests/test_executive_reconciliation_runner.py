"""Tests for the bounded executive reconciliation live verifier."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_executive_reconciliation.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("check_executive_reconciliation", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_live_configuration_is_blocked_without_secret_output() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json", "--api-key", "secret-value"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "secret-value" not in result.stdout
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.executive-reconciliation-check.v1"
    assert report["status"] == "blocked"


def test_executive_reconciliation_summarizes_and_can_require_clean(monkeypatch) -> None:
    runner = _load_runner()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "schema_version": "aiat.executive-reconciliation.v1",
                "status": "reconciled_with_findings",
                "coverage": {
                    "project_count": 2,
                    "project_usage_count": 2,
                    "worker_run_count": 3,
                    "budget_count": 1,
                    "budget_reservation_count": 3,
                    "secret_value": "must not be copied",
                },
                "findings": [{"code": "MODEL_PROFILE_COVERAGE_PENDING"}],
                "projects": {"secret": "must not be copied"},
            }

    monkeypatch.setattr(runner.httpx, "get", lambda *args, **kwargs: Response())
    report = runner.inspect_live(
        url="http://orchestrator.invalid",
        api_key="secret-value",
        company_id="company-1",
        timeout=1,
    )
    assert report["status"] == "pass_with_findings"
    assert report["finding_count"] == 1
    assert report["coverage"] == {
        "project_count": 2,
        "project_usage_count": 2,
        "worker_run_count": 3,
        "budget_count": 1,
        "budget_reservation_count": 3,
    }
    assert "secret-value" not in json.dumps(report)

    clean_required = runner.inspect_live(
        url="http://orchestrator.invalid",
        api_key="secret-value",
        company_id=None,
        timeout=1,
        require_clean=True,
    )
    assert clean_required["status"] == "fail"
