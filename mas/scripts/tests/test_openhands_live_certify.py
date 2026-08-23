"""Fail-closed status semantics for the OpenHands live wrapper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "openhands_live_certify.py"
    spec = importlib.util.spec_from_file_location("openhands_live_certify", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_live_wave_cannot_report_pass() -> None:
    module = _module()
    statuses = module._status_map("NOT_RUN")
    statuses["coding_task"] = "PASS"
    statuses["zero_residue"] = "PASS"
    assert module._final_status(statuses, []) == "BLOCKED_INCOMPLETE_MANDATORY_GATES"


def test_live_wrapper_uses_narrow_blocker_classes() -> None:
    module = _module()
    statuses = module._status_map("NOT_RUN")
    assert module._final_status(statuses, ["operator_configuration_missing:GROQ_API_KEY"]) == "BLOCKED_OPERATOR_CONFIGURATION"
    assert module._final_status(statuses, ["readiness:health"]) == "BLOCKED_RUNTIME_STARTUP"
    statuses["coding_task"] = "FAILED_MODEL_EXECUTION"
    assert module._final_status(statuses, ["live_coding_task_failed"]) == "FAILED_MODEL_EXECUTION"
    statuses["coding_task"] = "PASS"
    statuses["zero_residue"] = "FAILED_CLEANUP"
    assert module._final_status(statuses, ["run_scoped_mcp_grant_residue"]) == "BLOCKED_CLEANUP"


def test_task_spec_prompt_is_used_but_not_retained_in_public_definition(tmp_path: Path) -> None:
    module = _module()
    task = tmp_path / "task.json"
    prompt = "Implement the governed disposable task without exposing credentials."
    task.write_text(
        json.dumps(
            {
                "task_id": "fixture-task",
                "prompt": prompt,
                "test_command": "python -m pytest -q",
                "expected_changed_paths": ["slugger/core.py"],
                "forbidden_changed_paths": ["tests/test_slugger.py"],
            }
        ),
        encoding="utf-8",
    )
    loaded_prompt, definition, blockers = module._load_task_definition(task)
    assert loaded_prompt == prompt
    assert blockers == []
    assert definition["task_id"] == "fixture-task"
    assert "prompt" not in definition
    assert prompt not in json.dumps(definition)


def test_host_task_verification_requires_real_test_and_exact_workspace_change(tmp_path: Path) -> None:
    module = _module()
    fixture = tmp_path / "fixture"
    workspace = tmp_path / "workspace"
    (fixture / "slugger").mkdir(parents=True)
    (fixture / "tests").mkdir()
    (fixture / "slugger" / "core.py").write_text("before\n", encoding="utf-8")
    (fixture / "tests" / "test_slugger.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    import shutil

    shutil.copytree(fixture, workspace)
    (workspace / "slugger" / "core.py").write_text("after\n", encoding="utf-8")
    definition = {
        "test_command": "python -m pytest -q",
        "expected_changed_paths": ["slugger/core.py"],
        "forbidden_changed_paths": ["tests/test_slugger.py"],
    }
    details, blockers = module._verify_host_task(
        task_definition=definition,
        host_workspace=workspace,
        fixture_root=fixture,
    )
    assert details["file_modifications"] == "PASS"
    assert details["test_execution"] == "PASS"
    assert details["changed_paths"] == ["slugger/core.py"]
    assert blockers == []


def test_host_task_verification_does_not_infer_pass_without_workspace(tmp_path: Path) -> None:
    module = _module()
    details, blockers = module._verify_host_task(
        task_definition={
            "test_command": "python -m pytest -q",
            "expected_changed_paths": ["slugger/core.py"],
            "forbidden_changed_paths": [],
        },
        host_workspace=None,
        fixture_root=None,
    )
    assert details["test_execution"] == "NOT_RUN"
    assert details["file_modifications"] == "NOT_RUN"
    assert blockers == ["test_execution_evidence_unavailable"]


def test_event_secret_scan_retains_only_fingerprints() -> None:
    module = _module()

    class Event:
        def model_dump_json(self) -> str:
            return '{"status":"safe","value":"sentinel"}'

    clean = module._scan_event_for_secrets(Event(), ["secret-value"])
    assert clean["matches"] == 0
    leaked = module._scan_event_for_secrets(Event(), ["sentinel"])
    assert leaked["matches"] == 1
    assert "sentinel" not in json.dumps(leaked)
