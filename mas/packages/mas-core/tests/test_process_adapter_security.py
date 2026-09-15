"""PR B coverage for process-worker environment and secret boundaries."""

from __future__ import annotations

import asyncio
import sys

import pytest

from mas_core.worker_contract import (
    AdapterContext,
    EventType,
    WorkerCancellation,
    WorkerResult,
    WorkerRunRequest,
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
