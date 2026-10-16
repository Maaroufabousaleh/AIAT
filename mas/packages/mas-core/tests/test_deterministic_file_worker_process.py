"""Process-level tests for the stdlib deterministic file worker candidate."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from mas_core.worker_contract import (
    AdapterContext,
    ConformanceRunner,
    EventType,
    WorkerCapabilities,
    WorkerRunController,
    WorkerRunRequest,
    WorkerToolResponse,
)
from mas_core.worker_registry import runtime_adapters
from mas_core.worker_registry.runtime_adapters import OCIAdapter, ProcessAdapter

REPO_ROOT = Path(__file__).resolve().parents[4]
PROCESS_ENTRYPOINT = REPO_ROOT / "mas" / "workers" / "aiat_deterministic_file_worker_process.py"
WORKER_REGISTRY_ID = "d4c4a6d8-52fc-45ec-9136-89ca8aaec0e9"
TASK_TYPE = "deterministic_file_generation"
_UNSET = object()
_EXPENSES_SOURCE = '''"""Calculate expense totals grouped by category."""

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


def category_totals(rows):
    totals = defaultdict(Decimal)
    for row in rows:
        totals[row["category"]] += Decimal(row["amount"])
    return dict(totals)


def read_expenses(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        return category_totals(csv.DictReader(source))
'''


def _process_adapter(
    *,
    context: AdapterContext | None = None,
    worker_id: str = WORKER_REGISTRY_ID,
) -> ProcessAdapter:
    return ProcessAdapter(
        [sys.executable, "-I", str(PROCESS_ENTRYPOINT)],
        worker_id=worker_id,
        context=context,
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=3.0,
    )


def _request(
    *,
    project_id=_UNSET,
    task_type: str = TASK_TYPE,
    task_input=None,
    worker_id: str = WORKER_REGISTRY_ID,
) -> WorkerRunRequest:
    if project_id is _UNSET:
        project_id = uuid4()
    return WorkerRunRequest(
        worker_id=worker_id,
        task_type=task_type,
        idempotency_key="deterministic-process-file-write",
        project_id=project_id,
        task_input=task_input
        if task_input is not None
        else {"path": "src/expenses.py", "content": _EXPENSES_SOURCE},
        tool_grants=["file_write"],
    )


@pytest.mark.asyncio
async def test_real_process_requests_governed_file_write_and_waits_for_result(tmp_path: Path) -> None:
    workspace = tmp_path / "project-workspace"
    dispatched = []

    async def tool_service(tool_request):
        dispatched.append(tool_request)
        assert tool_request.tool_name == "file_write"
        assert tool_request.permission_scope == ["file_write"]
        assert tool_request.approval_required is False
        assert "project_id" not in tool_request.arguments
        target = workspace / tool_request.arguments["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        content = tool_request.arguments["content"].encode("utf-8")
        target.write_bytes(content)
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={
                "success": True,
                "project_id": str(request.project_id),
                "path": tool_request.arguments["path"],
                "bytes_written": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        )

    request = _request()
    controller = WorkerRunController()
    adapter = _process_adapter(context=AdapterContext(tool_dispatcher=tool_service))
    assert adapter.worker_id == request.worker_id == WORKER_REGISTRY_ID
    try:
        outcome = await controller.execute(request, adapter)
    finally:
        await adapter.close()

    artifact = workspace / "src/expenses.py"
    assert outcome.state == "SUCCEEDED"
    assert len(dispatched) == 1
    assert artifact.read_text(encoding="utf-8") == _EXPENSES_SOURCE
    ast.parse(artifact.read_text(encoding="utf-8"))
    module_spec = importlib.util.spec_from_file_location("generated_expenses", artifact)
    assert module_spec is not None and module_spec.loader is not None
    generated = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(generated)
    assert generated.category_totals(
        [
            {"category": "travel", "amount": "12.50"},
            {"category": "travel", "amount": "7.25"},
            {"category": "food", "amount": "3.00"},
        ]
    ) == {"travel": 19.75, "food": 3.0}
    tool_events = [event for event in outcome.events if event.event_type == EventType.TOOL_REQUEST]
    assert len(tool_events) == 1
    assert tool_events[0].worker_id == request.worker_id == adapter.worker_id
    assert [event.event_type for event in outcome.events].count(EventType.TOOL_RESPONSE) == 1
    assert outcome.result is not None and outcome.result.success is True
    effect = controller._memory_tool_effects[(request.run_id, dispatched[0].idempotency_key)]
    assert effect["state"] == "COMPLETED"
    assert not adapter._processes


@pytest.mark.asyncio
async def test_real_process_does_not_report_success_when_tool_service_denies_write() -> None:
    async def denied(tool_request):
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=False,
            error={"code": "FILE_WRITE_DENIED", "message": "denied", "terminal": True},
        )

    adapter = _process_adapter(context=AdapterContext(tool_dispatcher=denied))
    try:
        outcome = await WorkerRunController().execute(_request(), adapter)
    finally:
        await adapter.close()

    assert outcome.state == "FAILED"
    assert outcome.result is not None and outcome.result.success is False
    assert outcome.result.error is not None
    assert outcome.result.error.code == "FILE_WRITE_DENIED"
    assert not adapter._processes


@pytest.mark.asyncio
async def test_real_process_rejects_unscoped_project_before_tool_request() -> None:
    dispatched = []

    async def tool_service(tool_request):
        dispatched.append(tool_request)
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={"success": True, "path": "src/expenses.py"},
        )

    adapter = _process_adapter(context=AdapterContext(tool_dispatcher=tool_service))
    try:
        outcome = await WorkerRunController().execute(_request(project_id=None), adapter)
    finally:
        await adapter.close()

    assert outcome.state == "FAILED"
    assert outcome.result is not None and outcome.result.success is False
    assert outcome.result.error is not None
    assert outcome.result.error.code == "WORKER_INPUT_INVALID"
    assert not dispatched
    assert not any(event.event_type == EventType.TOOL_REQUEST for event in outcome.events)


@pytest.mark.asyncio
async def test_real_process_rejects_logical_worker_alias_before_tool_request() -> None:
    logical_alias = "aiat_deterministic_file_worker"
    dispatched = []

    async def tool_service(tool_request):
        dispatched.append(tool_request)
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={"success": True, "path": "src/expenses.py", "bytes_written": 1},
        )

    adapter = _process_adapter(
        context=AdapterContext(tool_dispatcher=tool_service),
        worker_id=logical_alias,
    )
    try:
        outcome = await WorkerRunController().execute(
            _request(worker_id=logical_alias),
            adapter,
        )
    finally:
        await adapter.close()

    assert outcome.state == "FAILED"
    assert outcome.result is not None and outcome.result.success is False
    assert outcome.result.error is not None
    assert outcome.result.error.code == "WORKER_INPUT_INVALID"
    assert not dispatched
    assert not any(event.event_type == EventType.TOOL_REQUEST for event in outcome.events)


@pytest.mark.asyncio
async def test_conformance_runner_executes_real_deterministic_process_case() -> None:
    adapter = ProcessAdapter(
        [sys.executable, "-I", str(PROCESS_ENTRYPOINT)],
        worker_id=WORKER_REGISTRY_ID,
        capabilities=WorkerCapabilities(
            capability_names=["deterministic.file_generate"],
            extensions={
                "aiat.conformance": {
                    "schema_version": "aiat.conformance.v1",
                    "case_id": "aiat.deterministic_file_generation.v1",
                }
            },
        ),
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=3.0,
    )
    try:
        report = await ConformanceRunner(timeout_seconds=5.0).run(
            adapter,
            worker_id=WORKER_REGISTRY_ID,
        )
    finally:
        await adapter.close()

    assert report.passed is True
    case_result = next(test for test in report.tests if test.name == "conformance_case_result")
    assert case_result.passed is True
    tool_protocol = next(test for test in report.tests if test.name == "conformance_tool_protocol")
    assert tool_protocol.passed is True
    assert tool_protocol.evidence["response_source"] == "local_conformance_fixture_not_tool_service"


@pytest.mark.parametrize(
    "task_input",
    [
        {"path": "../escape.py", "content": "VALUE = 1\n"},
        {"path": "src/expenses.py", "content": "x" * (64 * 1024 + 1)},
    ],
)
@pytest.mark.asyncio
async def test_real_process_rejects_unsafe_or_oversized_write_before_mediation(task_input) -> None:
    async def forbidden_dispatch(tool_request):
        raise AssertionError("invalid process input must not reach the tool dispatcher")

    adapter = _process_adapter(context=AdapterContext(tool_dispatcher=forbidden_dispatch))
    try:
        outcome = await WorkerRunController().execute(_request(task_input=task_input), adapter)
    finally:
        await adapter.close()

    assert outcome.state == "FAILED"
    assert outcome.result is not None and outcome.result.success is False
    assert outcome.result.error is not None
    assert outcome.result.error.code == "WORKER_INPUT_INVALID"
    assert not any(event.event_type == EventType.TOOL_REQUEST for event in outcome.events)


def test_oci_process_command_is_networkless_mountless_and_receives_no_secrets() -> None:
    image = "local.registry/aiat/deterministic-worker@sha256:" + "a" * 64
    adapter = OCIAdapter(
        image,
        worker_id=WORKER_REGISTRY_ID,
        sandbox_profile="trusted",
        trusted_worker=True,
        context=AdapterContext(secrets={"provider_key": "must-not-enter-command"}),
        bidirectional_tool_protocol=True,
    )

    assert adapter.command[adapter.command.index("--network") + 1] == "none"
    assert all(flag not in adapter.command for flag in ("-v", "--volume", "--mount", "-e", "--env"))
    assert "/var/run/docker.sock" not in adapter.command
    assert "must-not-enter-command" not in " ".join(adapter.command)
    assert adapter.environment == {}
    assert adapter.context.secrets == {"provider_key": "must-not-enter-command"}
    assert adapter.command[-1] == image
    assert adapter.command[0] == "docker"


@pytest.mark.asyncio
async def test_oci_launcher_uses_resolved_cli_with_shell_free_argv_and_no_environment_leakage(
    monkeypatch, tmp_path
) -> None:
    docker_cli = tmp_path / "trusted-docker"
    docker_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker_cli.chmod(0o755)
    monkeypatch.setattr(
        runtime_adapters.shutil,
        "which",
        lambda executable: str(docker_cli) if executable == "docker" else None,
    )
    monkeypatch.setenv("AIAT_PARENT_ENV_CANARY", "parent-value-must-not-leak")
    context = AdapterContext(secrets={"tool_secret": "context-value-must-not-leak"})
    adapter = OCIAdapter(
        "local.registry/aiat/worker@sha256:" + "b" * 64,
        worker_id=WORKER_REGISTRY_ID,
        sandbox_profile="sandboxed",
        sandbox_runtime="runsc",
        context=context,
    )

    class _Stdin:
        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

    class _Stdout:
        def __init__(self) -> None:
            self._lines = iter([b'{"success":true,"output":{"ok":true}}\n'])

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            try:
                return next(self._lines)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class _Stderr:
        async def read(self) -> bytes:
            return b""

    class _Process:
        def __init__(self) -> None:
            self.stdin = _Stdin()
            self.stdout = _Stdout()
            self.stderr = _Stderr()
            self.returncode = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    process = _Process()
    launch_calls = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        launch_calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(runtime_adapters.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    outcome = await adapter._execute(
        WorkerRunRequest(
            worker_id=WORKER_REGISTRY_ID,
            task_type="launcher-test",
            idempotency_key="launcher-test",
        )
    )

    assert getattr(outcome, "success", False) is True
    assert len(launch_calls) == 1
    argv, kwargs = launch_calls[0]
    assert argv == tuple(adapter.command)
    assert argv[0] == str(docker_cli.resolve())
    assert argv[1:3] == ("run", "--rm")
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "/bin/sh" not in argv and "-c" not in argv
    assert "shell" not in kwargs
    assert kwargs["env"] == {}
    assert "AIAT_PARENT_ENV_CANARY" not in kwargs["env"]
    assert "parent-value-must-not-leak" not in " ".join(argv)
    assert "context-value-must-not-leak" not in " ".join(argv)
    assert adapter.environment == {}


@pytest.mark.asyncio
async def test_oci_launcher_fails_closed_when_host_docker_cli_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(runtime_adapters.shutil, "which", lambda _executable: None)
    monkeypatch.setattr(
        runtime_adapters.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: pytest.fail("missing Docker CLI must fail before process creation"),
    )
    adapter = OCIAdapter(
        "local.registry/aiat/worker@sha256:" + "c" * 64,
        worker_id=WORKER_REGISTRY_ID,
        sandbox_profile="sandboxed",
        sandbox_runtime="runsc",
    )

    with pytest.raises(RuntimeError, match="certified Docker launcher is unavailable"):
        await adapter._execute(
            WorkerRunRequest(
                worker_id=WORKER_REGISTRY_ID,
                task_type="launcher-test",
                idempotency_key="launcher-missing",
            )
        )
