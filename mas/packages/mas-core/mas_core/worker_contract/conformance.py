"""Deterministic conformance checks for certified adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .models import (
    CapabilityRequirement,
    EventType,
    WorkerError,
    WorkerEvent,
    WorkerRunRequest,
    WorkerToolRequest,
    WorkerToolResponse,
)

if TYPE_CHECKING:
    from .adapters import WorkerAdapter


_CONFORMANCE_EXTENSION = "aiat.conformance"
_CONFORMANCE_SCHEMA_VERSION = "aiat.conformance.v1"
_DETERMINISTIC_FILE_CASE_ID = "aiat.deterministic_file_generation.v1"
_DETERMINISTIC_FILE_CAPABILITY = "deterministic.file_generate"
_DETERMINISTIC_FILE_TOOL = "file_write"
_DETERMINISTIC_FILE_PATH = "conformance/aiat-worker-check.txt"
_DETERMINISTIC_FILE_CONTENT = "AIAT worker conformance fixture v1\n"


@dataclass(frozen=True, slots=True)
class _ConformanceCase:
    """A fixed, versioned protocol case selected by a capability snapshot.

    The selector is carried in ``WorkerCapabilities.extensions`` and retained
    with the candidate's immutable capability snapshot. Task payloads and tool
    grants are defined here, rather than accepted from the candidate, so a
    candidate cannot broaden the certification probe.
    """

    case_id: str
    task_type: str
    task_input: dict[str, Any]
    required_capability: str
    tool_grants: tuple[str, ...]
    tool_name: str
    tool_arguments: dict[str, Any]
    project_scoped: bool = False


_CONFORMANCE_CASES: dict[str, _ConformanceCase] = {
    _DETERMINISTIC_FILE_CASE_ID: _ConformanceCase(
        case_id=_DETERMINISTIC_FILE_CASE_ID,
        task_type="deterministic_file_generation",
        task_input={
            "path": _DETERMINISTIC_FILE_PATH,
            "content": _DETERMINISTIC_FILE_CONTENT,
        },
        required_capability=_DETERMINISTIC_FILE_CAPABILITY,
        tool_grants=(_DETERMINISTIC_FILE_TOOL,),
        tool_name=_DETERMINISTIC_FILE_TOOL,
        tool_arguments={
            "path": _DETERMINISTIC_FILE_PATH,
            "content": _DETERMINISTIC_FILE_CONTENT,
        },
        project_scoped=True,
    ),
}


def _declared_conformance_case(adapter: WorkerAdapter) -> _ConformanceCase | None:
    """Resolve an explicitly declared versioned conformance case.

    Legacy adapters without this extension retain the universal echo probe.
    Once the extension is present, every field must match the versioned
    contract; malformed or unknown declarations fail closed.
    """

    capabilities = getattr(adapter, "capabilities", None)
    extensions = getattr(capabilities, "extensions", None)
    if not isinstance(extensions, dict) or _CONFORMANCE_EXTENSION not in extensions:
        return None

    declaration = extensions[_CONFORMANCE_EXTENSION]
    if not isinstance(declaration, dict) or set(declaration) != {"schema_version", "case_id"}:
        raise ValueError("adapter conformance declaration must contain exactly schema_version and case_id")
    if declaration.get("schema_version") != _CONFORMANCE_SCHEMA_VERSION:
        raise ValueError("adapter conformance declaration has an unsupported schema_version")
    case_id = declaration.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("adapter conformance declaration requires a non-blank case_id")
    case = _CONFORMANCE_CASES.get(case_id)
    if case is None:
        raise ValueError("adapter conformance declaration references an unsupported case_id")

    capability_names = getattr(capabilities, "capability_names", ())
    if case.required_capability not in capability_names:
        raise ValueError(
            f"adapter conformance case requires declared capability {case.required_capability!r}"
        )
    return case


def _conformance_tool_response(
    tool_request: WorkerToolRequest | None,
    *,
    request: WorkerRunRequest,
    case: _ConformanceCase,
    accepted_count: int,
) -> tuple[WorkerToolResponse | None, bool]:
    """Return a local protocol-fixture response only for the exact case tool.

    This is deliberately not a Tool Service call and creates no durable tool
    effect. A mismatch receives a bounded denial response and fails the
    conformance result.
    """

    if tool_request is None:
        return None, False
    valid = (
        accepted_count == 0
        and tool_request.run_id == request.run_id
        and tool_request.tool_name == case.tool_name
        and tool_request.tool_name in request.tool_grants
        and tool_request.arguments == case.tool_arguments
        and tool_request.permission_scope == [case.tool_name]
        and not tool_request.approval_required
    )
    if valid:
        return (
            WorkerToolResponse(
                request_id=tool_request.request_id,
                run_id=request.run_id,
                tool_name=tool_request.tool_name,
                success=True,
                result={
                    "success": True,
                    "path": case.tool_arguments["path"],
                    "bytes_written": len(str(case.tool_arguments["content"]).encode("utf-8")),
                    "conformance_fixture": True,
                },
            ),
            True,
        )
    return (
        WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=request.run_id,
            tool_name=tool_request.tool_name,
            success=False,
            error=WorkerError(
                code="CONFORMANCE_TOOL_DENIED",
                message="tool request did not match the bounded conformance case",
                retryable=False,
                terminal=True,
                category="conformance",
            ),
        ),
        False,
    )


@dataclass(frozen=True, slots=True)
class ConformanceTestResult:
    name: str
    passed: bool
    details: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    adapter_type: str
    adapter_api_version: str
    passed: bool
    started_at: datetime
    completed_at: datetime
    tests: tuple[ConformanceTestResult, ...]
    claimed_capabilities: dict[str, Any]

    @property
    def failed_tests(self) -> tuple[ConformanceTestResult, ...]:
        return tuple(test for test in self.tests if not test.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_type": self.adapter_type,
            "adapter_api_version": self.adapter_api_version,
            "passed": self.passed,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "tests": [
                {
                    "name": test.name,
                    "passed": test.passed,
                    "details": test.details,
                    "evidence": test.evidence,
                }
                for test in self.tests
            ],
            "claimed_capabilities": self.claimed_capabilities,
        }


class ConformanceRunner:
    """Run the common adapter contract suite without mutating control-plane state."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        adapter: WorkerAdapter,
        *,
        worker_id: str | None = None,
        task_input: dict[str, Any] | None = None,
        include_cancellation: bool = False,
        resolved_model_profile: Any | None = None,
    ) -> ConformanceReport:
        started = datetime.now(UTC)
        tests: list[ConformanceTestResult] = []
        health = None
        try:
            health = await asyncio.wait_for(adapter.health(), timeout=self.timeout_seconds)
            tests.append(ConformanceTestResult("health", health.healthy, health.status, health.model_dump(mode="json")))
        except Exception as exc:
            tests.append(ConformanceTestResult("health", False, str(exc)))

        case = _declared_conformance_case(adapter)
        if case is not None and task_input is not None and task_input != case.task_input:
            raise ValueError("task_input cannot override an adapter's declared conformance case")

        request = WorkerRunRequest(
            idempotency_key=f"conformance-{uuid4()}",
            worker_id=worker_id or getattr(adapter, "worker_id", "conformance-worker"),
            task_type=case.task_type if case is not None else "conformance.echo",
            task_input=(
                dict(case.task_input)
                if case is not None
                else task_input or {"probe": "aiat-contract"}
            ),
            project_id=uuid4() if case is not None and case.project_scoped else None,
            capability_requirements=(
                [CapabilityRequirement(name=case.required_capability)]
                if case is not None
                else []
            ),
            tool_grants=list(case.tool_grants) if case is not None else [],
            resolved_model_profile=resolved_model_profile,
        )
        try:
            readiness = await asyncio.wait_for(adapter.readiness(request), timeout=self.timeout_seconds)
            tests.append(ConformanceTestResult("readiness", readiness.ready, "; ".join(readiness.blockers), readiness.model_dump(mode="json")))
        except Exception as exc:
            tests.append(ConformanceTestResult("readiness", False, str(exc)))

        events: list[WorkerEvent] = []
        conformance_tool_requests = 0
        conformance_tool_responses = 0
        conformance_tool_protocol_passed = True
        try:
            accepted = await asyncio.wait_for(adapter.start(request), timeout=self.timeout_seconds)
            tests.append(ConformanceTestResult("task_acceptance", accepted.run_id == request.run_id, evidence=accepted.model_dump(mode="json")))

            async def collect() -> None:
                nonlocal conformance_tool_requests
                nonlocal conformance_tool_responses
                nonlocal conformance_tool_protocol_passed
                async for event in adapter.events(request.run_id):
                    events.append(event)
                    if event.event_type != EventType.TOOL_REQUEST:
                        continue
                    prior_requests = conformance_tool_requests
                    conformance_tool_requests += 1
                    if case is None:
                        conformance_tool_protocol_passed = False
                        tool_request = event.tool_request
                        if tool_request is not None:
                            await adapter.deliver_tool_response(
                                WorkerToolResponse(
                                    request_id=tool_request.request_id,
                                    run_id=request.run_id,
                                    tool_name=tool_request.tool_name,
                                    success=False,
                                    error=WorkerError(
                                        code="CONFORMANCE_TOOL_DENIED",
                                        message="no tool grant is available for the universal conformance probe",
                                        retryable=False,
                                        terminal=True,
                                        category="conformance",
                                    ),
                                )
                            )
                            conformance_tool_responses += 1
                        continue
                    response, valid = _conformance_tool_response(
                        event.tool_request,
                        request=request,
                        case=case,
                        accepted_count=prior_requests,
                    )
                    conformance_tool_protocol_passed = conformance_tool_protocol_passed and valid
                    if response is not None:
                        await adapter.deliver_tool_response(response)
                        conformance_tool_responses += 1

            await asyncio.wait_for(collect(), timeout=self.timeout_seconds)
            tests.append(ConformanceTestResult("event_stream", bool(events), evidence={"count": len(events)}))
            sequences = [event.sequence for event in events]
            tests.append(ConformanceTestResult(
                "event_ordering",
                sequences == list(range(len(sequences))),
                evidence={"sequences": sequences},
            ))
            terminal = {EventType.RESULT, EventType.ERROR, EventType.CANCELLED}
            terminal_events = [event for event in events if event.event_type in terminal]
            tests.append(ConformanceTestResult(
                "normalized_terminal_result",
                len(terminal_events) == 1 and (
                    terminal_events[0].result is not None or terminal_events[0].error is not None
                ),
                evidence={"terminal_events": [event.model_dump(mode="json") for event in terminal_events]},
            ))
            if case is not None:
                tests.append(ConformanceTestResult(
                    "conformance_case_result",
                    len(terminal_events) == 1
                    and terminal_events[0].result is not None
                    and terminal_events[0].result.success,
                    details="versioned conformance task completed successfully",
                    evidence={"case_id": case.case_id},
                ))
                tests.append(ConformanceTestResult(
                    "conformance_tool_protocol",
                    conformance_tool_requests == 1
                    and conformance_tool_responses == 1
                    and conformance_tool_protocol_passed,
                    details=(
                        "exact granted tool request received a local protocol fixture response"
                        if conformance_tool_protocol_passed
                        else "tool request did not match the bounded conformance contract"
                    ),
                    evidence={
                        "case_id": case.case_id,
                        "request_count": conformance_tool_requests,
                        "response_count": conformance_tool_responses,
                        "response_source": "local_conformance_fixture_not_tool_service",
                    },
                ))
            elif conformance_tool_requests:
                tests.append(ConformanceTestResult(
                    "unexpected_conformance_tool_request",
                    False,
                    details="universal echo conformance has no tool grant",
                    evidence={
                        "request_count": conformance_tool_requests,
                        "denied_count": conformance_tool_responses,
                    },
                ))
        except Exception as exc:
            tests.append(ConformanceTestResult("task_execution", False, str(exc), {"events": len(events)}))

        if include_cancellation:
            # This is a capability assertion, not a claim that every runtime
            # supports forced termination.
            supports = getattr(adapter.capabilities, "cancellation_mode", None) is not None
            tests.append(ConformanceTestResult("cancellation_declaration", supports, evidence={"mode": str(getattr(adapter.capabilities, "cancellation_mode", None))}))

        completed = datetime.now(UTC)
        return ConformanceReport(
            adapter_type=getattr(adapter, "runtime_type", type(adapter).__name__),
            adapter_api_version=getattr(adapter, "adapter_api_version", "unknown"),
            passed=all(test.passed for test in tests),
            started_at=started,
            completed_at=completed,
            tests=tuple(tests),
            claimed_capabilities=getattr(adapter, "capabilities", {}).model_dump(mode="json") if hasattr(getattr(adapter, "capabilities", None), "model_dump") else {},
        )
