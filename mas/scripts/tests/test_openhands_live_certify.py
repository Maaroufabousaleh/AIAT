"""Fail-closed status semantics for the OpenHands live wrapper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from mas_core.worker_contract import ModelProfileReference, WorkerRunRequest


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


@pytest.mark.parametrize("gate", ["pause", "interrupt", "resume", "timeout"])
def test_failed_lifecycle_gate_is_not_reported_as_generic_incomplete(gate: str) -> None:
    module = _module()
    statuses = module._status_map("NOT_RUN")
    statuses[gate] = f"FAILED_{gate.upper()}"
    assert module._final_status(statuses, []) == "BLOCKED_LIFECYCLE"


def test_failed_non_lifecycle_gate_is_classified_as_implementation_failure() -> None:
    module = _module()
    statuses = module._status_map("NOT_RUN")
    statuses["artifact_capture"] = "FAILED_ARTIFACT_CAPTURE"
    assert module._final_status(statuses, []) == "FAILED_CERTIFICATION_IMPLEMENTATION"


def test_execution_contract_failure_precedes_downstream_gate_failures() -> None:
    module = _module()
    statuses = module._status_map("NOT_RUN")
    statuses["coding_task"] = "FAILED_MODEL_EXECUTION"
    statuses["file_modifications"] = "FAILED_FILE_MODIFICATIONS"
    statuses["test_execution"] = "FAILED_TEST_EXECUTION"
    result = module._final_status(
        statuses,
        ["live_coding_task_failed", "test_execution_failed", "file_modifications_contract_failed"],
        {"conversation_create_http_status": 422, "run_start_http_status": None},
    )
    assert result == "BLOCKED_OPENHANDS_LIVE_EXECUTION_CONTRACT"


def test_lifecycle_polling_is_skipped_when_conversation_creation_failed() -> None:
    module = _module()
    status = module._lifecycle_upstream_block(
        {
            "conversation_create_status": "FAILED",
            "conversation_create_http_status": 500,
            "conversation_id_present": False,
        }
    )
    assert status == "NOT_RUN_UPSTREAM_CONVERSATION_CREATE_FAILURE"
    assert module._lifecycle_upstream_block(
        {"conversation_create_status": "PASS", "conversation_id_present": True}
    ) is None


def test_conversation_create_evidence_is_scalar_and_fail_closed() -> None:
    module = _module()
    evidence = module._conversation_create_evidence(
        {
            "execution_diagnostics": {
                "conversation_create_status": "FAILED",
                "conversation_create_http_status": 500,
                "conversation_id_present": False,
                "conversation_create_exception_class": "KeyError",
                "conversation_create_exception_message_sanitized": "ToolDefinition 'TerminalTool' is not registered",
                "conversation_create_request_shape_sha256": "a" * 64,
                "model_resolution_status": "FAILED",
                "model_resolution_logical_model_id": "omniroute-coding",
                "model_resolution_wire_model_id": "openai/omniroute-coding",
                "model_resolution_gateway_base_url_class": "internal_litellm",
            }
        }
    )
    assert evidence["status"] == "FAIL"
    assert evidence["conversation_create_http_status"] == 500
    assert evidence["model_resolution_status"] == "FAILED"
    assert evidence["model_resolution_wire_model_id"] == "openai/omniroute-coding"
    assert evidence["model_resolution_gateway_base_url_class"] == "internal_litellm"
    assert evidence["raw_request_retained"] is False
    assert evidence["secret_values_retained"] is False


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


def test_host_task_verification_ignores_disposable_git_metadata(tmp_path: Path) -> None:
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
    (workspace / ".git" / "objects").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    details, blockers = module._verify_host_task(
        task_definition={
            "test_command": "python -m pytest -q",
            "expected_changed_paths": ["slugger/core.py"],
            "forbidden_changed_paths": [],
        },
        host_workspace=workspace,
        fixture_root=fixture,
    )
    assert details["changed_paths"] == ["slugger/core.py"]
    assert details["file_modifications"] == "PASS"
    assert blockers == []


def test_host_task_tests_do_not_inherit_certification_secrets(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setenv("AIAT_TOOL_SECRET", "tool-secret")
    monkeypatch.setenv("OPENHANDS_SESSION_API_KEY", "session-secret")
    monkeypatch.setenv("OPENHANDS_MODEL_GATEWAY_API_KEY", "gateway-secret")
    monkeypatch.setenv("GROQ_API_KEY", "provider-secret")
    observed: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        observed["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    details, blockers = module._verify_host_task(
        task_definition={
            "test_command": "python -m pytest -q",
            "expected_changed_paths": ["slugger/core.py"],
            "forbidden_changed_paths": [],
        },
        host_workspace=workspace,
        fixture_root=fixture,
    )
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert all(
        name not in environment
        for name in (
            "AIAT_TOOL_SECRET",
            "OPENHANDS_SESSION_API_KEY",
            "OPENHANDS_MODEL_GATEWAY_API_KEY",
            "GROQ_API_KEY",
        )
    )
    assert details["test_environment_secret_scrubbed"] is True
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


def test_host_task_verification_scans_workspace_files_without_retaining_secret(tmp_path: Path) -> None:
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
    (workspace / "unexpected.txt").write_text("sentinel-value", encoding="utf-8")
    details, blockers = module._verify_host_task(
        task_definition={
            "test_command": "python -m pytest -q",
            "expected_changed_paths": ["slugger/core.py", "unexpected.txt"],
            "forbidden_changed_paths": [],
        },
        host_workspace=workspace,
        fixture_root=fixture,
        secret_values=["sentinel-value"],
    )
    assert details["workspace_secret_scan"]["status"] == "BLOCKED_SECRET_NON_DISCLOSURE"
    assert details["workspace_secret_scan"]["matches"] == 1
    assert "sentinel-value" not in json.dumps(details)
    assert "secret_disclosure_detected" in blockers


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


@pytest.mark.asyncio
async def test_preconfigured_mcp_cleanup_accepts_empty_204_and_verifies_absence() -> None:
    module = _module()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/api/settings":
            return httpx.Response(200, json={"mcp_config": {}})
        raise AssertionError(request)

    client = httpx.AsyncClient(
        base_url="http://openhands.test",
        transport=httpx.MockTransport(handler),
        headers={"X-Session-API-Key": "session-secret"},
    )
    report = await module._cleanup_preconfigured_mcp(
        base_url="http://openhands.test",
        settings_key="aiat-openhands-test-run",
        session_key="session-secret",
        client=client,
    )
    await client.aclose()

    assert report == {"status": "PASS", "delete": "deleted", "verified_absent": True}
    assert calls == [
        "DELETE /api/settings/mcp/aiat-openhands-test-run",
        "GET /api/settings",
    ]


@pytest.mark.asyncio
async def test_preconfigured_mcp_cleanup_reads_v143_nested_settings_envelope() -> None:
    module = _module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/api/settings":
            return httpx.Response(200, json={"agent_settings": {"mcp_config": {}}})
        raise AssertionError(request)

    client = httpx.AsyncClient(
        base_url="http://openhands.test",
        transport=httpx.MockTransport(handler),
        headers={"X-Session-API-Key": "session-secret"},
    )
    report = await module._cleanup_preconfigured_mcp(
        base_url="http://openhands.test",
        settings_key="aiat-openhands-test-run",
        session_key="session-secret",
        client=client,
    )
    await client.aclose()

    assert report == {"status": "PASS", "delete": "deleted", "verified_absent": True}


@pytest.mark.asyncio
async def test_preconfigured_mcp_cleanup_blocks_nested_residual_entry() -> None:
    module = _module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/api/settings":
            return httpx.Response(
                200,
                json={"agent_settings": {"mcp_config": {"aiat-openhands-test-run": {}}}},
            )
        raise AssertionError(request)

    client = httpx.AsyncClient(
        base_url="http://openhands.test",
        transport=httpx.MockTransport(handler),
        headers={"X-Session-API-Key": "session-secret"},
    )
    report = await module._cleanup_preconfigured_mcp(
        base_url="http://openhands.test",
        settings_key="aiat-openhands-test-run",
        session_key="session-secret",
        client=client,
    )
    await client.aclose()

    assert report == {"status": "BLOCKED_CLEANUP", "delete": "deleted", "verified_absent": False}


def test_mcp_cleanup_readback_does_not_let_empty_compatibility_field_hide_nested_entry() -> None:
    module = _module()
    config = module._extract_mcp_config(
        {
            "mcp_config": {},
            "agent_settings": {"mcp_config": {"aiat-openhands-test-run": {}}},
        }
    )
    assert config == {"aiat-openhands-test-run": {}}


@pytest.mark.asyncio
async def test_live_lifecycle_wave_requires_remote_control_states() -> None:
    module = _module()

    class FakeAdapter:
        def __init__(self) -> None:
            self._conversation_by_run = {}
            self._statuses = {}
            self._events = {}

        async def start(self, request: WorkerRunRequest) -> None:
            prompt = str(request.task_input["prompt"])
            if "pause/resume" in prompt:
                kind = "pause"
                self._statuses[kind] = iter(("running", "paused"))
                event = SimpleNamespace(result=SimpleNamespace(success=True), error=None)
            elif "interrupt" in prompt:
                kind = "interrupt"
                self._statuses[kind] = iter(("running",))
                event = SimpleNamespace(result=None, error=SimpleNamespace(code="CANCELLED"))
            else:
                kind = "timeout"
                self._statuses[kind] = iter(())
                event = SimpleNamespace(result=None, error=SimpleNamespace(code="TIMEOUT"))
            self._conversation_by_run[request.run_id] = kind
            self._events[request.run_id] = [event]

        async def _conversation(self, conversation_id: str) -> dict[str, str]:
            sequence = self._statuses[conversation_id]
            try:
                status = next(sequence)
            except StopIteration:
                status = "paused"
            return {"execution_status": status}

        async def pause(self, request: object) -> None:
            return None

        async def resume(self, request: object) -> None:
            return None

        async def cancel(self, request: object) -> None:
            return None

        async def events(self, run_id):
            for event in self._events.pop(run_id, []):
                yield event

    base_request = WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key="lifecycle-test",
        worker_id="coding-worker-openhands-candidate",
        task_type="coding",
        task_input={"prompt": "bounded lifecycle test"},
        resolved_model_profile=ModelProfileReference(profile_id="test", exact_model_id="omniroute-coding"),
    )
    statuses, details, blockers = await module._exercise_live_lifecycle(FakeAdapter(), base_request, ["sentinel"])

    assert statuses == {"pause": "PASS", "interrupt": "PASS", "resume": "PASS", "timeout": "PASS"}
    assert details["probes"]["pause_resume"]["status_before_pause"] == "running"
    assert details["probes"]["pause_resume"]["status_after_pause"] == "paused"
    assert details["probes"]["interrupt"]["status_before_interrupt"] == "running"
    assert details["secret_scan"]["status"] == "PASS"
    assert blockers == []
