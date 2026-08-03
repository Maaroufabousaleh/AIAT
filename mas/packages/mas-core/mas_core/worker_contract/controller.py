"""Sole authoritative writer for durable worker-run lifecycle state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .adapters import BaseWorkerAdapter, WorkerAdapter
from .models import (
    CheckpointMode,
    EventType,
    WorkerCancellation,
    WorkerEvent,
    WorkerPause,
    WorkerReadiness,
    WorkerResult,
    WorkerResume,
    WorkerRunAccepted,
    WorkerRunRequest,
    WorkerToolResponse,
)
from .protocol import ProtocolNegotiationError, negotiate_protocol

logger = logging.getLogger(__name__)


TERMINAL_RUN_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"})
RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"QUEUED", "VALIDATING", "FAILED", "CANCELLED"}),
    "QUEUED": frozenset({"VALIDATING", "FAILED", "CANCELLED"}),
    "CLAIMED": frozenset({"VALIDATING", "QUEUED", "FAILED", "CANCELLED"}),
    "VALIDATING": frozenset({"READY", "FAILED", "CANCELLED"}),
    "READY": frozenset({"DISPATCHING", "FAILED", "CANCELLED"}),
    "DISPATCHING": frozenset({"RUNNING", "FAILED", "CANCELLED", "TIMED_OUT"}),
    "RUNNING": frozenset({"PAUSING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}),
    # The adapter may finish after a pause request has atomically claimed the
    # run but before it has acknowledged the pause.  A terminal result is
    # authoritative in that race and must not be discarded.
    "PAUSING": frozenset({"PAUSED", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}),
    "PAUSED": frozenset({"RESUMING", "FAILED", "CANCELLED"}),
    "RESUMING": frozenset({"RUNNING", "FAILED", "CANCELLED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "TIMED_OUT": frozenset(),
}


class WorkerRunError(RuntimeError):
    """A normalized failure while dispatching or consuming a worker run."""

    def __init__(self, code: str, message: str, *, state: str = "FAILED", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.state = state
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class WorkerRunOutcome:
    run_id: UUID
    state: str
    accepted: WorkerRunAccepted | None = None
    result: WorkerResult | None = None
    events: tuple[WorkerEvent, ...] = ()
    readiness: WorkerReadiness | None = None
    negotiation: dict[str, Any] | None = None


class WorkerRunController:
    """Coordinate adapters and persist run state through compare-and-set APIs."""

    def __init__(self, *, storage: Any | None = None, max_event_count: int = 10_000) -> None:
        self.storage = storage
        self.max_event_count = max_event_count
        self._memory_runs: dict[UUID, dict[str, Any]] = {}
        self._memory_events: dict[UUID, list[dict[str, Any]]] = {}

    async def create_run(
        self,
        request: WorkerRunRequest,
        *,
        worker_registry_id: UUID | None = None,
        worker_shell_version_id: UUID | None = None,
        adapter_id: UUID | None = None,
        steward_id: UUID | None = None,
        model_resolution_snapshot_id: UUID | None = None,
    ) -> dict[str, Any]:
        if self.storage is not None and worker_registry_id is not None:
            return await self.storage.create_worker_run(
                run_id=request.run_id,
                worker_id=worker_registry_id,
                idempotency_key=request.idempotency_key,
                task_type=request.task_type,
                request=request.model_dump(mode="json"),
                project_id=request.project_id,
                flow_id=request.flow_id,
                flow_instance_id=request.flow_instance_id,
                flow_node_execution_id=request.flow_node_execution_id,
                worker_shell_version_id=worker_shell_version_id,
                adapter_id=adapter_id,
                steward_id=steward_id,
                model_resolution_snapshot_id=model_resolution_snapshot_id,
            )
        existing = next((row for row in self._memory_runs.values() if row["idempotency_key"] == request.idempotency_key and row["worker_id"] == request.worker_id), None)
        if existing:
            return existing
        row = {
            "id": request.run_id,
            "worker_id": request.worker_id,
            "idempotency_key": request.idempotency_key,
            "task_type": request.task_type,
            "state": "CREATED",
            "request_json": request.model_dump(mode="json"),
            "project_id": request.project_id,
            "created_at": datetime.now(UTC),
        }
        self._memory_runs[request.run_id] = row
        self._memory_events[request.run_id] = []
        return row

    async def get_run(self, run_id: UUID) -> dict[str, Any] | None:
        if self.storage is not None:
            return await self.storage.get_worker_run(run_id)
        return self._memory_runs.get(run_id)

    async def transition(
        self,
        run_id: UUID,
        target: str,
        *,
        expected: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        negotiation: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        actor: str = "worker-run-controller",
        reason: str | None = None,
        correlation_id: str | None = None,
        transition_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if self.storage is not None:
            return await self.storage.transition_worker_run(
                run_id,
                new_state=target,
                expected_state=expected,
                result=result,
                error=error,
                negotiation=negotiation,
                replay_metadata=replay_metadata,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
                transition_metadata=transition_metadata,
            )
        row = self._memory_runs.get(run_id)
        if row is None:
            return None
        current = str(row["state"])
        if expected is not None and current != expected:
            return None
        if target not in RUN_TRANSITIONS.get(current, frozenset()):
            raise WorkerRunError("INVALID_RUN_TRANSITION", f"invalid worker run transition {current} -> {target}")
        row["state"] = target
        if result is not None:
            row["result_json"] = result
        if error is not None:
            row["error_json"] = error
        if negotiation is not None:
            row["negotiation_json"] = negotiation
        if replay_metadata is not None:
            row["replay_metadata"] = replay_metadata
        if target == "RUNNING":
            row.setdefault("started_at", datetime.now(UTC))
        if target in TERMINAL_RUN_STATES:
            row["completed_at"] = datetime.now(UTC)
        return row

    async def _persist_result_evidence(self, request: WorkerRunRequest, result: WorkerResult) -> None:
        """Persist every returned artifact and usage record before terminal state.

        A worker result is only authoritative once its evidence is queryable.
        The canonical artifact table keeps existing retention behaviour; the
        worker-specific link preserves the run-local kind, checksum, and URI.
        """
        if self.storage is None:
            return
        for artifact in result.artifacts:
            metadata = {
                **artifact.metadata,
                "artifact_name": artifact.name,
                "artifact_kind": artifact.kind.value,
                "mime_type": artifact.mime_type,
                "retention_class": artifact.retention_class,
                "worker_run_id": str(request.run_id),
            }
            stored = await self.storage.create_artifact(
                agent_id=request.worker_id,
                path=artifact.uri,
                metadata=metadata,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
            )
            artifact_id = stored.get("id")
            if not isinstance(artifact_id, int):
                raise WorkerRunError(
                    "ARTIFACT_PERSISTENCE_FAILED",
                    "artifact storage did not return a canonical artifact ID",
                )
            await self.storage.create_worker_artifact(
                run_id=request.run_id,
                artifact_id=artifact_id,
                kind=artifact.kind.value,
                uri=artifact.uri,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                metadata=metadata,
            )
        usage = result.usage
        await self.storage.create_worker_usage(
            run_id=request.run_id,
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "cost_usd": usage.cost_usd,
                "duration_ms": usage.duration_ms,
                "resource_json": {
                    "cpu_seconds": usage.cpu_seconds,
                    "memory_bytes": usage.memory_bytes,
                },
                "provider_id": usage.provider,
                "exact_model_id": usage.exact_model_id,
            },
        )
        # The worker-specific record preserves the exact runtime payload;
        # project_usage_events is the cross-tool/LLM accounting surface used
        # by budgets and dashboards.  Keep this best-effort for lightweight
        # storage doubles, but make the durable AgentStorage path idempotent.
        project_id = request.project_id
        record_usage = getattr(self.storage, "record_project_usage", None)
        if project_id is not None and callable(record_usage):
            worker_id: UUID | None = None
            try:
                worker_id = UUID(str(request.worker_id))
            except (TypeError, ValueError):
                pass
            try:
                await record_usage(
                    project_id=project_id,
                    event_type="llm",
                    run_id=request.run_id,
                    worker_id=worker_id,
                    model=usage.exact_model_id,
                    provider_id=usage.provider,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cost_usd=usage.cost_usd,
                    duration_ms=usage.duration_ms,
                    resource_json={
                        "cpu_seconds": usage.cpu_seconds,
                        "memory_bytes": usage.memory_bytes,
                    },
                    idempotency_key=f"worker-run:{request.run_id}:usage",
                    details={"source": "worker_result", "runtime_worker_id": request.worker_id},
                )
            except Exception:
                # The worker-specific usage record above remains authoritative
                # if a legacy storage double or a telemetry-only backend does
                # not yet expose the project ledger columns.
                logger.exception("project_usage_ledger_write_failed", extra={"run_id": str(request.run_id)})

    async def _mediate_tool_request(
        self,
        request: WorkerRunRequest,
        adapter: WorkerAdapter,
        event: WorkerEvent,
    ) -> None:
        if event.tool_request is None:
            return
        tool_request = event.tool_request
        if tool_request.tool_name not in request.tool_grants:
            response = WorkerToolResponse(
                request_id=tool_request.request_id,
                run_id=request.run_id,
                tool_name=tool_request.tool_name,
                success=False,
                error={
                    "code": "TOOL_NOT_GRANTED",
                    "message": f"Tool {tool_request.tool_name!r} is not granted to this Worker Run",
                    "category": "policy",
                },
            )
        else:
            dispatcher = getattr(getattr(adapter, "context", None), "tool_dispatcher", None)
            if dispatcher is None:
                response = WorkerToolResponse(
                    request_id=tool_request.request_id,
                    run_id=request.run_id,
                    tool_name=tool_request.tool_name,
                    success=False,
                    error={
                        "code": "TOOL_MEDIATOR_UNAVAILABLE",
                        "message": "No AIAT tool mediator is configured for this worker",
                        "category": "policy",
                    },
                )
            else:
                try:
                    raw_response = await dispatcher(tool_request)
                    response = raw_response if isinstance(raw_response, WorkerToolResponse) else WorkerToolResponse.model_validate(raw_response)
                except Exception as exc:
                    response = WorkerToolResponse(
                        request_id=tool_request.request_id,
                        run_id=request.run_id,
                        tool_name=tool_request.tool_name,
                        success=False,
                        error={
                            "code": "TOOL_MEDIATION_FAILED",
                            "message": str(exc),
                            "category": "transport",
                        },
                    )
        deliver = getattr(adapter, "deliver_tool_response", None)
        if deliver is None:
            raise WorkerRunError("TOOL_RESPONSE_UNSUPPORTED", "adapter does not support mediated tool responses")
        await deliver(response)

    async def append_event(self, event: WorkerEvent) -> dict[str, Any]:
        event_payload = event.model_dump(mode="json")
        event_hash = hashlib.sha256(json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self.storage is not None:
            try:
                return await self.storage.append_worker_event(
                    run_id=event.run_id,
                    sequence=event.sequence,
                    event_type=event.event_type.value,
                    event=event_payload,
                    event_sha256=event_hash,
                    max_event_count=self.max_event_count,
                )
            except ValueError as exc:
                if "event limit" in str(exc).lower():
                    raise WorkerRunError("EVENT_LIMIT", "worker event limit exceeded") from exc
                raise
        memory_events = self._memory_events.setdefault(event.run_id, [])
        existing = next((row for row in memory_events if row["sequence"] == event.sequence), None)
        if existing:
            if existing["event_sha256"] != event_hash:
                raise WorkerRunError("DUPLICATE_EVENT_CONFLICT", "duplicate worker event differs from the stored event")
            return existing
        if len(memory_events) >= self.max_event_count:
            raise WorkerRunError("EVENT_LIMIT", "worker event limit exceeded")
        row = {"id": str(event.event_id), "run_id": event.run_id, "sequence": event.sequence, "event_type": event.event_type.value, "event_json": event_payload, "event_sha256": event_hash}
        memory_events.append(row)
        return row

    async def execute(
        self,
        request: WorkerRunRequest,
        adapter: WorkerAdapter,
        *,
        worker_registry_id: UUID | None = None,
        worker_shell_version_id: UUID | None = None,
        adapter_id: UUID | None = None,
        steward_id: UUID | None = None,
        model_resolution_snapshot_id: UUID | None = None,
    ) -> WorkerRunOutcome:
        created_row = await self.create_run(
            request,
            worker_registry_id=worker_registry_id,
            worker_shell_version_id=worker_shell_version_id,
            adapter_id=adapter_id,
            steward_id=steward_id,
            model_resolution_snapshot_id=model_resolution_snapshot_id,
        )
        canonical_run_id = UUID(str(created_row["id"]))
        if canonical_run_id != request.run_id:
            # Idempotency replays are reads of the canonical run. Never create
            # a second queue or persist events under the caller's throw-away
            # UUID; doing so leaves the replay waiting on a run that cannot
            # reach a terminal state.
            request = request.model_copy(update={"run_id": canonical_run_id})
            row = await self.get_run(canonical_run_id)
            state = str((row or {}).get("state", "CREATED"))
            stored_result = None
            if row and row.get("result_json"):
                try:
                    stored_result = WorkerResult.model_validate(row["result_json"])
                except (TypeError, ValueError):
                    logger.warning("canonical_worker_result_rehydrate_failed", extra={"run_id": str(canonical_run_id)})
            return WorkerRunOutcome(run_id=canonical_run_id, state=state, result=stored_result)
        row = await self.get_run(canonical_run_id)
        if row is not None and str(row.get("state")) in TERMINAL_RUN_STATES:
            stored_result = None
            if row.get("result_json"):
                try:
                    stored_result = WorkerResult.model_validate(row["result_json"])
                except (TypeError, ValueError):
                    logger.warning("worker_result_rehydrate_failed", extra={"run_id": str(canonical_run_id)})
            return WorkerRunOutcome(run_id=canonical_run_id, state=str(row["state"]), result=stored_result)
        events: list[WorkerEvent] = []
        accepted: WorkerRunAccepted | None = None
        negotiation: dict[str, Any] | None = None
        readiness: WorkerReadiness | None = None
        result: WorkerResult | None = None
        try:
            current_state = str((await self.get_run(request.run_id) or {}).get("state") or "CREATED")
            await self.transition(request.run_id, "VALIDATING", expected=current_state)
            if (
                bool((request.checkpoint_policy or {}).get("required"))
                and adapter.capabilities.checkpoint_mode == CheckpointMode.UNSUPPORTED
            ):
                raise WorkerRunError(
                    "CHECKPOINT_UNSUPPORTED",
                    "worker run requires checkpoints but the adapter declares them unsupported",
                )
            readiness = await adapter.readiness(request)
            if not readiness.ready:
                raise WorkerRunError("NOT_READY", "; ".join(readiness.blockers), details={"blockers": readiness.blockers})
            try:
                protocol = negotiate_protocol(
                    request.protocol,
                    required_capabilities={item.name for item in request.capability_requirements if item.required},
                    offered_capabilities=set(adapter.capabilities.capability_names),
                )
                negotiation = protocol.as_dict()
            except ProtocolNegotiationError as exc:
                raise WorkerRunError("PROTOCOL_NEGOTIATION_FAILED", str(exc)) from exc
            await self.transition(request.run_id, "READY", expected="VALIDATING", negotiation=negotiation)
            await self.transition(request.run_id, "DISPATCHING", expected="READY")
            accepted = await adapter.start(request)
            await self.transition(
                request.run_id,
                "RUNNING",
                expected="DISPATCHING",
                transition_metadata={
                    "runtime_run_id": accepted.runtime_run_id if accepted else None,
                    "accepted_metadata": accepted.metadata if accepted else {},
                },
            )

            async def collect_events() -> None:
                nonlocal result
                async for event in adapter.events(request.run_id):
                    if event.run_id != request.run_id or event.worker_id != request.worker_id:
                        raise WorkerRunError("EVENT_SCOPE_MISMATCH", "worker event does not match the requested run")
                    if events and event.sequence <= events[-1].sequence:
                        # Duplicate events are persisted idempotently only when
                        # their content is identical; the store enforces that.
                        if event.sequence < events[-1].sequence:
                            await self.append_event(event)
                            continue
                    events.append(event)
                    await self.append_event(event)
                    if event.event_type == EventType.TOOL_REQUEST:
                        await self._mediate_tool_request(request, adapter, event)
                    if event.event_type == EventType.CHECKPOINT and event.checkpoint is not None and self.storage is not None:
                        await self.storage.create_worker_checkpoint(
                            run_id=request.run_id,
                            sequence=event.checkpoint.sequence,
                            state=event.checkpoint.state,
                            resumable=event.checkpoint.resumable,
                        )
                    if event.result is not None:
                        result = event.result
                    elif event.event_type == EventType.ERROR and event.error is not None:
                        result = WorkerResult(run_id=request.run_id, worker_id=request.worker_id, success=False, error=event.error)
                    elif event.event_type == EventType.CANCELLED:
                        result = WorkerResult(
                            run_id=request.run_id,
                            worker_id=request.worker_id,
                            success=False,
                            error=event.error or {"code": "CANCELLED", "message": "worker cancelled"},
                        )

            timeout = request.timeout_seconds or 86_400
            await asyncio.wait_for(collect_events(), timeout=timeout)
            if result is None:
                raise WorkerRunError("MISSING_RESULT", "worker stream ended without a normalized result")
            terminal = "SUCCEEDED" if result.success else "CANCELLED" if result.error and result.error.code == "CANCELLED" else "FAILED"
            if terminal != "CANCELLED":
                await self._persist_result_evidence(request, result)
            terminal_kwargs = {
                "result": result.model_dump(mode="json"),
                "error": None if result.success else result.error.model_dump(mode="json") if result.error else None,
                "replay_metadata": result.replay_metadata,
                "reason": "worker emitted a normalized terminal result",
                "correlation_id": request.idempotency_key,
            }
            persisted_terminal = await self.transition(
                request.run_id,
                terminal,
                expected="RUNNING",
                **terminal_kwargs,
            )
            # A pause request owns RUNNING -> PAUSING, but it does not make a
            # concurrently emitted terminal result disappear.  Allow the
            # result to settle the run from PAUSING before pause can write
            # PAUSED, preserving one coherent durable state and result.
            if persisted_terminal is None:
                persisted_terminal = await self.transition(
                    request.run_id,
                    terminal,
                    expected="PAUSING",
                    **terminal_kwargs,
                )
            if persisted_terminal is None:
                current = await self.get_run(request.run_id)
                current_state = str((current or {}).get("state") or terminal)
                return WorkerRunOutcome(run_id=request.run_id, state=current_state, accepted=accepted, result=result, events=tuple(events), readiness=readiness, negotiation=negotiation)
            return WorkerRunOutcome(run_id=request.run_id, state=str(persisted_terminal["state"]), accepted=accepted, result=result, events=tuple(events), readiness=readiness, negotiation=negotiation)
        except TimeoutError:
            await adapter.cancel(WorkerCancellation(run_id=request.run_id, reason="worker run timed out", requested_by="worker-run-controller", force=True))
            await self.transition(request.run_id, "TIMED_OUT", expected="RUNNING", error={"code": "TIMEOUT", "message": "worker run exceeded timeout"})
            return WorkerRunOutcome(run_id=request.run_id, state="TIMED_OUT", accepted=accepted, events=tuple(events), readiness=readiness, negotiation=negotiation)
        except WorkerRunError as exc:
            current = (await self.get_run(request.run_id) or {}).get("state")
            if current not in TERMINAL_RUN_STATES:
                target = "CANCELLED" if exc.state == "CANCELLED" else "FAILED"
                try:
                    await self.transition(request.run_id, target, expected=str(current), error={"code": exc.code, "message": str(exc), "details": exc.details})
                except Exception:
                    logger.exception("Failed to persist worker run failure")
            return WorkerRunOutcome(run_id=request.run_id, state="CANCELLED" if exc.state == "CANCELLED" else "FAILED", accepted=accepted, events=tuple(events), readiness=readiness, negotiation=negotiation)
        except Exception as exc:
            logger.exception("Unexpected worker run controller failure")
            current = (await self.get_run(request.run_id) or {}).get("state")
            if current not in TERMINAL_RUN_STATES:
                try:
                    await self.transition(request.run_id, "FAILED", expected=str(current), error={"code": "CONTROLLER_ERROR", "message": str(exc)})
                except Exception:
                    logger.exception("Failed to persist unexpected worker run failure")
            return WorkerRunOutcome(run_id=request.run_id, state="FAILED", accepted=accepted, events=tuple(events), readiness=readiness, negotiation=negotiation)

    async def cancel(self, run_id: UUID, adapter: WorkerAdapter, *, reason: str, requested_by: str, force: bool = False) -> dict[str, Any] | None:
        row = await self.get_run(run_id)
        if row is None:
            return None
        if row.get("state") in TERMINAL_RUN_STATES:
            return row
        current = str(row.get("state"))
        if current in {"QUEUED", "CLAIMED", "CREATED"}:
            await self.transition(run_id, "CANCELLED", expected=current, error={"code": "CANCELLED", "message": reason})
            return await self.get_run(run_id)
        await adapter.cancel(WorkerCancellation(run_id=run_id, reason=reason, requested_by=requested_by, force=force))
        if current in {"RUNNING", "DISPATCHING", "READY", "VALIDATING"}:
            await self.transition(run_id, "CANCELLED", expected=current, error={"code": "CANCELLED", "message": reason})
        return await self.get_run(run_id)

    async def pause(self, run_id: UUID, adapter: WorkerAdapter, *, reason: str, requested_by: str) -> dict[str, Any] | None:
        checkpoint_mode = adapter.capabilities.checkpoint_mode
        if checkpoint_mode == CheckpointMode.UNSUPPORTED:
            raise WorkerRunError(
                "UNSUPPORTED_CAPABILITY",
                "worker adapter does not support pause/checkpoint control",
            )
        if checkpoint_mode == CheckpointMode.RESTART_ONLY:
            raise WorkerRunError(
                "CHECKPOINT_RESTART_ONLY",
                "worker adapter supports restart from a safe point, not in-place pause",
            )
        if isinstance(adapter, BaseWorkerAdapter) and type(adapter).pause is BaseWorkerAdapter.pause:
            raise WorkerRunError(
                "UNSUPPORTED_CAPABILITY",
                "worker adapter has no in-place pause implementation",
            )
        row = await self.get_run(run_id)
        if row is None:
            return row
        if row.get("state") != "RUNNING":
            raise WorkerRunError(
                "RUN_NOT_PAUSABLE",
                f"worker run is {row.get('state')}, not RUNNING",
                details={"state": row.get("state")},
            )
        pausing = await self.transition(
            run_id,
            "PAUSING",
            expected="RUNNING",
            actor=requested_by,
            reason=reason,
        )
        if pausing is None:
            raise WorkerRunError("RUN_STATE_CONFLICT", "worker run state changed before it could be paused")
        try:
            await adapter.pause(WorkerPause(run_id=run_id, reason=reason, requested_by=requested_by))
        except Exception as exc:
            # A failed pause leaves the runtime's exact state uncertain.  Do
            # not silently return it to RUNNING and risk duplicate effects.
            await self.transition(
                run_id,
                "FAILED",
                expected="PAUSING",
                error={"code": "PAUSE_FAILED", "message": str(exc)},
                actor=requested_by,
                reason=reason,
            )
            raise WorkerRunError("PAUSE_FAILED", str(exc)) from exc
        paused = await self.transition(
            run_id,
            "PAUSED",
            expected="PAUSING",
            actor=requested_by,
            reason=reason,
        )
        if paused is None:
            raise WorkerRunError("RUN_STATE_CONFLICT", "worker run state changed while pause was acknowledged")
        return paused

    async def resume(self, run_id: UUID, adapter: WorkerAdapter, *, requested_by: str, checkpoint_id: UUID | None = None) -> dict[str, Any] | None:
        row = await self.get_run(run_id)
        if row is None:
            return row
        if row.get("state") != "PAUSED":
            raise WorkerRunError(
                "RUN_NOT_RESUMABLE",
                f"worker run is {row.get('state')}, not PAUSED",
                details={"state": row.get("state")},
            )
        resuming = await self.transition(
            run_id,
            "RESUMING",
            expected="PAUSED",
            actor=requested_by,
            reason="operator requested resume",
            transition_metadata={"checkpoint_id": str(checkpoint_id) if checkpoint_id else None},
        )
        if resuming is None:
            raise WorkerRunError("RUN_STATE_CONFLICT", "worker run state changed before it could be resumed")
        try:
            await adapter.resume(WorkerResume(run_id=run_id, requested_by=requested_by, checkpoint_id=checkpoint_id))
        except Exception as exc:
            await self.transition(
                run_id,
                "FAILED",
                expected="RESUMING",
                error={"code": "RESUME_FAILED", "message": str(exc)},
                actor=requested_by,
                reason="resume command failed",
            )
            raise WorkerRunError("RESUME_FAILED", str(exc)) from exc
        resumed = await self.transition(
            run_id,
            "RUNNING",
            expected="RESUMING",
            actor=requested_by,
            reason="runtime acknowledged resume",
            transition_metadata={"checkpoint_id": str(checkpoint_id) if checkpoint_id else None},
        )
        if resumed is None:
            raise WorkerRunError("RUN_STATE_CONFLICT", "worker run state changed while resume was acknowledged")
        return resumed
