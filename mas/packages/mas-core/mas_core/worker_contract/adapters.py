"""Adapter SDK for the universal worker contract.

Adapters translate a runtime into contract events. They do not write flow or
worker-run state; callers consume the event stream and let the controller make
authoritative transitions.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from .models import (
    ADAPTER_API_VERSION,
    CONTRACT_VERSION,
    EventType,
    ProtocolVersion,
    WorkerAuditEvent,
    WorkerCapabilities,
    WorkerCancellation,
    WorkerError,
    WorkerEvent,
    WorkerHealth,
    WorkerPause,
    WorkerReadiness,
    WorkerResult,
    WorkerResume,
    WorkerRunAccepted,
    WorkerRunRequest,
    WorkerToolResponse,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AdapterContext:
    """Dependencies an adapter may use after AIAT has authorized a run."""

    tool_dispatcher: Callable[[Any], Awaitable[Any]] | None = None
    artifact_registrar: Callable[[Any], Awaitable[Any]] | None = None
    audit_sink: Callable[[WorkerAuditEvent], Awaitable[None]] | None = None
    workspace_path: str | None = None
    secrets: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class WorkerAdapter(Protocol):
    """Runtime-neutral adapter surface consumed by WorkerRunController."""

    adapter_api_version: str
    runtime_type: str
    capabilities: WorkerCapabilities

    async def health(self) -> WorkerHealth: ...

    async def readiness(self, request: WorkerRunRequest | None = None) -> WorkerReadiness: ...

    async def start(self, request: WorkerRunRequest) -> WorkerRunAccepted: ...

    def events(self, run_id: UUID) -> AsyncIterator[WorkerEvent]: ...

    async def cancel(self, request: WorkerCancellation) -> None: ...

    async def pause(self, request: WorkerPause) -> None: ...

    async def resume(self, request: WorkerResume) -> None: ...

    async def deliver_tool_response(self, response: WorkerToolResponse) -> None: ...

    async def close(self) -> None: ...


class BaseWorkerAdapter:
    """Common idempotency, event ordering, and health behavior."""

    adapter_api_version = ADAPTER_API_VERSION
    runtime_type = "unknown"

    def __init__(
        self,
        *,
        worker_id: str,
        capabilities: WorkerCapabilities | None = None,
        context: AdapterContext | None = None,
        runtime_version: str | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.capabilities = capabilities or WorkerCapabilities()
        self.context = context or AdapterContext()
        self.runtime_version = runtime_version
        self._queues: dict[UUID, asyncio.Queue[WorkerEvent | None]] = defaultdict(asyncio.Queue)
        self._sequence: dict[UUID, int] = defaultdict(int)
        self._accepted_by_key: dict[str, WorkerRunAccepted] = {}
        self._active_tasks: dict[UUID, asyncio.Task[Any]] = {}
        self._cancel_requested: set[UUID] = set()
        self._stream_closed: set[UUID] = set()
        self._closed = False

    def _protocol(self) -> ProtocolVersion:
        return ProtocolVersion(
            contract_version=CONTRACT_VERSION,
            adapter_api_version=self.adapter_api_version,
            runtime_api_version=self.runtime_version,
        )

    async def health(self) -> WorkerHealth:
        return WorkerHealth(
            worker_id=self.worker_id,
            healthy=not self._closed,
            status="closed" if self._closed else "healthy",
            runtime_version=self.runtime_version,
            adapter_version=self.adapter_api_version,
        )

    async def readiness(self, request: WorkerRunRequest | None = None) -> WorkerReadiness:
        blockers: list[str] = []
        if self._closed:
            blockers.append("adapter is closed")
        if request is not None:
            offered = self.capabilities.capability_names
            required = {item.name for item in request.capability_requirements if item.required}
            missing = sorted(required - set(offered))
            blockers.extend(f"missing capability: {name}" for name in missing)
        return WorkerReadiness(
            worker_id=self.worker_id,
            ready=not blockers,
            checks={"adapter_open": not self._closed},
            blockers=blockers,
        )

    async def start(self, request: WorkerRunRequest) -> WorkerRunAccepted:
        if self._closed:
            raise RuntimeError("adapter is closed")
        existing = self._accepted_by_key.get(request.idempotency_key)
        if existing is not None:
            return existing
        readiness = await self.readiness(request)
        if not readiness.ready:
            raise RuntimeError("; ".join(readiness.blockers))
        accepted = WorkerRunAccepted(
            protocol=self._protocol(),
            run_id=request.run_id,
            idempotency_key=request.idempotency_key,
            worker_id=request.worker_id,
            runtime_run_id=self._acceptance_runtime_run_id(request),
            negotiated_capabilities=self.capabilities,
            metadata=self._acceptance_metadata(request),
        )
        self._accepted_by_key[request.idempotency_key] = accepted
        await self._emit(WorkerEvent(
            protocol=self._protocol(),
            run_id=request.run_id,
            worker_id=self.worker_id,
            event_type=EventType.ACCEPTED,
            idempotency_key=request.idempotency_key,
        ))
        task = asyncio.create_task(self._run_and_emit(request), name=f"worker-run-{request.run_id}")
        self._active_tasks[request.run_id] = task
        return accepted

    def _acceptance_runtime_run_id(self, request: WorkerRunRequest) -> str | None:
        """Return the runtime-owned identifier that is safe to expose at accept."""

        return None

    def _acceptance_metadata(self, request: WorkerRunRequest) -> dict[str, Any]:
        """Return immutable adapter metadata attached to the accept event."""

        return {}

    async def _run_and_emit(self, request: WorkerRunRequest) -> None:
        try:
            result = await self._execute(request)
            if not isinstance(result, WorkerResult):
                result = self._coerce_result(request, result)
            if not result.success and result.error is not None and result.error.code == "CANCELLED":
                await self._emit(WorkerEvent(
                    protocol=self._protocol(),
                    run_id=request.run_id,
                    worker_id=self.worker_id,
                    event_type=EventType.CANCELLED,
                    error=result.error,
                ))
                return
            await self._emit(WorkerEvent(
                protocol=self._protocol(),
                run_id=request.run_id,
                worker_id=self.worker_id,
                event_type=EventType.RESULT if result.success else EventType.ERROR,
                result=result if result.success else None,
                error=result.error if not result.success else None,
                usage=result.usage,
            ))
        except asyncio.CancelledError:
            error = WorkerError(
                code="CANCELLED",
                message="adapter task was forcefully cancelled",
                retryable=True,
                category="cancellation",
            )
            await self._emit(WorkerEvent(
                protocol=self._protocol(),
                run_id=request.run_id,
                worker_id=self.worker_id,
                event_type=EventType.CANCELLED,
                error=error,
            ))
        except Exception as exc:  # adapters must normalize runtime failures
            logger.exception("Worker adapter execution failed for %s", self.worker_id)
            error = WorkerError(
                code="RUNTIME_ERROR",
                message=str(exc),
                retryable=True,
                category="runtime",
                cause_type=type(exc).__name__,
            )
            await self._emit(WorkerEvent(
                protocol=self._protocol(),
                run_id=request.run_id,
                worker_id=self.worker_id,
                event_type=EventType.ERROR,
                error=error,
            ))
        finally:
            self._active_tasks.pop(request.run_id, None)
            await self._close_queue(request.run_id)

    async def _execute(self, request: WorkerRunRequest) -> WorkerResult | Any:
        raise NotImplementedError

    def _coerce_result(self, request: WorkerRunRequest, result: Any) -> WorkerResult:
        if isinstance(result, dict) and "success" in result:
            return WorkerResult.model_validate({"run_id": request.run_id, "worker_id": self.worker_id, **result})
        return WorkerResult(
            run_id=request.run_id,
            worker_id=self.worker_id,
            success=True,
            output=result,
        )

    async def _emit(self, event: WorkerEvent) -> None:
        sequence = self._sequence[event.run_id]
        self._sequence[event.run_id] = sequence + 1
        event.sequence = sequence
        await self._queues[event.run_id].put(event)

    async def _close_queue(self, run_id: UUID) -> None:
        self._stream_closed.add(run_id)
        await self._queues[run_id].put(None)

    async def emit_progress(self, run_id: UUID, message: str, *, percent: float | None = None, phase: str | None = None) -> None:
        await self._emit(WorkerEvent(
            protocol=self._protocol(),
            run_id=run_id,
            worker_id=self.worker_id,
            event_type=EventType.PROGRESS,
            progress={"message": message, "percent": percent, "phase": phase},
        ))

    async def emit_audit(self, run_id: UUID, action: str, *, actor: str = "adapter", details: dict[str, Any] | None = None) -> None:
        audit = WorkerAuditEvent(run_id=run_id, worker_id=self.worker_id, action=action, actor=actor, details=details or {})
        await self._emit(WorkerEvent(
            protocol=self._protocol(),
            run_id=run_id,
            worker_id=self.worker_id,
            event_type=EventType.AUDIT,
            audit=audit,
        ))
        if self.context.audit_sink is not None:
            await self.context.audit_sink(audit)

    async def events(self, run_id: UUID) -> AsyncIterator[WorkerEvent]:
        queue = self._queues[run_id]
        while True:
            event = await queue.get()
            if event is None:
                # A controller may be finishing an AIAT-mediated tool
                # response while the runtime task exits. Drain any response
                # queued in that hand-off before declaring the stream closed.
                await asyncio.sleep(0)
                if queue.empty():
                    break
                continue
            yield event
            if run_id in self._stream_closed and queue.empty():
                break

    async def cancel(self, request: WorkerCancellation) -> None:
        self._cancel_requested.add(request.run_id)
        task = self._active_tasks.get(request.run_id)
        if request.force and task is not None:
            task.cancel()
        else:
            await self._emit(WorkerEvent(
                protocol=self._protocol(),
                run_id=request.run_id,
                worker_id=self.worker_id,
                event_type=EventType.CANCEL_REQUESTED,
                error=WorkerError(code="CANCEL_REQUESTED", message=request.reason, category="cancellation"),
            ))

    async def pause(self, request: WorkerPause) -> None:
        await self._emit(WorkerEvent(
            protocol=self._protocol(),
            run_id=request.run_id,
            worker_id=self.worker_id,
            event_type=EventType.PAUSED,
            extensions={"reason": request.reason, "requested_by": request.requested_by},
        ))

    async def resume(self, request: WorkerResume) -> None:
        await self._emit(WorkerEvent(
            protocol=self._protocol(),
            run_id=request.run_id,
            worker_id=self.worker_id,
            event_type=EventType.RESUMED,
            extensions={"checkpoint_id": str(request.checkpoint_id) if request.checkpoint_id else None},
        ))

    async def deliver_tool_response(self, response: WorkerToolResponse) -> None:
        """Persist a mediated tool response into the normalized event stream.

        Runtime-specific adapters can override this hook to resume a blocked
        native/MCP session.  The base implementation deliberately exposes the
        response only as a contract event rather than granting direct tool
        access to a worker.
        """
        await self._emit(WorkerEvent(
            protocol=self._protocol(),
            run_id=response.run_id,
            worker_id=self.worker_id,
            event_type=EventType.TOOL_RESPONSE,
            tool_response=response,
        ))

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._active_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class NativeWorkerAdapter(BaseWorkerAdapter):
    """Adapter for an AIAT-owned callable worker.

    The callable may return a result, a JSON-compatible value, or an async
    iterator of contract events followed by a result. Tool dispatch remains an
    injected AIAT service and is never supplied as raw runtime credentials.
    """

    runtime_type = "native"

    def __init__(
        self,
        worker: Callable[[WorkerRunRequest, "NativeWorkerAdapter"], Awaitable[Any] | Any],
        *,
        worker_id: str,
        capabilities: WorkerCapabilities | None = None,
        context: AdapterContext | None = None,
        runtime_version: str | None = None,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            capabilities=capabilities or WorkerCapabilities(
                checkpoint_mode="wrapper",
                cancellation_mode="cooperative",
                streaming_mode="event_stream",
                tool_mode="aiat_mediated",
                model_mode="aiat_gateway",
            ),
            context=context,
            runtime_version=runtime_version,
        )
        self._worker = worker

    async def _execute(self, request: WorkerRunRequest) -> Any:
        result = self._worker(request, self)
        if inspect.isawaitable(result):
            result = await result
        if hasattr(result, "__aiter__"):
            final: Any = None
            async for item in result:
                if isinstance(item, WorkerEvent):
                    await self._emit(item)
                    if item.result is not None:
                        final = item.result
                else:
                    final = item
            return final
        return result
