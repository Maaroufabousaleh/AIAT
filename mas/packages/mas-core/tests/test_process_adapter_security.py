"""PR B coverage for process-worker environment and secret boundaries."""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

import pytest

from mas_core.worker_contract import (
    AdapterContext,
    EventType,
    WorkerCancellation,
    WorkerResult,
    WorkerRunRequest,
    WorkerToolResponse,
)
from mas_core.worker_contract.controller import WorkerRunController
from mas_core.worker_registry.runtime_adapters import ProcessAdapter


def _environment_probe() -> str:
    return (
        "import json, os; "
        "print(json.dumps({'success': True, 'output': {"
        "'parent': os.environ.get('AIAT_PARENT_ENV_CANARY'), "
        "'context': os.environ.get('AIAT_CONTEXT_ENV_CANARY'), "
        "'explicit': os.environ.get('AIAT_EXPLICIT_ENV')"
        "}}))"
    )


def _bidirectional_tool_worker() -> str:
    return "\n".join(
        [
            "import json, sys",
            "request = json.loads(sys.stdin.readline())",
            "run_id = request['run_id']",
            "tool_request = {",
            "  'request_id': '9bb99a44-805f-4454-93ad-a0442dc784a1',",
            "  'run_id': run_id,",
            "  'tool_name': 'file_write',",
            "  'arguments': {'path': 'src/example.py', 'content': 'VALUE = 3\\n'},",
            "  'permission_scope': ['file_write'],",
            "  'approval_required': False,",
            "  'idempotency_key': 'process-file-write-1',",
            "}",
            "print(json.dumps({'run_id': run_id, 'worker_id': request['worker_id'],",
            "  'event_type': 'tool_request', 'tool_request': tool_request}), flush=True)",
            "message = json.loads(sys.stdin.readline())",
            "assert message['message_type'] == 'WorkerToolResponse'",
            "response = message['payload']",
            "if response['success']:",
            "  print(json.dumps({'success': True, 'output': {'tool_result': response['result']}}), flush=True)",
            "else:",
            "  print(json.dumps({'success': False, 'error': response['error']}), flush=True)",
        ]
    )


@pytest.mark.asyncio
async def test_process_adapter_does_not_inherit_parent_or_context_secrets(monkeypatch) -> None:
    monkeypatch.setenv("AIAT_PARENT_ENV_CANARY", "parent-secret")
    monkeypatch.setenv("AIAT_CONTEXT_ENV_CANARY", "parent-context-secret")
    context = AdapterContext(secrets={"tool_secret": "context-secret"})
    adapter = ProcessAdapter(
        [sys.executable, "-c", _environment_probe()],
        worker_id="process-environment-worker",
        context=context,
    )

    try:
        result = await adapter._execute(
            WorkerRunRequest(
                worker_id="process-environment-worker",
                task_type="environment-probe",
                idempotency_key="environment-probe",
            )
        )
    finally:
        await adapter.close()

    assert isinstance(result, WorkerResult)
    assert result.output == {"parent": None, "context": None, "explicit": None}
    assert context.secrets == {"tool_secret": "context-secret"}
    assert "context-secret" not in repr(context)


@pytest.mark.asyncio
async def test_process_adapter_passes_only_explicit_environment(monkeypatch) -> None:
    monkeypatch.setenv("AIAT_PARENT_ENV_CANARY", "must-not-inherit")
    adapter = ProcessAdapter(
        [sys.executable, "-c", _environment_probe()],
        worker_id="process-explicit-environment-worker",
        environment={"AIAT_EXPLICIT_ENV": "allowlisted-value"},
    )

    try:
        result = await adapter._execute(
            WorkerRunRequest(
                worker_id="process-explicit-environment-worker",
                task_type="environment-probe",
                idempotency_key="explicit-environment-probe",
            )
        )
    finally:
        await adapter.close()

    assert isinstance(result, WorkerResult)
    assert result.output == {
        "parent": None,
        "context": None,
        "explicit": "allowlisted-value",
    }
    assert adapter.environment == {"AIAT_EXPLICIT_ENV": "allowlisted-value"}


@pytest.mark.asyncio
async def test_process_adapter_redacts_known_secret_values_from_results() -> None:
    adapter = ProcessAdapter(
        [
            sys.executable,
            "-c",
            (
                "import json, os; print(json.dumps({'success': True, 'output': "
                "{'token': os.environ['AIAT_PROCESS_TOKEN'], 'context': 'context-secret'}}))"
            ),
        ],
        worker_id="process-redaction-worker",
        environment={"AIAT_PROCESS_TOKEN": "process-secret"},
        context=AdapterContext(secrets={"tool_secret": "context-secret"}),
    )

    try:
        result = await adapter._execute(
            WorkerRunRequest(
                worker_id="process-redaction-worker",
                task_type="secret-output",
                idempotency_key="secret-output",
            )
        )
    finally:
        await adapter.close()

    assert isinstance(result, WorkerResult)
    assert result.output == {"token": "[REDACTED]", "context": "[REDACTED]"}
    assert "process-secret" not in result.model_dump_json()
    assert "context-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_process_adapter_redacts_known_secret_values_from_process_errors() -> None:
    adapter = ProcessAdapter(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.stderr.write(os.environ['AIAT_PROCESS_TOKEN']); sys.exit(3)",
        ],
        worker_id="process-error-redaction-worker",
        environment={"AIAT_PROCESS_TOKEN": "process-secret"},
    )

    try:
        result = await adapter._execute(
            WorkerRunRequest(
                worker_id="process-error-redaction-worker",
                task_type="secret-error",
                idempotency_key="secret-error",
            )
        )
    finally:
        await adapter.close()

    assert isinstance(result, WorkerResult)
    assert result.success is False
    assert result.error is not None
    assert result.error.message == "[REDACTED]"
    assert "process-secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "environment",
    [
        {"BAD=NAME": "value"},
        {"BAD": "value\x00"},
        {"BAD": 123},
    ],
)
def test_process_adapter_rejects_invalid_explicit_environment(environment) -> None:
    with pytest.raises((TypeError, ValueError), match="process environment"):
        ProcessAdapter(
            [sys.executable, "-c", "print('unused')"],
            worker_id="invalid-env",
            environment=environment,
        )


@pytest.mark.asyncio
async def test_process_adapter_normalizes_expected_termination_as_cancellation() -> None:
    adapter = ProcessAdapter(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        worker_id="process-cancellation-worker",
    )
    request = WorkerRunRequest(
        worker_id="process-cancellation-worker",
        task_type="cancellation",
        idempotency_key="process-cancellation",
    )

    try:
        await adapter.start(request)
        for _ in range(100):
            if request.run_id in adapter._processes:
                break
            await asyncio.sleep(0.01)
        assert request.run_id in adapter._processes

        receipt = await adapter.cancel(
            WorkerCancellation(
                run_id=request.run_id,
                reason="test termination",
                requested_by="test",
                force=False,
            )
        )
        events = [event async for event in adapter.events(request.run_id)]
    finally:
        await adapter.close()

    assert receipt.terminal is True
    assert receipt.runtime_status == "CANCELLED"
    assert [event.event_type for event in events][-1] == EventType.CANCELLED
    assert EventType.ERROR not in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_controller_settles_process_termination_as_cancelled() -> None:
    adapter = ProcessAdapter(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        worker_id="process-controller-cancellation-worker",
    )
    request = WorkerRunRequest(
        worker_id="process-controller-cancellation-worker",
        task_type="cancellation",
        idempotency_key="process-controller-cancellation",
    )
    controller = WorkerRunController()

    try:
        execution = asyncio.create_task(controller.execute(request, adapter))
        for _ in range(100):
            if (await controller.get_run(request.run_id) or {}).get("state") == "RUNNING":
                break
            await asyncio.sleep(0.01)
        assert (await controller.get_run(request.run_id) or {}).get("state") == "RUNNING"

        acknowledged = await controller.cancel(
            request.run_id,
            adapter,
            reason="operator requested cancellation",
            requested_by="test",
            force=False,
        )
        outcome = await asyncio.wait_for(execution, timeout=3)
    finally:
        await adapter.close()

    assert acknowledged is not None
    assert acknowledged["state"] in {"RUNNING", "CANCELLED"}
    assert outcome.state == "CANCELLED"
    assert (await controller.get_run(request.run_id))["state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_opt_in_process_tool_protocol_round_trips_through_worker_run_controller() -> None:
    dispatched = []

    async def tool_service(tool_request):
        dispatched.append(tool_request)
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={"success": True, "path": "src/example.py", "bytes_written": 10},
        )

    adapter = ProcessAdapter(
        [sys.executable, "-c", _bidirectional_tool_worker()],
        worker_id="process-mediated-worker",
        context=AdapterContext(tool_dispatcher=tool_service),
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=2.0,
    )
    request = WorkerRunRequest(
        worker_id="process-mediated-worker",
        task_type="project_file_generation",
        idempotency_key="process-mediated-tool-roundtrip",
        project_id=uuid4(),
        tool_grants=["file_write"],
    )

    try:
        outcome = await WorkerRunController().execute(request, adapter)
    finally:
        await adapter.close()

    assert outcome.state == "SUCCEEDED"
    assert len(dispatched) == 1
    assert dispatched[0].tool_name == "file_write"
    assert dispatched[0].arguments == {"path": "src/example.py", "content": "VALUE = 3\n"}
    assert [event.event_type for event in outcome.events].count(EventType.TOOL_REQUEST) == 1
    assert [event.event_type for event in outcome.events].count(EventType.TOOL_RESPONSE) == 1
    assert outcome.result is not None and outcome.result.success is True
    assert not adapter._pending_process_tool_requests
    assert not adapter._process_tool_timeout_tasks
    assert not adapter._processes


@pytest.mark.asyncio
async def test_opt_in_process_tool_protocol_propagates_failed_tool_effect() -> None:
    async def denied_tool_service(tool_request):
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=False,
            error={"code": "FILE_WRITE_DENIED", "message": "denied", "terminal": True},
        )

    adapter = ProcessAdapter(
        [sys.executable, "-c", _bidirectional_tool_worker()],
        worker_id="process-denied-tool-worker",
        context=AdapterContext(tool_dispatcher=denied_tool_service),
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=2.0,
    )
    request = WorkerRunRequest(
        worker_id="process-denied-tool-worker",
        task_type="project_file_generation",
        idempotency_key="process-denied-tool-effect",
        tool_grants=["file_write"],
    )

    try:
        outcome = await WorkerRunController().execute(request, adapter)
    finally:
        await adapter.close()

    assert outcome.state == "FAILED"
    assert outcome.result is not None and outcome.result.success is False
    assert outcome.result.error is not None
    assert outcome.result.error.code == "FILE_WRITE_DENIED"
    assert not adapter._processes


@pytest.mark.asyncio
async def test_opt_in_process_tool_protocol_rejects_mismatched_response_identity() -> None:
    async def mismatched_tool_service(tool_request):
        return WorkerToolResponse(
            request_id=uuid4(),
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={"success": True},
        )

    adapter = ProcessAdapter(
        [sys.executable, "-c", _bidirectional_tool_worker()],
        worker_id="process-mismatched-tool-worker",
        context=AdapterContext(tool_dispatcher=mismatched_tool_service),
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=2.0,
    )
    request = WorkerRunRequest(
        worker_id="process-mismatched-tool-worker",
        task_type="project_file_generation",
        idempotency_key="process-mismatched-tool-effect",
        tool_grants=["file_write"],
    )

    try:
        outcome = await WorkerRunController().execute(request, adapter)
    finally:
        await adapter.close()

    assert outcome.state == "FAILED"
    assert not adapter._processes
    assert not adapter._pending_process_tool_requests
    assert not adapter._process_tool_timeout_tasks


@pytest.mark.asyncio
async def test_opt_in_process_tool_protocol_timeout_kills_and_cleans_child() -> None:
    command = [
        sys.executable,
        "-c",
        "import json,sys,time; r=json.loads(sys.stdin.readline()); "
        "q={'request_id':'9bb99a44-805f-4454-93ad-a0442dc784a1','run_id':r['run_id'],"
        "'tool_name':'file_write','arguments':{},'permission_scope':['file_write'],"
        "'approval_required':False,'idempotency_key':'timeout-write'}; "
        "print(json.dumps({'run_id':r['run_id'],'worker_id':r['worker_id'],"
        "'event_type':'tool_request','tool_request':q}),flush=True); time.sleep(30)",
    ]
    adapter = ProcessAdapter(
        command,
        worker_id="process-tool-timeout-worker",
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=0.05,
    )
    request = WorkerRunRequest(
        worker_id="process-tool-timeout-worker",
        task_type="tool-timeout",
        idempotency_key="process-tool-timeout",
    )

    result = await adapter._execute(request)

    assert isinstance(result, WorkerResult)
    assert result.success is False
    assert result.error is not None and result.error.code == "TOOL_RESPONSE_TIMEOUT"
    assert request.run_id not in adapter._processes
    assert not adapter._pending_process_tool_requests
    assert not adapter._process_tool_timeout_tasks
    await adapter.close()


@pytest.mark.asyncio
async def test_opt_in_process_tool_protocol_cancellation_terminates_waiting_child() -> None:
    adapter = ProcessAdapter(
        [sys.executable, "-c", _bidirectional_tool_worker()],
        worker_id="process-tool-cancellation-worker",
        bidirectional_tool_protocol=True,
        tool_response_timeout_seconds=5.0,
    )
    request = WorkerRunRequest(
        worker_id="process-tool-cancellation-worker",
        task_type="tool-cancellation",
        idempotency_key="process-tool-cancellation",
    )
    events = []

    try:
        await adapter.start(request)
        async for event in adapter.events(request.run_id):
            events.append(event)
            if event.event_type == EventType.TOOL_REQUEST:
                receipt = await adapter.cancel(
                    WorkerCancellation(
                        run_id=request.run_id,
                        reason="cancel child while awaiting tool response",
                        requested_by="test",
                    )
                )
                assert receipt.accepted is True
                break
    finally:
        await adapter.close()

    assert request.run_id not in adapter._processes
    assert not adapter._pending_process_tool_requests
    assert not adapter._process_tool_timeout_tasks
    assert any(event.event_type == EventType.TOOL_REQUEST for event in events)
