from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_default_worker_bindings.py"
WORKERS = SCRIPT.parents[1] / "workers"


def test_default_worker_binding_contract_reconciles_documented_slots():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.default-worker-bindings.v1"
    assert report["status"] == "pass"
    assert report["checked_worker_count"] == 15
    assert report["expected_worker_count"] == 15
    assert report["licence_metadata_is_gate"] is False
    assert {row["worker_id"] for row in report["workers"]} == {
        "financial_analyst",
        "tech_analyst",
        "hr_analyst",
        "security_analyst",
        "sprint_planner",
        "kpi_analyst",
        "requirements_writer",
        "planner",
        "cost_estimator",
        "system_architect",
        "solution_designer",
        "tech_writer",
        "tester",
        "devops_eng",
        "sre_agent",
    }
    tester = next(row for row in report["workers"] if row["worker_id"] == "tester")
    assert tester["transport"] == "opencode"
    assert tester["runtime_catalogue_pair"] is True
    assert tester["adapter_entrypoint"] == "OpenCodeAdapter"
    assert tester["security_scan_status"] == "findings_review_required"


def test_default_worker_binding_contract_detects_stack_drift(tmp_path):
    source = WORKERS / "planner.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["runtime"]["adapter_config"]["default_planning_adapter"] = "plane"
    (tmp_path / "planner.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("check_default_worker_bindings", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.reconcile(workers_dir=tmp_path)

    assert report["status"] == "fail"
    assert any("planner" in error and "default_planning_adapter" in error for error in report["errors"])


def test_default_worker_binding_live_mode_is_explicitly_blocked():
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
    assert report["live"]["status"] == "blocked"
