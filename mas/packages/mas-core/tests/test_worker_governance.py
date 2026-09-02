"""Behavioral tests for the governed universal-worker foundation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from uuid import uuid4

import httpx
import pytest

from mas_core.llm_gateway.model_profiles import (
    ModelPolicyConstraints,
    ModelPolicyLayer,
    ModelProfile,
    ModelProfileStatus,
    ModelProfileVersion,
    ModelResolutionError,
    ModelResolutionRequest,
    PrivacyClass,
)
from mas_core.llm_gateway.model_resolver import ModelProfileResolver
from mas_core.worker_contract import (
    AdapterContext,
    ConformanceRunner,
    EventType,
    NativeWorkerAdapter,
    WorkerEvent,
    WorkerProgress,
    WorkerResult,
    WorkerRunController,
    WorkerRunError,
    WorkerRunRequest,
    WorkerToolRequest,
    WorkerToolResponse,
    issue_opencode_tool_grant,
    verify_opencode_tool_grant,
)
from mas_core.worker_registry.runtime_adapters import (
    CrewAIAdapter,
    LangGraphAdapter,
    OCIAdapter,
    OpenCodeAdapter,
    OpenCodeInterfaceVerification,
    ProcessAdapter,
)
from mas_core.worker_registry.steward import (
    BundleStatus,
    CandidateIntakeStatus,
    ExternalProvenance,
    ExternalWorkerSteward,
    RolloutStatus,
    StewardStatus,
    StewardTransitionError,
)


def _profile(*, profile_id: str = "coding", model_id: str = "openai/gpt-test", local: bool = False) -> ModelProfile:
    version = ModelProfileVersion(
        version="1",
        provider_id=model_id.split("/", 1)[0],
        exact_model_id=model_id,
        capabilities=frozenset({"tool_calling", "structured_output", "streaming"}),
        context_window=131_072,
        max_output_tokens=16_384,
        tool_calling=True,
        structured_output=True,
        streaming=True,
        privacy_class=PrivacyClass.INTERNAL,
    regions=frozenset({"ca"}),
    local=local,
    status=ModelProfileStatus.APPROVED,
)
    return ModelProfile(
        profile_id=profile_id,
        purpose="test profile",
        approved_provider_ids=frozenset({version.provider_id}),
        versions=(version,),
        status=ModelProfileStatus.APPROVED,
    )


def test_worker_run_request_carries_bounded_trace_context_outside_task_input() -> None:
    request = WorkerRunRequest(
        idempotency_key="trace-context-test",
        worker_id="tester",
        task_type="verification",
        task_input={"trace_id": "payload-must-not-be-authoritative"},
        trace_id="trace-123",
        span_id="span-456",
    )

    assert request.trace_id == "trace-123"
    assert request.span_id == "span-456"
    assert request.task_input["trace_id"] == "payload-must-not-be-authoritative"
    with pytest.raises(ValueError, match="bounded safe values"):
        WorkerRunRequest(
            idempotency_key="unsafe-trace-context",
            worker_id="tester",
            task_type="verification",
            trace_id="trace value with spaces",
        )
def test_model_resolution_intersects_constraints_and_records_rejections() -> None:
    request = ModelResolutionRequest(
        task_type="coding",
        layers=(
            ModelPolicyLayer(
                name="organization",
                constraints=ModelPolicyConstraints(local_only=True),
            ),
        ),
        worker_required_capabilities=frozenset({"tool_calling"}),
    )

    with pytest.raises(ModelResolutionError) as exc_info:
        ModelProfileResolver().resolve([_profile()], request)

    assert exc_info.value.code == "NO_COMPLIANT_MODEL"
    assert any("local-only policy" in reason for reason in exc_info.value.rejected_candidates[0].reasons)

    resolved = ModelProfileResolver().resolve([_profile(local=True, model_id="local/qwen")], request)
    assert resolved.exact_model_id == "local/qwen"
    assert resolved.capability_checks["tool_calling"] is True
    assert resolved.fallback_chain == ("coding:1",)


def test_raw_model_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="raw model IDs"):
        ModelResolutionRequest(task_type="coding", requested_raw_model_id="gpt-4o")


def test_requested_profile_never_falls_back_to_an_unrelated_profile() -> None:
    requested = _profile(profile_id="requested", model_id="local/requested", local=True)
    unrelated = _profile(profile_id="unrelated", model_id="local/unrelated", local=True)
    request = ModelResolutionRequest(
        task_type="coding",
        requested_profile_id="requested",
        worker_required_capabilities=frozenset({"vision"}),
    )

    with pytest.raises(ModelResolutionError) as exc_info:
        ModelProfileResolver().resolve([requested, unrelated], request)

    assert exc_info.value.code == "NO_COMPLIANT_MODEL"
    assert any(
        "requested profile fallback lineage" in reason
        for candidate in exc_info.value.rejected_candidates
        if candidate.profile_id == "unrelated"
        for reason in candidate.reasons
    )


def test_opencode_requires_a_committed_phase_0b_report() -> None:
    pending = OpenCodeInterfaceVerification.from_report(
        {"report_id": "opencode-phase0b-pending"}
    )
    assert pending.approved is False
    with pytest.raises(ValueError, match="not committed"):
        OpenCodeInterfaceVerification.from_report({"report_id": "made-up-report"})

    verified = OpenCodeInterfaceVerification.from_report(
        {"report_id": "opencode-phase0b-1.17.13"}
    )
    assert {
        "event-fixtures/live-event-summary.json",
        "request-response-fixtures/live-interface-summary.json",
    } <= set(verified.evidence["fixture_refs"])
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", digest)
        for digest in verified.evidence["fixture_sha256"].values()
    )


def test_opencode_tool_grant_is_short_lived_and_tamper_evident() -> None:
    run_id = uuid4()
    token = issue_opencode_tool_grant(
        "test-tool-secret",
        worker_id="opencode-test",
        run_id=run_id,
        project_id=None,
        tool_names=["repo.read"],
        now=1_000,
    )
    grant = verify_opencode_tool_grant(token, "test-tool-secret", now=1_001)
    assert grant.worker_id == "opencode-test"
    assert grant.run_id == run_id
    assert grant.tool_names == {"repo.read"}
    with pytest.raises(ValueError):
        verify_opencode_tool_grant(f"{token}x", "test-tool-secret", now=1_001)
    with pytest.raises(ValueError):
        verify_opencode_tool_grant(token, "test-tool-secret", now=1_301)


def test_opencode_cannot_override_certified_endpoint_paths() -> None:
    verification = OpenCodeInterfaceVerification(
        release="1.2.3",
        commit_sha="a" * 40,
        report_version="phase-0b.v1",
        approved=True,
        openapi_sha256="a" * 64,
        config_schema_sha256="b" * 64,
        endpoints={
            "health": "/health",
            "openapi": "/openapi.json",
            "project_current": "/project/current",
            "session_list": "/session",
            "session_create": "/session",
            "session_get": "/session/{sessionID}",
            "session_delete": "/session/{sessionID}",
            "session_status": "/session/status",
            "prompt_async": "/session/{sessionID}/prompt_async",
            "events": "/event",
            "abort": "/session/{sessionID}/abort",
            "messages": "/session/{sessionID}/message",
            "diff": "/session/{sessionID}/diff",
            "permission_reply": "/session/{sessionID}/permissions/{permissionID}",
            "mcp_add": "/mcp",
            "mcp_status": "/mcp",
        },
        supported_model_pattern=r"^[^/]+/.+$",
        evidence={"report_id": "test-approved-report"},
    )

    with pytest.raises(ValueError, match="must exactly match"):
        OpenCodeAdapter(
            verification,
            base_url="http://opencode.invalid",
            worker_id="opencode-test",
            endpoints={"run": "/unreviewed-run"},
        )


@pytest.mark.asyncio
async def test_opencode_session_adapter_maps_live_lifecycle_and_basic_auth() -> None:
    endpoints = {
        "health": "/global/health",
        "openapi": "/doc",
        "project_current": "/project/current",
        "session_list": "/session",
        "session_create": "/session",
        "session_get": "/session/{sessionID}",
        "session_delete": "/session/{sessionID}",
        "session_status": "/session/status",
        "messages": "/session/{sessionID}/message",
        "prompt_async": "/session/{sessionID}/prompt_async",
        "events": "/global/event",
        "abort": "/session/{sessionID}/abort",
        "diff": "/session/{sessionID}/diff",
        "permission_reply": "/session/{sessionID}/permissions/{permissionID}",
        "mcp_add": "/mcp",
        "mcp_status": "/mcp",
    }
    schema = {"openapi": "3.1.0", "paths": {"/global/health": {"get": {"operationId": "global.health"}}}}
    schema_hash = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    verification = OpenCodeInterfaceVerification(
        release="1.17.13",
        commit_sha="F8C45BAE73A8F1E2088023FDD34DC2FE0A7F93F505F073E0703E4E1A19AFE8FF",
        report_version="2",
        approved=True,
        openapi_sha256=schema_hash,
        config_schema_sha256="b" * 64,
        endpoints=endpoints,
        evidence={"approval_record_id": "test", "fixture_refs": ["test"]},
    )
    seen: list[httpx.Request] = []
    mcp_names: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True}, request=request)
        if request.url.path == "/doc":
            return httpx.Response(200, json=schema, request=request)
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_test", "title": "test"}, request=request)
        if request.url.path == "/mcp" and request.method == "POST":
            mcp_names.append(json.loads(request.content)["name"])
            return httpx.Response(200, json={"name": "aiat-test"}, request=request)
        if request.url.path == "/mcp" and request.method == "GET":
            return httpx.Response(
                200,
                json={mcp_names[-1]: {"status": "connected"}},
                request=request,
            )
        if request.url.path == "/global/event":
            body = "data: {\"payload\":{\"type\":\"server.connected\",\"properties\":{}},\"id\":\"evt_1\"}\n\ndata: {\"payload\":{\"type\":\"session.idle\",\"properties\":{\"sessionID\":\"ses_test\"}},\"id\":\"evt_2\"}\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}, request=request)
        if request.url.path == "/session/ses_test/prompt_async":
            return httpx.Response(204, request=request)
        if request.url.path == "/session/ses_test/message":
            return httpx.Response(200, json=[{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "done"}]}], request=request)
        if request.url.path == "/session/ses_test/diff":
            return httpx.Response(200, json=[], request=request)
        if request.url.path == "/session/ses_test/abort":
            return httpx.Response(200, json=True, request=request)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(
        base_url="http://opencode.test",
        auth=httpx.BasicAuth("aiat", "test-password"),
        transport=httpx.MockTransport(handler),
    )
    adapter = OpenCodeAdapter(
        verification,
        base_url="http://opencode.test",
        worker_id="opencode-test",
        client=client,
        context=AdapterContext(secrets={"opencode_username": "aiat", "opencode_password": "test-password", "tool_secret": "test-tool-secret"}),
    )
    request = WorkerRunRequest(
        idempotency_key="opencode-session-test",
        worker_id="opencode-test",
        task_type="test",
        task_input={"prompt": "return done"},
        resolved_model_profile={"profile_id": "coding", "exact_model_id": "aiat/gpt-test"},
        timeout_seconds=10,
    )
    accepted = await asyncio.wait_for(adapter.start(request), timeout=5)
    assert accepted.runtime_run_id == "ses_test"
    async def collect() -> list[WorkerEvent]:
        return [event async for event in adapter.events(request.run_id)]
    events = await asyncio.wait_for(collect(), timeout=5)
    assert any(event.event_type == EventType.RESULT and event.result and event.result.output == "done" for event in events)
    assert any(req.headers.get("Authorization", "").startswith("Basic ") for req in seen)
    await adapter.close()


@pytest.mark.asyncio
async def test_opencode_bridge_refreshes_expiring_grant_with_same_name() -> None:
    endpoints = {
        "health": "/global/health",
        "openapi": "/doc",
        "project_current": "/project/current",
        "session_list": "/session",
        "session_create": "/session",
        "session_get": "/session/{sessionID}",
        "session_delete": "/session/{sessionID}",
        "session_status": "/session/status",
        "messages": "/session/{sessionID}/message",
        "prompt_async": "/session/{sessionID}/prompt_async",
        "events": "/global/event",
        "abort": "/session/{sessionID}/abort",
        "diff": "/session/{sessionID}/diff",
        "permission_reply": "/session/{sessionID}/permissions/{permissionID}",
        "mcp_add": "/mcp",
        "mcp_status": "/mcp",
    }
    verification = OpenCodeInterfaceVerification(
        release="1.17.13",
        commit_sha="F8C45BAE73A8F1E2088023FDD34DC2FE0A7F93F505F073E0703E4E1A19AFE8FF",
        report_version="2",
        approved=True,
        openapi_sha256="a" * 64,
        config_schema_sha256="b" * 64,
        endpoints=endpoints,
        evidence={"approval_record_id": "test", "fixture_refs": ["test"]},
    )
    bridge_configs: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp" and request.method == "POST":
            config = json.loads(request.content)
            bridge_configs.append(config)
            return httpx.Response(200, json={config["name"]: {"status": "connected"}}, request=request)
        if request.url.path == "/mcp" and request.method == "GET":
            return httpx.Response(
                200,
                json={bridge_configs[-1]["name"]: {"status": "connected"}},
                request=request,
            )
        return httpx.Response(404, request=request)

    adapter = OpenCodeAdapter(
        verification,
        base_url="http://opencode.test",
        worker_id="opencode-test",
        client=httpx.AsyncClient(base_url="http://opencode.test", transport=httpx.MockTransport(handler)),
        context=AdapterContext(
            secrets={
                "opencode_password": "test-password",
                "tool_secret": "test-tool-secret",
            }
        ),
    )
    request = WorkerRunRequest(
        idempotency_key="opencode-grant-refresh",
        worker_id="opencode-test",
        task_type="test",
        tool_grants=["opencode.workspace_read"],
    )
    adapter._MCP_GRANT_TTL_SECONDS = 2
    adapter._MCP_GRANT_REFRESH_LEEWAY_SECONDS = 1

    try:
        name = await adapter._configure_tool_bridge(request)
        first_grant = bridge_configs[-1]["config"]["headers"]["X-AIAT-OpenCode-Grant"]
        adapter._ensure_mcp_grant_refresher(request)
        for _ in range(60):
            if len(bridge_configs) == 2:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("OpenCode MCP grant was not refreshed before expiry")

        refreshed_name = bridge_configs[-1]["name"]
        refreshed_grant = bridge_configs[-1]["config"]["headers"]["X-AIAT-OpenCode-Grant"]

        assert refreshed_name == name
        assert len(bridge_configs) == 2
        assert refreshed_grant != first_grant
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_native_worker_runs_through_controller_and_conformance() -> None:
    async def worker(request: WorkerRunRequest, adapter: NativeWorkerAdapter) -> WorkerResult:
        await adapter.emit_progress(request.run_id, "working", percent=50)
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"echo": request.task_input["value"]},
        )

    adapter = NativeWorkerAdapter(worker, worker_id="native-test")
    controller = WorkerRunController()
    request = WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key="native-test-1",
        worker_id="native-test",
        task_type="echo",
        task_input={"value": "ok"},
    )

    outcome = await controller.execute(request, adapter)
    assert outcome.state == "SUCCEEDED"
    assert outcome.result is not None and outcome.result.output == {"echo": "ok"}
    assert [event.event_type for event in outcome.events] == [EventType.ACCEPTED, EventType.PROGRESS, EventType.RESULT]

    report = await ConformanceRunner().run(adapter, worker_id="native-test")
    assert report.passed is True
    await adapter.close()


@pytest.mark.asyncio
async def test_framework_transport_adapters_share_the_universal_conformance_suite() -> None:
    async def worker(request: WorkerRunRequest, adapter: NativeWorkerAdapter) -> WorkerResult:
        await adapter.emit_progress(request.run_id, "framework bridge", percent=100)
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output={"runtime": adapter.runtime_type, "value": request.task_input["value"]},
        )

    for adapter_type in (LangGraphAdapter, CrewAIAdapter):
        adapter = adapter_type(worker, worker_id=f"{adapter_type.runtime_type}-contract")
        report = await ConformanceRunner().run(
            adapter,
            worker_id=adapter.worker_id,
            task_input={"value": "ok"},
        )
        assert report.passed is True
        assert report.adapter_type == adapter_type.runtime_type
        await adapter.close()


@pytest.mark.asyncio
async def test_worker_tool_requests_are_mediated_and_not_direct_runtime_calls() -> None:
    async def worker(request: WorkerRunRequest, _adapter: NativeWorkerAdapter):
        yield WorkerEvent(
            run_id=request.run_id,
            worker_id=request.worker_id,
            event_type=EventType.TOOL_REQUEST,
            tool_request=WorkerToolRequest(
                run_id=request.run_id,
                tool_name="web.search",
                arguments={"query": "AIAT"},
                idempotency_key="tool-request-1",
            ),
        )
        yield WorkerResult(run_id=request.run_id, worker_id=request.worker_id, success=True, output={"done": True})

    async def mediator(tool_request: WorkerToolRequest) -> WorkerToolResponse:
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=True,
            result={"results": []},
        )

    adapter = NativeWorkerAdapter(
        worker,
        worker_id="native-tool-test",
        context=AdapterContext(tool_dispatcher=mediator),
    )
    request = WorkerRunRequest(
        idempotency_key="native-tool-test-1",
        worker_id="native-tool-test",
        task_type="tool-test",
        tool_grants=["web.search"],
    )
    outcome = await WorkerRunController().execute(request, adapter)

    assert outcome.state == "SUCCEEDED"
    assert EventType.TOOL_RESPONSE in [event.event_type for event in outcome.events]
    await adapter.close()


@pytest.mark.asyncio
async def test_controller_preserves_cancelled_terminal_state_when_adapter_finishes_late() -> None:
    async def slow_worker(_request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
        await asyncio.sleep(10)
        return WorkerResult(run_id=_request.run_id, worker_id=_request.worker_id, success=True, output="late")

    adapter = NativeWorkerAdapter(slow_worker, worker_id="cancel-test")
    controller = WorkerRunController()
    request = WorkerRunRequest(idempotency_key="cancel-test-1", worker_id="cancel-test", task_type="slow")
    execution = asyncio.create_task(controller.execute(request, adapter))
    for _ in range(50):
        row = await controller.get_run(request.run_id)
        if row and row.get("state") == "RUNNING":
            break
        await asyncio.sleep(0.01)
    await controller.cancel(request.run_id, adapter, reason="operator cancelled", requested_by="operator", force=True)
    outcome = await asyncio.wait_for(execution, timeout=5)
    assert outcome.state == "CANCELLED"
    assert (await controller.get_run(request.run_id))["state"] == "CANCELLED"
    await adapter.close()


@pytest.mark.asyncio
async def test_terminal_completion_wins_over_an_inflight_pause() -> None:
    terminal_gate = asyncio.Event()
    pause_started = asyncio.Event()
    release_pause = asyncio.Event()

    async def worker(request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
        await terminal_gate.wait()
        return WorkerResult(
            run_id=request.run_id,
            worker_id=request.worker_id,
            success=True,
            output="completed while pause was being acknowledged",
        )

    class PauseGateAdapter(NativeWorkerAdapter):
        async def pause(self, _request) -> None:
            pause_started.set()
            await release_pause.wait()

    adapter = PauseGateAdapter(worker, worker_id="pause-race")
    controller = WorkerRunController()
    request = WorkerRunRequest(
        idempotency_key="pause-race-1",
        worker_id="pause-race",
        task_type="slow",
    )
    execution = asyncio.create_task(controller.execute(request, adapter))
    for _ in range(50):
        if (await controller.get_run(request.run_id) or {}).get("state") == "RUNNING":
            break
        await asyncio.sleep(0.01)

    pause = asyncio.create_task(
        controller.pause(
            request.run_id,
            adapter,
            reason="operator review",
            requested_by="operator",
        )
    )
    await asyncio.wait_for(pause_started.wait(), timeout=2)
    terminal_gate.set()

    outcome = await asyncio.wait_for(execution, timeout=2)
    assert outcome.state == "SUCCEEDED"
    assert (await controller.get_run(request.run_id))["state"] == "SUCCEEDED"

    release_pause.set()
    with pytest.raises(WorkerRunError, match="state changed"):
        await asyncio.wait_for(pause, timeout=2)
    await adapter.close()


def test_external_steward_requires_gates_before_rollout() -> None:
    steward = ExternalWorkerSteward(
        worker_id="opencode-worker",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/opencode",
            exact_release="1.0.0",
            commit_sha="a" * 40,
            transport_type="http",
            license_id="MIT",
            redistribution_status="approved",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    assert candidate.intake_status == CandidateIntakeStatus.DISCOVERED

    for status in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.LICENSE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, status)

    certification = steward.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={"license": True, "security": True},
        approved_by="reviewer",
    )
    assert certification.passed is True
    assert candidate.intake_status == CandidateIntakeStatus.CERTIFYING
    assert candidate.bundle.status == BundleStatus.CERTIFIED
    steward.approve_candidate(candidate.candidate_id)
    rollout = steward.start_rollout(candidate.candidate_id, actor="approver", eligible_task_classes=["read_only"])
    assert rollout.status == RolloutStatus.PENDING
    steward.advance_rollout(rollout.rollout_id, RolloutStatus.SHADOW, sample_count=10)
    steward.rollback(rollout.rollout_id, reason="test rollback")
    assert rollout.status == RolloutStatus.ROLLED_BACK
    with pytest.raises(
        ValueError,
        match="candidate already has rollout history; generate and approve a new immutable candidate",
    ):
        steward.start_rollout(
            candidate.candidate_id,
            actor="approver",
            eligible_task_classes=["read_only"],
        )


def test_certifying_candidate_is_not_activation_eligible_without_passed_certification_and_approval() -> None:
    steward = ExternalWorkerSteward(
        worker_id="openhands-candidate",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/OpenHands/software-agent-sdk",
            exact_release="v1.43.0",
            commit_sha="b" * 40,
            transport_type="openhands_agent_server",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    pending = steward.generate_candidate(
        semantic_version="v1.43.0",
        adapter_version="candidate",
        upstream_compatibility_range="==v1.43.0",
    )
    for status in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(pending.candidate_id, status)

    with pytest.raises(ValueError, match="passed certification"):
        steward.approve_candidate(pending.candidate_id)
    with pytest.raises(ValueError, match="only approved candidates"):
        steward.start_rollout(pending.candidate_id, actor="operator")

    failed = steward.certify_candidate(pending.candidate_id, conformance={"passed": False}, checks={})
    assert failed.passed is False
    assert pending.intake_status == CandidateIntakeStatus.REJECTED
    with pytest.raises(ValueError, match="only approved candidates"):
        steward.start_rollout(pending.candidate_id, actor="operator")

    passed_candidate = steward.generate_candidate(
        semantic_version="v1.43.0+2",
        adapter_version="candidate-2",
        upstream_compatibility_range="==v1.43.0",
    )
    for status in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(passed_candidate.candidate_id, status)
    certification = steward.certify_candidate(passed_candidate.candidate_id, conformance={"passed": True}, checks={})
    assert certification.passed is True
    assert passed_candidate.intake_status == CandidateIntakeStatus.CERTIFYING
    with pytest.raises(ValueError, match="only approved candidates"):
        steward.start_rollout(passed_candidate.candidate_id, actor="operator")
    steward.approve_candidate(passed_candidate.candidate_id)
    assert passed_candidate.intake_status == CandidateIntakeStatus.APPROVED


def test_certification_cannot_be_passed_with_caller_selected_operational_checks() -> None:
    steward = ExternalWorkerSteward(
        worker_id="unverified-worker",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            license_id="MIT",
            redistribution_status="pending",
            security_scan_status="pending",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    for status in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.LICENSE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, status)

    certification = steward.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={},
    )
    assert certification.passed is False
    assert "security" in certification.failures
    assert "license" not in certification.failures
    assert candidate.intake_status == CandidateIntakeStatus.REJECTED


def test_restricted_or_missing_license_metadata_does_not_block_certification() -> None:
    steward = ExternalWorkerSteward(
        worker_id="metadata-only-license-worker",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            license_id="AGPL-3.0",
            redistribution_status="restricted",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    for status in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.LICENSE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, status)

    certification = steward.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={"license": False, "licensing": False},
    )
    assert certification.passed is True
    assert "license" not in certification.checks
    assert "licensing" not in certification.checks


def test_license_metadata_stage_cannot_block_a_candidate() -> None:
    steward = ExternalWorkerSteward(
        worker_id="metadata-only-block-test",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            license_id="AGPL-3.0",
            redistribution_status="restricted",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    steward.advance_candidate(candidate.candidate_id, CandidateIntakeStatus.SOURCE_REVIEW)
    steward.advance_candidate(candidate.candidate_id, CandidateIntakeStatus.LICENSE_REVIEW)

    with pytest.raises(StewardTransitionError, match="invalid candidate transition"):
        steward.advance_candidate(candidate.candidate_id, CandidateIntakeStatus.BLOCKED)

    assert candidate.intake_status == CandidateIntakeStatus.LICENSE_REVIEW


def test_license_metadata_stage_can_be_skipped() -> None:
    """Licence capture is optional and never delays the technical review path."""
    steward = ExternalWorkerSteward(
        worker_id="metadata-stage-skip",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            license_id="AGPL-3.0",
            redistribution_status="restricted",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    steward.advance_candidate(candidate.candidate_id, CandidateIntakeStatus.SOURCE_REVIEW)
    steward.advance_candidate(candidate.candidate_id, CandidateIntakeStatus.SECURITY_REVIEW)

    assert candidate.intake_status == CandidateIntakeStatus.SECURITY_REVIEW


def test_steward_rollback_restores_previous_active_pointers() -> None:
    steward = ExternalWorkerSteward(
        worker_id="rollback-worker",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            license_id="MIT",
            redistribution_status="approved",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")

    def approved_candidate(version: str):
        candidate = steward.generate_candidate(
            semantic_version=version,
            adapter_version=version,
            upstream_compatibility_range=f"=={version}",
        )
        for status in (
            CandidateIntakeStatus.SOURCE_REVIEW,
            CandidateIntakeStatus.LICENSE_REVIEW,
            CandidateIntakeStatus.SECURITY_REVIEW,
            CandidateIntakeStatus.INTERFACE_RESEARCH,
            CandidateIntakeStatus.GENERATED,
            CandidateIntakeStatus.CERTIFYING,
        ):
            steward.advance_candidate(candidate.candidate_id, status)
        steward.certify_candidate(candidate.candidate_id, conformance={"passed": True}, checks={})
        steward.approve_candidate(candidate.candidate_id)
        return candidate

    first = approved_candidate("1.0.0")
    first_rollout = steward.start_rollout(first.candidate_id, actor="test", eligible_task_classes=["read_only"])
    steward.advance_rollout(first_rollout.rollout_id, RolloutStatus.SHADOW, sample_count=10)
    steward.advance_rollout(first_rollout.rollout_id, RolloutStatus.CANARY, sample_count=10)
    steward.advance_rollout(first_rollout.rollout_id, RolloutStatus.PROMOTING, sample_count=5)
    steward.advance_rollout(first_rollout.rollout_id, RolloutStatus.ACTIVE, sample_count=3)
    prior_bundle = steward.active_bundle
    prior_adapter = steward.active_adapter

    second = approved_candidate("2.0.0")
    second_rollout = steward.start_rollout(second.candidate_id, actor="test", eligible_task_classes=["read_only"])
    steward.advance_rollout(second_rollout.rollout_id, RolloutStatus.SHADOW, sample_count=10)
    steward.advance_rollout(second_rollout.rollout_id, RolloutStatus.CANARY, sample_count=10)
    steward.advance_rollout(second_rollout.rollout_id, RolloutStatus.PROMOTING, sample_count=5)
    steward.advance_rollout(second_rollout.rollout_id, RolloutStatus.ACTIVE, sample_count=3)
    assert steward.active_bundle == second.bundle

    steward.rollback(second_rollout.rollout_id, reason="regression")
    assert steward.active_bundle == prior_bundle
    assert steward.active_adapter == prior_adapter


def test_pre_activation_rollback_preserves_active_candidate_and_blocks_regression() -> None:
    steward = ExternalWorkerSteward(
        worker_id="pre-active-rollback-worker",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")

    def approved_candidate(version: str):
        candidate = steward.generate_candidate(
            semantic_version=version,
            adapter_version=version,
            upstream_compatibility_range=f"=={version}",
        )
        for status in (
            CandidateIntakeStatus.SOURCE_REVIEW,
            CandidateIntakeStatus.SECURITY_REVIEW,
            CandidateIntakeStatus.INTERFACE_RESEARCH,
            CandidateIntakeStatus.GENERATED,
            CandidateIntakeStatus.CERTIFYING,
        ):
            steward.advance_candidate(candidate.candidate_id, status)
        steward.certify_candidate(candidate.candidate_id, conformance={"passed": True}, checks={})
        steward.approve_candidate(candidate.candidate_id)
        return candidate

    first = approved_candidate("1.0.0")
    first_rollout = steward.start_rollout(first.candidate_id, actor="test", eligible_task_classes=["read_only"])
    steward.advance_rollout(first_rollout.rollout_id, RolloutStatus.SHADOW, sample_count=10)
    steward.advance_rollout(first_rollout.rollout_id, RolloutStatus.CANARY, sample_count=10)
    steward.advance_rollout(first_rollout.rollout_id, RolloutStatus.PROMOTING, sample_count=5)
    steward.advance_rollout(
        first_rollout.rollout_id,
        RolloutStatus.ACTIVE,
        sample_count=3,
        metrics={"regression_fraction": 0.0},
    )
    baseline_bundle = steward.active_bundle
    baseline_adapter = steward.active_adapter

    second = approved_candidate("2.0.0")
    second_rollout = steward.start_rollout(second.candidate_id, actor="test", eligible_task_classes=["read_only"])
    steward.advance_rollout(second_rollout.rollout_id, RolloutStatus.SHADOW, sample_count=10)
    steward.advance_rollout(second_rollout.rollout_id, RolloutStatus.CANARY, sample_count=10)
    steward.advance_rollout(second_rollout.rollout_id, RolloutStatus.PROMOTING, sample_count=5)
    with pytest.raises(StewardTransitionError, match="exceeds rollback threshold"):
        steward.advance_rollout(
            second_rollout.rollout_id,
            RolloutStatus.ACTIVE,
            sample_count=3,
            metrics={"regression_fraction": 0.5},
        )

    assert second_rollout.status == RolloutStatus.PROMOTING
    steward.rollback(second_rollout.rollout_id, reason="regression blocked before activation")
    assert second_rollout.status == RolloutStatus.ROLLED_BACK
    assert steward.active_bundle == baseline_bundle
    assert steward.active_adapter == baseline_adapter


def test_steward_restores_active_pointers_from_durable_ids() -> None:
    steward = ExternalWorkerSteward(
        worker_id="rehydrated-pointer-worker",
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )

    assert steward.restore_active_pointers(
        bundle_id=str(candidate.bundle.bundle_id),
        adapter_id=str(candidate.adapter.adapter_id),
    ) is True
    assert steward.active_bundle == candidate.bundle
    assert steward.active_adapter == candidate.adapter
    assert steward.provenance == candidate.source_provenance

    steward.active_bundle = None
    steward.active_adapter = None
    prior_provenance = steward.provenance
    assert steward.restore_active_pointers(bundle_id="missing-bundle", adapter_id=None) is False
    assert steward.active_bundle is None
    assert steward.active_adapter is None
    assert steward.provenance == prior_provenance


@pytest.mark.asyncio
async def test_controller_returns_canonical_idempotent_run() -> None:
    canonical_id = uuid4()

    class Storage:
        async def create_worker_run(self, **_kwargs):
            return {"id": canonical_id, "state": "RUNNING"}

        async def get_worker_run(self, run_id):
            return {"id": run_id, "state": "RUNNING"}

    request = WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key="canonical-replay",
        worker_id="worker",
        task_type="test",
    )
    outcome = await WorkerRunController(storage=Storage()).execute(
        request,
        object(),
        worker_registry_id=uuid4(),
    )
    assert outcome.run_id == canonical_id
    assert outcome.state == "RUNNING"


@pytest.mark.asyncio
async def test_controller_forwards_explicit_skill_bundle_pin() -> None:
    bundle_id = uuid4()
    worker_id = uuid4()
    calls: list[dict[str, object]] = []

    class Storage:
        async def create_worker_run(self, **kwargs):
            calls.append(kwargs)
            return {"id": kwargs["run_id"], "state": "CREATED", "skill_bundle_id": bundle_id}

    request = WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key="explicit-bundle-pin",
        worker_id="worker",
        task_type="test",
    )
    row = await WorkerRunController(storage=Storage()).create_run(
        request,
        worker_registry_id=worker_id,
        skill_bundle_id=bundle_id,
    )

    assert row["skill_bundle_id"] == bundle_id
    assert calls[0]["worker_id"] == worker_id
    assert calls[0]["skill_bundle_id"] == bundle_id


@pytest.mark.asyncio
async def test_persistent_event_limit_is_passed_to_storage_boundary() -> None:
    class Storage:
        def __init__(self) -> None:
            self.calls = []

        async def append_worker_event(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) > 1:
                raise ValueError("worker event limit exceeded")
            return kwargs

    storage = Storage()
    controller = WorkerRunController(storage=storage, max_event_count=1)
    run_id = uuid4()
    event = WorkerEvent(
        run_id=run_id,
        worker_id="worker",
        event_type=EventType.PROGRESS,
        progress=WorkerProgress(message="one"),
    )
    await controller.append_event(event)
    with pytest.raises(WorkerRunError) as exc_info:
        await controller.append_event(
            WorkerEvent(
                run_id=run_id,
                worker_id="worker",
                event_type=EventType.PROGRESS,
                progress=WorkerProgress(message="two"),
            )
        )
    assert getattr(exc_info.value, "code", None) == "EVENT_LIMIT"
    assert storage.calls[0]["max_event_count"] == 1


@pytest.mark.asyncio
async def test_process_adapter_drains_large_stderr_without_deadlock() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('x' * 2000000); print('{\"success\": true, \"output\": {\"ok\": true}}')",
    ]
    adapter = ProcessAdapter(command, worker_id="stderr-worker")
    request = WorkerRunRequest(worker_id="stderr-worker", task_type="stderr-test", idempotency_key="stderr-test")
    result = await adapter._execute(request)
    assert isinstance(result, WorkerResult)
    assert result.success is True
    await adapter.close()


def test_oci_adapter_requires_and_applies_hardened_sandbox() -> None:
    with pytest.raises(ValueError, match="sandbox profile"):
        OCIAdapter("example/worker@sha256:" + "a" * 64, worker_id="oci-worker")

    adapter = OCIAdapter(
        "example/worker@sha256:" + "a" * 64,
        worker_id="oci-worker",
        sandbox_profile="gvisor",
    )
    assert "-i" in adapter.command
    assert "--network" in adapter.command and "none" in adapter.command
    assert "--cap-drop" in adapter.command and "ALL" in adapter.command
    assert "--runtime" in adapter.command and "runsc" in adapter.command
