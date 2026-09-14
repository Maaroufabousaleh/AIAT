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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from uuid import UUID

from .models import (
    ADAPTER_API_VERSION,
    CONTRACT_VERSION,
    EventType,
    ProtocolVersion,
    WorkerAuditEvent,
    WorkerCancellation,
    WorkerCancellationReceipt,
    WorkerCapabilities,
    WorkerError,
    WorkerEvent,
    WorkerHealth,
    WorkerPause,
    WorkerReadiness,
    WorkerResult,
    WorkerResume,
    WorkerRunAccepted,
    WorkerRunRequest,
    WorkerRuntimeStatus,
    WorkerToolResponse,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AdapterContext:
    """Dependencies an adapter may use after AIAT has authorized a run.

    ``secrets`` is retained as a compatibility channel for runtime-specific
    bootstrap credentials.  It is deliberately hidden from the dataclass
    representation and must not be copied into a child process environment.
    New integrations should pass opaque AIAT-owned lease/reference IDs through
    ``secret_refs`` and resolve them only at the governed service boundary.
    """

    tool_dispatcher: Callable[[Any], Awaitable[Any]] | None = None
    artifact_registrar: Callable[[Any], Awaitable[Any]] | None = None
    audit_sink: Callable[[WorkerAuditEvent], Awaitable[None]] | None = None
    workspace_path: str | None = None
    secrets: dict[str, str] = field(default_factory=dict, repr=False)
    secret_refs: dict[str, str] = field(default_factory=dict)
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

    async def cancel(self, request: WorkerCancellation) -> WorkerCancellationReceipt | None: ...

    async def reconcile(
        self,
        run_id: UUID,
        *,
        runtime_run_id: str | None = None,
    ) -> WorkerRuntimeStatus: ...

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
        self._terminal_status: dict[UUID, str] = {}
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
                self._terminal_status[request.run_id] = "CANCELLED"
                await self._emit(WorkerEvent(
                    protocol=self._protocol(),
                    run_id=request.run_id,
                    worker_id=self.worker_id,
                    event_type=EventType.CANCELLED,
                    error=result.error,
                ))
                return
            self._terminal_status[request.run_id] = "SUCCEEDED" if result.success else "FAILED"
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
            self._terminal_status[request.run_id] = "CANCELLED"
            error = WorkerError(
                code="CANCELLED",
                message="adapter task was forcefully cancelled",
                retryable=True,
                terminal=True,
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
            self._terminal_status[request.run_id] = "FAILED"
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

    async def cancel(self, request: WorkerCancellation) -> WorkerCancellationReceipt:
        terminal_status = self._terminal_status.get(request.run_id)
        if terminal_status is not None:
            return WorkerCancellationReceipt(
                run_id=request.run_id,
                accepted=False,
                terminal=True,
                runtime_status=terminal_status,
                details={"reason": "runtime already terminal"},
            )
        self._cancel_requested.add(request.run_id)
        task = self._active_tasks.get(request.run_id)
        if task is None:
            accepted = any(item.run_id == request.run_id for item in self._accepted_by_key.values())
            return WorkerCancellationReceipt(
                run_id=request.run_id,
                accepted=accepted,
                terminal=False,
                runtime_status="UNKNOWN",
                details={"reason": "runtime task is not present in this adapter process"},
            )
        if request.force and task is not None:
            # Give a freshly-created task one scheduling turn before forcing
            # cancellation.  Without this yield asyncio may cancel the task
            # before ``_run_and_emit`` starts, which skips its cancellation
            # handler/finally block and leaves the event stream open.
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._active_tasks.pop(request.run_id, None)
            if request.run_id not in self._terminal_status:
                # A task cancelled before its coroutine ever entered still
                # needs a terminal contract event for the controller and
                # stream consumer.  This is a subordinate runtime event; the
                # controller remains responsible for canonical state.
                self._terminal_status[request.run_id] = "CANCELLED"
                await self._emit(WorkerEvent(
                    protocol=self._protocol(),
                    run_id=request.run_id,
                    worker_id=self.worker_id,
                    event_type=EventType.CANCELLED,
                    error=WorkerError(
                        code="CANCELLED",
                        message="adapter task was forcefully cancelled before it started",
                        retryable=True,
                        terminal=True,
                        category="cancellation",
                    ),
                ))
                await self._close_queue(request.run_id)
            terminal_status = self._terminal_status.get(request.run_id, "CANCELLED")
            return WorkerCancellationReceipt(
                run_id=request.run_id,
                accepted=True,
                terminal=True,
                runtime_status=terminal_status,
                details={"force": True},
            )
        else:
            await self._emit(WorkerEvent(
                protocol=self._protocol(),
                run_id=request.run_id,
                worker_id=self.worker_id,
                event_type=EventType.CANCEL_REQUESTED,
                error=WorkerError(code="CANCEL_REQUESTED", message=request.reason, category="cancellation"),
            ))
        return WorkerCancellationReceipt(
            run_id=request.run_id,
            accepted=True,
            terminal=False,
            runtime_status="CANCELLATION_REQUESTED",
            details={"force": request.force},
        )

    async def reconcile(
        self,
        run_id: UUID,
        *,
        runtime_run_id: str | None = None,
    ) -> WorkerRuntimeStatus:
        """Report local runtime knowledge without changing AIAT state.

        The base adapter intentionally cannot recover work from a fresh
        process: its acceptance/task maps are process-local.  Returning
        ``UNKNOWN`` in that case makes the limitation explicit and gives
        external adapters a stable hook for a durable runtime lookup.
        """

        terminal_status = self._terminal_status.get(run_id)
        if terminal_status is not None:
            return WorkerRuntimeStatus(
                run_id=run_id,
                status=terminal_status,
                terminal=True,
                runtime_run_id=runtime_run_id,
            )
        task = self._active_tasks.get(run_id)
        if task is not None and not task.done():
            status = "CANCELLATION_REQUESTED" if run_id in self._cancel_requested else "RUNNING"
            accepted = next(
                (item for item in self._accepted_by_key.values() if item.run_id == run_id),
                None,
            )
            return WorkerRuntimeStatus(
                run_id=run_id,
                status=status,
                runtime_run_id=runtime_run_id or (accepted.runtime_run_id if accepted else None),
            )
        if run_id in self._stream_closed:
            return WorkerRuntimeStatus(
                run_id=run_id,
                status="UNKNOWN",
                terminal=False,
                runtime_run_id=runtime_run_id,
                details={"reason": "stream closed without retained terminal status"},
            )
        accepted = next(
            (item for item in self._accepted_by_key.values() if item.run_id == run_id),
            None,
        )
        if accepted is not None:
            return WorkerRuntimeStatus(
                run_id=run_id,
                status="ACCEPTED",
                runtime_run_id=runtime_run_id or accepted.runtime_run_id,
            )
        return WorkerRuntimeStatus(
            run_id=run_id,
            status="UNKNOWN",
            runtime_run_id=runtime_run_id,
            details={"reason": "run is not known to this adapter process"},
        )

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
        worker: Callable[[WorkerRunRequest, NativeWorkerAdapter], Awaitable[Any] | Any],
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
