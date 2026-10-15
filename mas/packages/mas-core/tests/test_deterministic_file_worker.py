"""Tests for the bounded, model-free file-write request worker."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_contract import (
    AdapterContext,
    EventType,
    NativeWorkerAdapter,
    WorkerArtifact,
    WorkerCancellation,
    WorkerResult,
    WorkerRunController,
    WorkerRunError,
    WorkerRunRequest,
    WorkerToolResponse,
)
from mas_core.worker_registry.deterministic_file_worker import (
    MAX_CONTENT_BYTES,
    TASK_TYPE,
    WORKER_ID,
    generate_project_file,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = Path(__file__).resolve().parents[1] / "mas_core" / "worker_registry" / "deterministic_file_worker.py"
PROCESS_ENTRYPOINT = REPO_ROOT / "mas" / "workers" / "aiat_deterministic_file_worker_process.py"
MANIFEST_PATH = REPO_ROOT / "mas" / "workers" / f"{WORKER_ID}.yaml"
_UNSET = object()


def _request(
    *,
    task_type: str = TASK_TYPE,
    project_id=_UNSET,
    task_input: dict | None = None,
    tool_grants: list[str] | None = None,
) -> WorkerRunRequest:
    if project_id is _UNSET:
        project_id = uuid4()
    return WorkerRunRequest(
        idempotency_key="deterministic-file-worker-test",
        worker_id=WORKER_ID,
        task_type=task_type,
        task_input=task_input or {"path": "src/expenses.py", "content": "TOTAL = 1\n"},
        project_id=project_id,
        tool_grants=tool_grants if tool_grants is not None else ["file_write"],
    )


@pytest.mark.asyncio
async def test_native_adapter_correlates_successful_project_scoped_file_write_response() -> None:
    request = _request()
    adapter = NativeWorkerAdapter(
        generate_project_file,
        worker_id=WORKER_ID,
        runtime_version="aiat-deterministic-file-worker/0.1.0",
    )

    accepted = await adapter.start(request)
    events = []
    async for event in adapter.events(request.run_id):
        events.append(event)
        if event.event_type == EventType.TOOL_REQUEST:
            tool_request = event.tool_request
            assert tool_request is not None
            # A wrong request ID must not release the active worker.
            await adapter.deliver_tool_response(
                WorkerToolResponse(
                    request_id=uuid4(),
                    run_id=request.run_id,
                    tool_name="file_write",
                    success=True,
                    result={"success": True, "path": "src/expenses.py", "bytes_written": 10},
                )
            )
            await adapter.deliver_tool_response(
                WorkerToolResponse(
                    request_id=tool_request.request_id,
                    run_id=request.run_id,
                    tool_name="file_read",
                    success=True,
                    result={"success": True, "path": "src/expenses.py", "bytes_written": 10},
                )
            )
            await adapter.deliver_tool_response(
                WorkerToolResponse(
                    request_id=tool_request.request_id,
                    run_id=request.run_id,
                    tool_name="file_write",
                    success=True,
                    result={"success": True, "path": "src/expenses.py", "bytes_written": 10},
                )
            )

    tool_events = [event for event in events if event.event_type == EventType.TOOL_REQUEST]
    assert accepted.run_id == request.run_id
    assert len(tool_events) == 1
    tool_request = tool_events[0].tool_request
    assert tool_request is not None
    assert tool_request.run_id == request.run_id
    assert tool_request.tool_name == "file_write"
    assert tool_request.arguments == {"path": "src/expenses.py", "content": "TOTAL = 1\n"}
    assert tool_request.permission_scope == ["file_write"]
    assert tool_request.approval_required is False
    assert tool_request.idempotency_key
    assert "project_id" not in tool_request.arguments
    assert events[-1].event_type == EventType.RESULT
    assert events[-1].result is not None
    assert events[-1].result.output["file_write_confirmed"] is True
    assert events[-1].result.completion_criteria["file_write_confirmed_by_tool_service"] is True
    await adapter.close()


@pytest.mark.asyncio
async def test_worker_run_controller_mediates_file_write_before_worker_success() -> None:
    dispatched = []

    async def tool_service(tool_request):
        dispatched.append(tool_request)
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={
                "success": True,
                "project_id": str(request.project_id),
                "path": "src/expenses.py",
                "bytes_written": len(b"TOTAL = 1\n"),
                "sha256": hashlib.sha256(b"TOTAL = 1\n").hexdigest(),
            },
        )

    request = _request()
    adapter = NativeWorkerAdapter(
        generate_project_file,
        worker_id=WORKER_ID,
        context=AdapterContext(tool_dispatcher=tool_service),
    )
    outcome = await WorkerRunController().execute(request, adapter)

    assert outcome.state == "SUCCEEDED"
    assert len(dispatched) == 1
    assert dispatched[0].tool_name == "file_write"
    assert [event.event_type for event in outcome.events].count(EventType.TOOL_REQUEST) == 1
    assert [event.event_type for event in outcome.events].count(EventType.TOOL_RESPONSE) == 1
    assert outcome.result is not None
    assert outcome.result.completion_criteria["file_write_confirmed_by_tool_service"] is True
    assert len(outcome.result.artifacts) == 1
    artifact = outcome.result.artifacts[0]
    assert artifact.uri == "src/expenses.py"
    assert artifact.sha256 == hashlib.sha256(b"TOTAL = 1\n").hexdigest()
    assert artifact.metadata["worker_run_id"] == str(request.run_id)
    assert artifact.metadata["project_id"] == str(request.project_id)
    assert artifact.metadata["source_tool"] == "file_write"
    assert artifact.metadata["tool_effect_id"]
    await adapter.close()


@pytest.mark.asyncio
async def test_mediated_file_artifact_is_registered_before_claim_fenced_evidence_write() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    request = _request()
    request._execution_claim_fence = {
        "claim_owner": "executor-test",
        "attempt_count": 1,
    }
    content = b"TOTAL = 1\n"
    artifact = WorkerArtifact(
        kind="file",
        name="src/expenses.py",
        uri="src/expenses.py",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        metadata={
            "worker_run_id": str(request.run_id),
            "project_id": str(request.project_id),
            "source_tool": "file_write",
        },
    )
    registrar = AsyncMock()
    atomic_writer = AsyncMock(return_value={"artifacts": [], "usage": {}})
    storage = SimpleNamespace(
        persist_worker_result_evidence_claim_fenced=atomic_writer,
    )
    adapter = SimpleNamespace(
        context=SimpleNamespace(artifact_registrar=registrar),
    )

    await WorkerRunController(storage=storage)._persist_result_evidence(
        request,
        WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            artifacts=[artifact],
        ),
        adapter=adapter,
    )

    registrar.assert_awaited_once_with(artifact, claim_fence=request.execution_claim_fence)
    atomic_writer.assert_awaited_once()
    assert atomic_writer.await_args.kwargs["artifacts"][0]["metadata"]["source_tool"] == "file_write"


@pytest.mark.asyncio
async def test_registered_file_artifact_is_cleaned_up_when_claim_fenced_evidence_write_fails() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    request = _request()
    request._execution_claim_fence = {"claim_owner": "executor-test", "attempt_count": 2}
    artifact = WorkerArtifact(
        kind="file",
        name="src/expenses.py",
        uri="src/expenses.py",
        sha256=hashlib.sha256(b"TOTAL = 1\n").hexdigest(),
        size_bytes=len(b"TOTAL = 1\n"),
        metadata={
            "worker_run_id": str(request.run_id),
            "project_id": str(request.project_id),
            "source_tool": "file_write",
        },
    )
    registrar = AsyncMock()
    atomic_writer = AsyncMock(return_value=None)
    adapter = SimpleNamespace(context=SimpleNamespace(artifact_registrar=registrar))
    controller = WorkerRunController(
        storage=SimpleNamespace(persist_worker_result_evidence_claim_fenced=atomic_writer)
    )

    with pytest.raises(WorkerRunError) as caught:
        await controller._persist_result_evidence(
            request,
            WorkerResult(
                run_id=request.run_id,
                worker_id=request.worker_id,
                success=True,
                artifacts=[artifact],
            ),
            adapter=adapter,
        )

    assert caught.value.code == "WORKER_RUN_CLAIM_LOST"
    assert registrar.await_args_list[0].kwargs == {"claim_fence": request.execution_claim_fence}
    assert registrar.await_args_list[1].kwargs == {
        "claim_fence": request.execution_claim_fence,
        "cleanup": True,
    }


@pytest.mark.asyncio
async def test_partial_file_artifact_registration_cleans_previously_registered_objects() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    request = _request()
    request._execution_claim_fence = {"claim_owner": "executor-test", "attempt_count": 3}
    artifacts = [
        WorkerArtifact(
            kind="file",
            name=f"src/file-{index}.py",
            uri=f"src/file-{index}.py",
            sha256=hashlib.sha256(f"VALUE = {index}\n".encode()).hexdigest(),
            size_bytes=len(f"VALUE = {index}\n".encode()),
            metadata={
                "worker_run_id": str(request.run_id),
                "project_id": str(request.project_id),
                "source_tool": "file_write",
            },
        )
        for index in range(2)
    ]
    calls: list[tuple[WorkerArtifact, bool]] = []

    async def registrar(artifact, *, claim_fence=None, cleanup=False):
        calls.append((artifact, cleanup))
        if artifact is artifacts[1] and not cleanup:
            raise RuntimeError("bounded registrar failure")

    atomic_writer = AsyncMock()
    adapter = SimpleNamespace(context=SimpleNamespace(artifact_registrar=registrar))
    controller = WorkerRunController(
        storage=SimpleNamespace(persist_worker_result_evidence_claim_fenced=atomic_writer)
    )

    with pytest.raises(RuntimeError, match="bounded registrar failure"):
        await controller._persist_result_evidence(
            request,
            WorkerResult(
                run_id=request.run_id,
                worker_id=request.worker_id,
                success=True,
                artifacts=artifacts,
            ),
            adapter=adapter,
        )

    assert calls == [(artifacts[0], False), (artifacts[1], False), (artifacts[0], True)]
    atomic_writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_file_write_without_integrity_metadata_cannot_settle_successfully() -> None:
    async def incomplete_tool_service(tool_request):
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={
                "success": True,
                "project_id": str(request.project_id),
                "path": "src/expenses.py",
                "bytes_written": len(b"TOTAL = 1\n"),
            },
        )

    request = _request()
    adapter = NativeWorkerAdapter(
        generate_project_file,
        worker_id=WORKER_ID,
        context=AdapterContext(tool_dispatcher=incomplete_tool_service),
    )

    controller = WorkerRunController()
    outcome = await controller.execute(request, adapter)

    assert outcome.state == "FAILED"
    assert outcome.result is not None
    assert outcome.result.success is False
    assert outcome.result.error is not None
    assert outcome.result.error.code == "TOOL_ARTIFACT_INTEGRITY_INVALID"
    assert outcome.result.artifacts == []
    await adapter.close()


@pytest.mark.asyncio
async def test_failed_tool_effect_does_not_become_worker_success() -> None:
    request = _request()

    async def denied_tool_service(tool_request):
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=False,
            error={"code": "FILE_WRITE_DENIED", "message": "denied", "terminal": True},
        )

    adapter = NativeWorkerAdapter(
        generate_project_file,
        worker_id=WORKER_ID,
        context=AdapterContext(tool_dispatcher=denied_tool_service),
    )
    outcome = await WorkerRunController().execute(request, adapter)

    assert outcome.state == "FAILED"
    assert outcome.result is not None and outcome.result.success is False
    assert outcome.result.error is not None
    assert outcome.result.error.code == "FILE_WRITE_DENIED"
    assert all(event.event_type != EventType.RESULT for event in outcome.events)
    await adapter.close()


@pytest.mark.asyncio
async def test_tool_response_timeout_is_bounded_and_normalized(monkeypatch) -> None:
    from mas_core.worker_registry import deterministic_file_worker as worker_module

    monkeypatch.setattr(worker_module, "TOOL_RESPONSE_TIMEOUT_SECONDS", 0.01)
    request = _request()
    adapter = NativeWorkerAdapter(generate_project_file, worker_id=WORKER_ID)
    await adapter.start(request)
    events = [event async for event in adapter.events(request.run_id)]
    assert events[-1].event_type == EventType.ERROR
    assert events[-1].error is not None
    assert events[-1].error.code == "TOOL_RESPONSE_TIMEOUT"
    await adapter.close()


@pytest.mark.asyncio
async def test_cancellation_wakes_worker_waiting_for_tool_response() -> None:
    request = _request()
    adapter = NativeWorkerAdapter(generate_project_file, worker_id=WORKER_ID)
    await adapter.start(request)
    stream = adapter.events(request.run_id)
    events = []
    async for event in stream:
        events.append(event)
        if event.event_type == EventType.TOOL_REQUEST:
            receipt = await adapter.cancel(
                WorkerCancellation(
                    run_id=request.run_id,
                    reason="test cancellation while awaiting mediated write",
                    requested_by="unit-test",
                )
            )
            assert receipt.accepted is True
    assert any(event.event_type == EventType.CANCELLED for event in events)
    assert not any(event.event_type == EventType.RESULT for event in events)
    await adapter.close()


@pytest.mark.parametrize(
    "path",
    ["../escape.py", "/absolute.py", "src/../escape.py", "src\\escape.py", "src//empty.py", "src/./same.py", "src/"],
)
@pytest.mark.asyncio
async def test_worker_rejects_paths_outside_its_bounded_relative_path_contract(path: str) -> None:
    with pytest.raises(ValueError, match="safe relative workspace segments"):
        await generate_project_file(
            _request(task_input={"path": path, "content": "VALUE = 1\n"}),
            object(),
        )


@pytest.mark.asyncio
async def test_worker_rejects_non_project_tasks_ungranted_tools_and_unbounded_input() -> None:
    with pytest.raises(ValueError, match="unsupported task type"):
        await generate_project_file(_request(task_type="shell.run"), object())

    with pytest.raises(ValueError, match="project-scoped"):
        await generate_project_file(_request(project_id=None), object())

    with pytest.raises(ValueError, match="not granted"):
        await generate_project_file(_request(tool_grants=[]), object())

    with pytest.raises(ValueError, match="exactly path and content"):
        await generate_project_file(
            _request(task_input={"path": "src/expenses.py", "content": "x", "project_id": "other"}),
            object(),
        )

    with pytest.raises(ValueError, match="64 KiB"):
        await generate_project_file(
            _request(task_input={"path": "src/expenses.py", "content": "x" * (MAX_CONTENT_BYTES + 1)}),
            object(),
        )


def test_manifest_is_pending_and_pins_exact_sandboxed_worker_image() -> None:
    raw = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = WorkerManifest.model_validate(raw)
    source_hash = hashlib.sha256(PROCESS_ENTRYPOINT.read_bytes()).hexdigest()

    assert manifest.metadata.id == WORKER_ID
    assert manifest.metadata.version == "0.1.1"
    assert manifest.metadata.source_repo == "https://github.com/Maaroufabousaleh/AIAT"
    assert manifest.metadata.source_revision == "1855a75fb17243d8298b84468372e7a6e496da46"
    assert manifest.metadata.version_pin == (
        "0.1.1+sha256.fb1a347b40350519451872e6d4d818da757355d0bc3c7314f74563bed42407cc"
    )
    assert manifest.source_provenance["source_provider"] == "aiat"
    assert manifest.source_provenance["commit_sha"] == manifest.metadata.source_revision
    assert manifest.source_provenance["runtime_fingerprint"] == f"sha256:{source_hash}"
    assert manifest.runtime.transport == "oci"
    assert manifest.runtime.adapter_config["image"] == (
        "aiat-deterministic-file-worker@sha256:"
        "fb1a347b40350519451872e6d4d818da757355d0bc3c7314f74563bed42407cc"
    )
    assert manifest.runtime.adapter_config["sandbox_runtime"] == "runsc"
    assert manifest.runtime.adapter_config["bidirectional_tool_protocol"] is True
    assert manifest.execution is not None
    assert manifest.execution.trust == "untrusted"
    assert manifest.execution.minimum_isolation == "sandboxed"
    assert manifest.execution.gvisor_compatible is True
    assert manifest.sandbox.profile == "sandboxed"
    assert manifest.sandbox.network_mode == "egress-deny-all"
    assert manifest.sandbox.egress_allowlist == []
    assert any(
        "Local development image identity only" in note
        for note in manifest.source_provenance["notes"]
    )
    assert manifest.model_mode == "none"
    assert manifest.tool_grants == ["file_write"]
    assert manifest.metadata.evaluation_status == "pending"
    assert manifest.certification_status == "pending"
    assert manifest.active_adapter_version is None
    assert manifest.active_skill_bundle_id is None
