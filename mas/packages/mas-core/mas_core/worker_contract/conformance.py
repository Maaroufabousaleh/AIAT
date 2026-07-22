"""Deterministic conformance checks for certified adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .adapters import WorkerAdapter
from .models import (
    EventType,
    WorkerCancellation,
    WorkerEvent,
    WorkerRunRequest,
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

        request = WorkerRunRequest(
            idempotency_key=f"conformance-{uuid4()}",
            worker_id=worker_id or getattr(adapter, "worker_id", "conformance-worker"),
            task_type="conformance.echo",
            task_input=task_input or {"probe": "aiat-contract"},
            resolved_model_profile=resolved_model_profile,
        )
        try:
            readiness = await asyncio.wait_for(adapter.readiness(request), timeout=self.timeout_seconds)
            tests.append(ConformanceTestResult("readiness", readiness.ready, "; ".join(readiness.blockers), readiness.model_dump(mode="json")))
        except Exception as exc:
            tests.append(ConformanceTestResult("readiness", False, str(exc)))

        events: list[WorkerEvent] = []
        try:
            accepted = await asyncio.wait_for(adapter.start(request), timeout=self.timeout_seconds)
            tests.append(ConformanceTestResult("task_acceptance", accepted.run_id == request.run_id, evidence=accepted.model_dump(mode="json")))
            async def collect() -> None:
                async for event in adapter.events(request.run_id):
                    events.append(event)
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
