"""Read-only selected Worker Run readiness contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from mas_core.worker_registry.worker_run_readiness import evaluate_worker_run_readiness

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER_ID = "00000000-0000-4000-8000-000000000101"
PROJECT_ID = "00000000-0000-4000-8000-000000000102"
COMPANY_ID = "00000000-0000-4000-8000-000000000103"
PROFILE_ID = "fixture-model-profile"


def _inputs() -> dict[str, object]:
    return {
        "worker": {
            "id": WORKER_ID,
            "status": "ACTIVE",
            "model_mode": "aiat_gateway",
            "model_profile_id": PROFILE_ID,
            "evaluation_status": "approved",
            "version_pin": "fixture-1.0.0",
            "active_shell_version_id": "shell-1",
            "active_adapter_id": "adapter-1",
            "active_skill_bundle_id": "bundle-1",
            "sandbox_profile": "gvisor",
        },
        "project": {"id": PROJECT_ID, "company_id": COMPANY_ID, "state": "IN_PROGRESS"},
        "company": {"id": COMPANY_ID, "status": "ACTIVE"},
        "assignments": [
            {
                "worker_id": WORKER_ID,
                "status": "ACTIVE",
                "approval_required": False,
                "model_profile_id": PROFILE_ID,
            }
        ],
        "budgets": [
            {"budget_key": "max_concurrent_runs", "configured": True, "available": "1"},
            {"budget_key": "max_cost_usd", "configured": True, "available": "0.10"},
        ],
        "model_profiles": [
            {
                "profile_id": PROFILE_ID,
                "status": "approved",
                "versions": [{"status": "approved", "version": "1"}],
            }
        ],
        "worker_id": WORKER_ID,
        "project_id": PROJECT_ID,
        "required_budget_usd": Decimal("0.10"),
        "require_sandbox": True,
        "health": {"health_status": "healthy"},
    }


def test_complete_selected_snapshot_is_ready_without_license_gate() -> None:
    report = evaluate_worker_run_readiness(**_inputs())
    assert report["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False
    assert report["checks"]["identity"]["status"] == "not_checked"
    assert report["checks"]["sandbox"]["runtime_status"] == "not_checked"
    assert report["blockers"] == []


def test_inactive_worker_terminal_project_and_missing_immutable_records_block() -> None:
    inputs = _inputs()
    worker = dict(inputs["worker"])
    worker.update(
        {
            "status": "INACTIVE",
            "active_shell_version_id": None,
            "active_adapter_id": None,
            "active_skill_bundle_id": None,
        }
    )
    inputs["worker"] = worker
    inputs["project"] = {"id": PROJECT_ID, "company_id": COMPANY_ID, "state": "FAILED"}
    report = evaluate_worker_run_readiness(**inputs)
    codes = {item["code"] for item in report["blockers"]}
    assert report["status"] == "blocked"
    assert "worker_not_active" in codes
    assert "active_shell_version_id_missing" in codes
    assert "active_adapter_id_missing" in codes
    assert "active_skill_bundle_id_missing" in codes
    assert "project_terminal" in codes


def test_missing_budget_and_profile_are_fail_closed() -> None:
    inputs = _inputs()
    inputs["budgets"] = []
    inputs["model_profiles"] = []
    report = evaluate_worker_run_readiness(**inputs)
    codes = {item["code"] for item in report["blockers"]}
    assert report["status"] == "blocked"
    assert "budget_max_concurrent_runs_missing" in codes
    assert "budget_max_cost_usd_missing" in codes
    assert "model_profile_not_approved" in codes


def test_live_mode_requires_explicit_selection_and_never_mutates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_worker_run_readiness.py",
            "--live",
            "--json",
            "--url",
            "http://localhost:8000",
            "--api-key",
            "test-only-not-secret",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert report["no_mutation"] is True
    assert "worker-id" in report["reason"]


def test_fixture_cli_passes_and_remains_secret_safe() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_run_readiness.py", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.worker-run-readiness-check.v1"
    assert report["readiness_schema"] == "aiat.worker-run-readiness.v1"
    assert report["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False
    assert report["readiness"]["checks"]["identity"]["status"] == "not_checked"
    assert "task_input" not in result.stdout
