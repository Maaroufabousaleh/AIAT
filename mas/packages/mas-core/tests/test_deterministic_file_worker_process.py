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
    EventType,
    WorkerRunController,
    WorkerRunRequest,
    WorkerToolResponse,
)
from mas_core.worker_registry.runtime_adapters import OCIAdapter, ProcessAdapter

REPO_ROOT = Path(__file__).resolve().parents[4]
PROCESS_ENTRYPOINT = REPO_ROOT / "mas" / "workers" / "aiat_deterministic_file_worker_process.py"
WORKER_ID = "aiat_deterministic_file_worker"
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


def _process_adapter(*, context: AdapterContext | None = None) -> ProcessAdapter:
    return ProcessAdapter(
        [sys.executable, "-I", str(PROCESS_ENTRYPOINT)],
        worker_id=WORKER_ID,
        context=context,
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=3.0,
    )


def _request(*, project_id=_UNSET, task_type: str = TASK_TYPE, task_input=None) -> WorkerRunRequest:
    if project_id is _UNSET:
        project_id = uuid4()
    return WorkerRunRequest(
        worker_id=WORKER_ID,
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
                "path": tool_request.arguments["path"],
                "bytes_written": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        )

    request = _request()
    controller = WorkerRunController()
    adapter = _process_adapter(context=AdapterContext(tool_dispatcher=tool_service))
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
    assert [event.event_type for event in outcome.events].count(EventType.TOOL_REQUEST) == 1
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
        worker_id=WORKER_ID,
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
