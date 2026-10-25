"""Authoritative post-claim coordinator for durable worker-run lifecycle state.

Host placement, claim acquisition, and host-loss recovery retain ownership of
their respective worker-run transitions; this controller owns the execution
lifecycle after a run has been claimed.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import mimetypes
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from .adapters import BaseWorkerAdapter, WorkerAdapter, WorkerStartCancelled
from .models import (
    ArtifactKind,
    CheckpointMode,
    EventType,
    WorkerArtifact,
    WorkerCancellation,
    WorkerCancellationReceipt,
    WorkerEvent,
    WorkerPause,
    WorkerReadiness,
    WorkerResult,
    WorkerResume,
    WorkerRunAccepted,
    WorkerRunRequest,
    WorkerRuntimeStatus,
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
        self._memory_tool_effects: dict[tuple[UUID, str], dict[str, Any]] = {}
        self._tool_artifacts_by_run: dict[UUID, dict[str, WorkerArtifact]] = {}
        self._dispatch_locks: dict[UUID, asyncio.Lock] = {}
        self._dispatch_phase: dict[UUID, str] = {}
        self._cancel_events: dict[UUID, asyncio.Event] = {}

    @staticmethod
    def _tool_request_sha256(tool_request: Any) -> str:
        """Hash the effect identity while ignoring a transport-local request ID."""

        payload = tool_request.model_dump(mode="json")
        payload.pop("request_id", None)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    async def _begin_tool_effect(
        self,
        tool_request: Any,
        *,
        claim_fence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_sha256 = self._tool_request_sha256(tool_request)
        fence = dict(claim_fence or {})
        claim_owner = fence.get("claim_owner")
        attempt_count = int(fence.get("attempt_count") or 0)
        storage_begin = getattr(self.storage, "begin_worker_tool_effect", None)
        if callable(storage_begin):
            return await storage_begin(
                run_id=tool_request.run_id,
                request_id=tool_request.request_id,
                idempotency_key=tool_request.idempotency_key,
                tool_name=tool_request.tool_name,
                request_sha256=request_sha256,
                claim_owner=claim_owner,
                attempt_count=attempt_count,
            )
        if claim_owner is not None or attempt_count > 0:
            # A claimed execution must have durable authority state against
            # which the generation can be checked before any tool dispatch.
            return {"disposition": "CLAIM_LOST"}

        key = (tool_request.run_id, tool_request.idempotency_key)
        existing = self._memory_tool_effects.get(key)
        if existing is not None:
            result = dict(existing)
            if result["request_sha256"] != request_sha256:
                result["disposition"] = "CONFLICT"
            elif result["state"] == "COMPLETED" and result.get("response_json") is not None:
                result["disposition"] = "REPLAY"
            elif result["state"] == "AMBIGUOUS":
                result["disposition"] = "AMBIGUOUS"
            else:
                result["disposition"] = "AMBIGUOUS"
            return result
        row = {
            "id": uuid4(),
            "run_id": tool_request.run_id,
            "request_id": tool_request.request_id,
            "idempotency_key": tool_request.idempotency_key,
            "tool_name": tool_request.tool_name,
            "request_sha256": request_sha256,
            "state": "IN_FLIGHT",
            "response_json": None,
        }
        self._memory_tool_effects[key] = row
        return {**row, "disposition": "NEW"}

    async def _complete_tool_effect(
        self,
        tool_request: Any,
        effect: dict[str, Any],
        *,
        state: str,
        response: WorkerToolResponse,
    ) -> None:
        response_json = response.model_dump(mode="json")
        storage_complete = getattr(self.storage, "complete_worker_tool_effect", None)
        if callable(storage_complete):
            await storage_complete(
                effect["id"],
                state=state,
                response_json=response_json,
            )
            return
        key = (tool_request.run_id, tool_request.idempotency_key)
        row = self._memory_tool_effects.get(key)
        if row is not None:
            row.update(
                {
                    "state": state,
                    "response_json": response_json,
                }
            )

    def _record_file_write_artifact(
        self,
        request: WorkerRunRequest,
        tool_request: Any,
        response: WorkerToolResponse,
        effect: dict[str, Any],
    ) -> None:
        """Promote a confirmed mediated file write into run evidence.

        The path and bytes come from the ToolRequest, while the digest and
        byte count are independently recomputed and compared with the
        authenticated Tool Service response. The workspace bytes are later
        opened without following symlinks by the artifact registrar.
        """

        if tool_request.tool_name != "file_write" or not response.success or request.project_id is None:
            return
        if not isinstance(tool_request.arguments, dict) or not isinstance(response.result, dict):
            raise WorkerRunError(
                "TOOL_ARTIFACT_INTEGRITY_INVALID",
                "the mediated file write lacks verifiable project artifact metadata",
            )
        path = tool_request.arguments.get("path")
        content = tool_request.arguments.get("content")
        result = response.result
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not isinstance(content, str)
            or result.get("success") is not True
            or result.get("path") != path
            or str(result.get("project_id") or "") != str(request.project_id)
        ):
            raise WorkerRunError(
                "TOOL_ARTIFACT_INTEGRITY_INVALID",
                "the mediated file write does not match the authenticated project and safe path",
            )
        content_bytes = content.encode("utf-8")
        digest = hashlib.sha256(content_bytes).hexdigest()
        size_bytes = result.get("bytes_written")
        if (
            result.get("sha256") != digest
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes != len(content_bytes)
        ):
            raise WorkerRunError(
                "TOOL_ARTIFACT_INTEGRITY_INVALID",
                "the mediated file write checksum or byte count is invalid",
            )
        metadata: dict[str, Any] = {
            "worker_run_id": str(request.run_id),
            "project_id": str(request.project_id),
            "source_tool": "file_write",
        }
        effect_id = effect.get("id")
        if effect_id is not None:
            metadata["tool_effect_id"] = str(effect_id)
        artifact = WorkerArtifact(
            kind=ArtifactKind.FILE,
            name=path,
            uri=path,
            sha256=digest,
            size_bytes=size_bytes,
            mime_type=mimetypes.guess_type(path)[0],
            metadata=metadata,
        )
        # A later confirmed write to the same run-relative path supersedes
        # the earlier descriptor; artifact upload will verify its bytes.
        self._tool_artifacts_by_run.setdefault(request.run_id, {})[path] = artifact

    def _attach_tool_artifacts(
        self,
        request: WorkerRunRequest,
        result: WorkerResult,
    ) -> WorkerResult:
        tool_artifacts = self._tool_artifacts_by_run.get(request.run_id, {})
        if not tool_artifacts:
            return result
        result_uris = {artifact.uri for artifact in result.artifacts}
        additional = [
            artifact
            for uri, artifact in sorted(tool_artifacts.items())
            if uri not in result_uris
        ]
        if not additional:
            return result
        return result.model_copy(update={"artifacts": [*result.artifacts, *additional]})

    async def create_run(
        self,
        request: WorkerRunRequest,
        *,
        worker_registry_id: UUID | None = None,
        worker_shell_version_id: UUID | None = None,
        adapter_id: UUID | None = None,
        skill_bundle_id: UUID | None = None,
        steward_id: UUID | None = None,
        model_resolution_snapshot_id: UUID | None = None,
        retry_of_run_id: UUID | None = None,
        retry_context_json: dict[str, Any] | None = None,
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
                skill_bundle_id=skill_bundle_id,
                steward_id=steward_id,
                model_resolution_snapshot_id=model_resolution_snapshot_id,
                retry_of_run_id=retry_of_run_id,
                retry_context_json=retry_context_json,
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
            "skill_bundle_id": skill_bundle_id,
            "retry_of_run_id": retry_of_run_id,
            "retry_context_json": retry_context_json or {},
            "created_at": datetime.now(UTC),
        }
        self._memory_runs[request.run_id] = row
        self._memory_events[request.run_id] = []
        return row

    async def get_run(self, run_id: UUID) -> dict[str, Any] | None:
        if self.storage is not None:
            return await self.storage.get_worker_run(run_id)
        return self._memory_runs.get(run_id)

    @staticmethod
    def _claim_fence(row: dict[str, Any] | None) -> dict[str, Any]:
        """Return the persisted owner/attempt fence for a claimed run."""

        if row is None:
            return {}
        owner = row.get("claim_owner")
        attempt_count = int(row.get("attempt_count") or 0)
        if owner is None and attempt_count == 0:
            return {}
        return {
            "claim_owner": str(owner) if owner is not None else None,
            "attempt_count": attempt_count,
        }

    @staticmethod
    def _claim_is_current(
        row: dict[str, Any] | None,
        claim_fence: dict[str, Any],
    ) -> bool:
        if row is None:
            return False
        owner = row.get("claim_owner")
        attempt_count = int(row.get("attempt_count") or 0)
        if not claim_fence:
            return owner is None and attempt_count == 0
        lease_expires_at = row.get("lease_expires_at")
        return (
            owner == claim_fence.get("claim_owner")
            and attempt_count == claim_fence.get("attempt_count")
            and lease_expires_at is not None
            and lease_expires_at > datetime.now(UTC)
            and str(row.get("state") or "") not in TERMINAL_RUN_STATES
        )

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
        claim_owner: str | None = None,
        attempt_count: int | None = None,
        allow_unfenced_cancel: bool = False,
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
                claim_owner=claim_owner,
                attempt_count=attempt_count,
                allow_unfenced_cancel=allow_unfenced_cancel,
            )
        row = self._memory_runs.get(run_id)
        if row is None:
            return None
        current = str(row["state"])
        if expected is not None and current != expected:
            return None
        current_owner = row.get("claim_owner")
        current_attempt = int(row.get("attempt_count") or 0)
        control_cancel = target == "CANCELLED" and allow_unfenced_cancel
        if not control_cancel and (current_owner is not None or current_attempt > 0):
            lease_expires_at = row.get("lease_expires_at")
            if (
                claim_owner != current_owner
                or attempt_count != current_attempt
                or lease_expires_at is None
                or lease_expires_at <= datetime.now(UTC)
            ):
                return None
        elif not control_cancel and (claim_owner is not None or attempt_count is not None):
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
        if transition_metadata:
            row.setdefault("transition_metadata", {}).update(transition_metadata)
            runtime_binding = transition_metadata.get("runtime_binding")
            if isinstance(runtime_binding, dict):
                row["runtime_run_id"] = runtime_binding.get("runtime_run_id")
                row["runtime_binding"] = dict(runtime_binding)
        if target in {"PAUSED", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            binding = row.get("runtime_binding")
            if isinstance(binding, dict):
                binding["status"] = target
        if target == "RUNNING":
            row.setdefault("started_at", datetime.now(UTC))
        if target in TERMINAL_RUN_STATES:
            row["completed_at"] = datetime.now(UTC)
        return row

    async def reconcile(
        self,
        run_id: UUID,
        adapter: WorkerAdapter,
    ) -> WorkerRuntimeStatus:
        """Observe subordinate runtime state without mutating canonical state.

        A controller restart must not infer completion from missing in-memory
        adapter maps.  The adapter may provide a durable runtime lookup; when
        it cannot, this method returns ``UNKNOWN`` and leaves the AIAT run
        untouched for an explicit recovery decision.
        """

        row = await self.get_run(run_id)
        if row is None:
            return WorkerRuntimeStatus(
                run_id=run_id,
                status="UNKNOWN",
                details={"reason": "canonical worker run not found"},
            )

        runtime_run_id = row.get("runtime_run_id")
        if runtime_run_id is not None:
            runtime_run_id = str(runtime_run_id)
        binding_reader = getattr(self.storage, "get_worker_runtime_binding", None)
        if runtime_run_id is None and callable(binding_reader):
            try:
                binding = await binding_reader(run_id)
            except Exception as exc:
                logger.warning(
                    "worker_runtime_binding_read_failed",
                    extra={"run_id": str(run_id), "error": type(exc).__name__},
                )
                binding = None
            if isinstance(binding, dict):
                candidate = binding.get("runtime_run_id")
                if candidate is not None:
                    runtime_run_id = str(candidate)
        if runtime_run_id is None:
            transition_reader = getattr(self.storage, "list_worker_run_transitions", None)
            if callable(transition_reader):
                try:
                    transitions = await transition_reader(run_id)
                except Exception as exc:
                    logger.warning(
                        "worker_runtime_transition_read_failed",
                        extra={"run_id": str(run_id), "error": type(exc).__name__},
                    )
                    transitions = []
                for transition in reversed(transitions or []):
                    metadata = transition.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        continue
                    runtime_run_id = metadata.get("runtime_run_id")
                    accepted_metadata = metadata.get("accepted_metadata")
                    if runtime_run_id is None and isinstance(accepted_metadata, dict):
                        runtime_run_id = accepted_metadata.get("runtime_run_id")
                        if runtime_run_id is None:
                            # OpenCode and similar adapters may use a named
                            # session identifier in accepted metadata.
                            runtime_run_id = accepted_metadata.get("opencode_session_id")
                    if runtime_run_id is not None:
                        runtime_run_id = str(runtime_run_id)
                        break

        reconciler = getattr(adapter, "reconcile", None)
        if not callable(reconciler):
            return WorkerRuntimeStatus(
                run_id=run_id,
                status="UNKNOWN",
                runtime_run_id=runtime_run_id,
                details={"reason": "adapter does not expose runtime reconciliation"},
            )
        try:
            raw_status = reconciler(run_id, runtime_run_id=runtime_run_id)
            observed = await raw_status if inspect.isawaitable(raw_status) else raw_status
            if isinstance(observed, WorkerRuntimeStatus):
                normalized = observed
            else:
                normalized = WorkerRuntimeStatus.model_validate(observed)
            if normalized.run_id != run_id:
                logger.error(
                    "worker_runtime_reconciliation_scope_mismatch",
                    extra={"run_id": str(run_id)},
                )
                return WorkerRuntimeStatus(
                    run_id=run_id,
                    status="UNKNOWN",
                    runtime_run_id=runtime_run_id,
                    details={"reason": "adapter returned a different run ID"},
                )
            recorder = getattr(self.storage, "record_worker_runtime_observation", None)
            if callable(recorder):
                try:
                    await recorder(
                        run_id,
                        status=normalized.status,
                        runtime_run_id=normalized.runtime_run_id or runtime_run_id,
                    )
                except Exception as exc:
                    # The observation is useful audit state, but it is not
                    # allowed to turn a read-only reconciliation into a
                    # canonical worker-run transition or hide the runtime
                    # fact already returned to the caller.
                    logger.warning(
                        "worker_runtime_observation_persist_failed",
                        extra={"run_id": str(run_id), "error": type(exc).__name__},
                    )
            return normalized
        except Exception as exc:
            logger.warning(
                "worker_runtime_reconciliation_failed",
                extra={"run_id": str(run_id), "error": type(exc).__name__},
            )
            return WorkerRuntimeStatus(
                run_id=run_id,
                status="UNKNOWN",
                runtime_run_id=runtime_run_id,
                details={"reason": "adapter reconciliation failed", "error_type": type(exc).__name__},
            )

    async def _persist_result_evidence(
        self,
        request: WorkerRunRequest,
        result: WorkerResult,
        *,
        adapter: WorkerAdapter | None = None,
        terminal_success: dict[str, Any] | None = None,
        terminal_failure: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Persist every returned artifact and usage record before terminal state.

        A worker result is only authoritative once its evidence is queryable.
        The canonical artifact table keeps existing retention behaviour; the
        worker-specific link preserves the run-local kind, checksum, and URI.
        """
        claim_fence = request.execution_claim_fence
        attempt_count = int(claim_fence.get("attempt_count") or 0)
        if terminal_success is not None and terminal_failure is not None:
            raise ValueError("result persistence cannot finalize both success and failure")
        terminal_completion = terminal_success or terminal_failure
        if self.storage is None:
            if claim_fence and terminal_completion is not None:
                persistence_code = (
                    "ATOMIC_FAILURE_PERSISTENCE_UNAVAILABLE"
                    if terminal_failure is not None
                    else "ATOMIC_SUCCESS_PERSISTENCE_UNAVAILABLE"
                )
                completion_kind = "failure" if terminal_failure is not None else "success"
                raise WorkerRunError(
                    persistence_code,
                    f"claimed worker {completion_kind} requires atomic result and evidence storage",
                )
            return None
        context = getattr(adapter, "context", None)
        registrar = getattr(context, "artifact_registrar", None)
        tool_artifacts = [
            artifact
            for artifact in result.artifacts
            if artifact.metadata.get("source_tool") == "file_write"
        ]
        if tool_artifacts and not callable(registrar):
            raise WorkerRunError(
                "ARTIFACT_REGISTRAR_UNAVAILABLE",
                "the mediated file output cannot be registered as project evidence",
            )
        registered_tool_artifacts: list[WorkerArtifact] = []
        try:
            if callable(registrar):
                for artifact in tool_artifacts:
                    await registrar(artifact, claim_fence=claim_fence)
                    registered_tool_artifacts.append(artifact)
        except Exception:
            if registered_tool_artifacts:
                await self._cleanup_unpersisted_artifacts(
                    adapter,
                    result.model_copy(update={"artifacts": registered_tool_artifacts}),
                    claim_fence,
                )
            raise
        artifact_rows: list[dict[str, Any]] = []
        for artifact in result.artifacts:
            metadata = {
                **artifact.metadata,
                "artifact_name": artifact.name,
                "artifact_kind": artifact.kind.value,
                "mime_type": artifact.mime_type,
                "retention_class": artifact.retention_class,
                "worker_run_id": str(request.run_id),
                "worker_execution_attempt": attempt_count,
            }
            artifact_rows.append(
                {
                    "agent_id": request.worker_id,
                    "path": artifact.uri,
                    "metadata": metadata,
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes,
                    "kind": artifact.kind.value,
                    "uri": artifact.uri,
                    "trace_id": request.trace_id,
                    "span_id": request.span_id,
                }
            )
        usage = result.usage
        usage_row = {
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
                "trace_id": request.trace_id,
                "span_id": request.span_id,
            }
        if claim_fence and terminal_completion is not None:
            terminal_state = "FAILED" if terminal_failure is not None else "SUCCEEDED"
            writer_name = (
                "complete_worker_run_failure_claim_fenced"
                if terminal_state == "FAILED"
                else "complete_worker_run_success_claim_fenced"
            )
            terminal_writer = getattr(
                self.storage,
                writer_name,
                None,
            )
            if not callable(terminal_writer):
                await self._cleanup_unpersisted_artifacts(adapter, result, claim_fence)
                persistence_code = (
                    "ATOMIC_FAILURE_PERSISTENCE_UNAVAILABLE"
                    if terminal_state == "FAILED"
                    else "ATOMIC_SUCCESS_PERSISTENCE_UNAVAILABLE"
                )
                completion_kind = "failure" if terminal_state == "FAILED" else "success"
                raise WorkerRunError(
                    persistence_code,
                    f"claimed worker {completion_kind} requires atomic result and evidence storage",
                )
            project_usage: dict[str, Any] | None = None
            if request.project_id is not None and callable(
                getattr(self.storage, "record_project_usage", None)
            ):
                worker_id: UUID | None = None
                with suppress(TypeError, ValueError):
                    worker_id = UUID(str(request.worker_id))
                project_usage = {
                    "project_id": request.project_id,
                    "event_type": "llm",
                    "run_id": request.run_id,
                    "worker_id": worker_id,
                    "model": usage.exact_model_id,
                    "provider_id": usage.provider,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "cost_usd": usage.cost_usd,
                    "duration_ms": usage.duration_ms,
                    "resource_json": {
                        "cpu_seconds": usage.cpu_seconds,
                        "memory_bytes": usage.memory_bytes,
                    },
                    "trace_id": request.trace_id,
                    "span_id": request.span_id,
                    "idempotency_key": (
                        f"worker-run:{request.run_id}:attempt:{attempt_count}:usage"
                    ),
                    "details": {
                        "source": "worker_result",
                        "runtime_worker_id": request.worker_id,
                    },
                }
            try:
                terminal_kwargs: dict[str, Any] = {
                    "run_id": request.run_id,
                    "claim_owner": claim_fence.get("claim_owner"),
                    "attempt_count": attempt_count,
                    "artifacts": artifact_rows,
                    "usage": usage_row,
                    "result": terminal_completion["result"],
                    "project_usage": project_usage,
                    "reason": terminal_completion.get("reason")
                    or "worker emitted a normalized terminal result",
                    "correlation_id": terminal_completion.get("correlation_id"),
                }
                if terminal_state == "FAILED":
                    terminal_kwargs["error"] = terminal_completion.get("error")
                terminal_kwargs["replay_metadata"] = terminal_completion.get(
                    "replay_metadata"
                )
                persisted = await terminal_writer(**terminal_kwargs)
            except Exception:
                # A transaction exception can have an ambiguous commit outcome.
                # Preserve staged artifacts because durable evidence may reference them.
                raise
            if persisted is None:
                await self._cleanup_unpersisted_artifacts(adapter, result, claim_fence)
                raise WorkerRunError(
                    "WORKER_RUN_CLAIM_LOST",
                    "worker run claim changed before result evidence and terminal state were persisted",
                )
            return persisted
        atomic_writer = getattr(self.storage, "persist_worker_result_evidence_claim_fenced", None)
        if callable(atomic_writer):
            try:
                persisted = await atomic_writer(
                    run_id=request.run_id,
                    claim_owner=claim_fence.get("claim_owner"),
                    attempt_count=attempt_count,
                    artifacts=artifact_rows,
                    usage=usage_row,
                )
            except Exception:
                await self._cleanup_unpersisted_artifacts(adapter, result, claim_fence)
                raise
            if persisted is None:
                await self._cleanup_unpersisted_artifacts(adapter, result, claim_fence)
                raise WorkerRunError(
                    "WORKER_RUN_CLAIM_LOST",
                    "worker run claim changed before result evidence was persisted",
                )
        else:
            if claim_fence:
                await self._cleanup_unpersisted_artifacts(adapter, result, claim_fence)
                raise WorkerRunError(
                    "WORKER_RUN_CLAIM_LOST",
                    "durable claim-fenced result evidence storage is unavailable",
                )
            for artifact in artifact_rows:
                stored = await self.storage.create_artifact(
                    agent_id=artifact["agent_id"],
                    path=artifact["path"],
                    metadata=artifact["metadata"],
                    sha256=artifact["sha256"],
                    size_bytes=artifact["size_bytes"],
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
                    kind=artifact["kind"],
                    uri=artifact["uri"],
                    sha256=artifact["sha256"],
                    size_bytes=artifact["size_bytes"],
                    metadata=artifact["metadata"],
                    trace_id=request.trace_id,
                    span_id=request.span_id,
                )
            await self.storage.create_worker_usage(run_id=request.run_id, usage=usage_row)
        # The worker-specific record preserves the exact runtime payload;
        # project_usage_events is the cross-tool/LLM accounting surface used
        # by budgets and dashboards.  Keep this best-effort for lightweight
        # storage doubles, but make the durable AgentStorage path idempotent.
        project_id = request.project_id
        record_usage = getattr(self.storage, "record_project_usage", None)
        if project_id is not None and callable(record_usage):
            worker_id: UUID | None = None
            with suppress(TypeError, ValueError):
                worker_id = UUID(str(request.worker_id))
            try:
                project_usage = await record_usage(
                    project_id=project_id,
                    event_type="llm",
                    run_id=request.run_id,
                    worker_id=worker_id,
                    **(
                        {
                            "claim_owner": claim_fence.get("claim_owner"),
                            "attempt_count": attempt_count,
                        }
                        if claim_fence
                        else {}
                    ),
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
                    idempotency_key=(
                        f"worker-run:{request.run_id}:attempt:{attempt_count}:usage"
                        if claim_fence
                        else f"worker-run:{request.run_id}:usage"
                    ),
                    details={"source": "worker_result", "runtime_worker_id": request.worker_id},
                )
                if claim_fence and project_usage is None:
                    raise WorkerRunError(
                        "WORKER_RUN_CLAIM_LOST",
                        "worker run claim changed before project usage was persisted",
                    )
            except WorkerRunError:
                raise
            except Exception:
                # The worker-specific usage record above remains authoritative
                # if a legacy storage double or a telemetry-only backend does
                # not yet expose the project ledger columns.
                logger.exception("project_usage_ledger_write_failed", extra={"run_id": str(request.run_id)})
        return None

    @staticmethod
    async def _cleanup_unpersisted_artifacts(
        adapter: WorkerAdapter | None,
        result: WorkerResult,
        claim_fence: dict[str, Any],
    ) -> None:
        context = getattr(adapter, "context", None)
        registrar = getattr(context, "artifact_registrar", None)
        if not callable(registrar):
            return
        for artifact in result.artifacts:
            try:
                await registrar(
                    artifact,
                    claim_fence=claim_fence,
                    cleanup=True,
                )
            except Exception as exc:
                logger.warning(
                    "worker_artifact_cleanup_after_claim_loss_failed",
                    extra={"run_id": str(result.run_id), "error": type(exc).__name__},
                )

    async def _validate_result_model_attribution(
        self,
        request: WorkerRunRequest,
        result: WorkerResult,
        model_resolution_snapshot_id: UUID | None,
    ) -> None:
        """Require model usage to match the immutable resolution snapshot.

        A governed worker may return arbitrary runtime output, but its
        provider/model usage is part of AIAT-owned accounting.  When a run
        carries a resolution snapshot, the result must report the exact
        provider and model selected by that snapshot before usage or terminal
        state is persisted.  Legacy/native runs without a snapshot retain
        their existing compatibility path.
        """
        if self.storage is None:
            return
        reference = request.resolved_model_profile
        effective_snapshot_id = model_resolution_snapshot_id or (
            reference.resolution_snapshot_id if reference is not None else None
        )
        if effective_snapshot_id is None:
            return
        try:
            normalized_snapshot_id = (
                effective_snapshot_id
                if isinstance(effective_snapshot_id, UUID)
                else UUID(str(effective_snapshot_id))
            )
        except (TypeError, ValueError) as exc:
            raise WorkerRunError(
                "MODEL_USAGE_ATTRIBUTION_INVALID",
                "model resolution snapshot ID is invalid",
            ) from exc
        getter = getattr(self.storage, "get_model_resolution_snapshot", None)
        if not callable(getter):
            raise WorkerRunError(
                "MODEL_USAGE_ATTRIBUTION_UNAVAILABLE",
                "model resolution snapshot storage is unavailable",
            )
        snapshot = await getter(
            normalized_snapshot_id,
            project_id=request.project_id,
        )
        if not isinstance(snapshot, dict):
            raise WorkerRunError(
                "MODEL_USAGE_ATTRIBUTION_SNAPSHOT_NOT_FOUND",
                "model resolution snapshot was not found for result attribution",
            )
        expected_provider = str(snapshot.get("provider_id") or "").strip()
        expected_model = str(snapshot.get("exact_model_id") or "").strip()
        if not expected_provider or not expected_model:
            raise WorkerRunError(
                "MODEL_USAGE_ATTRIBUTION_SNAPSHOT_INCOMPLETE",
                "model resolution snapshot lacks provider/model attribution",
            )
        observed_provider = str(result.usage.provider or "").strip()
        observed_model = str(result.usage.exact_model_id or "").strip()
        if observed_provider != expected_provider or observed_model != expected_model:
            raise WorkerRunError(
                "MODEL_USAGE_ATTRIBUTION_MISMATCH",
                "worker result usage does not match the resolved model snapshot",
                details={
                    "expected_provider_id": expected_provider,
                    "expected_exact_model_id": expected_model,
                    "observed_provider_id": observed_provider or None,
                    "observed_exact_model_id": observed_model or None,
                },
            )

    async def _mediate_tool_request(
        self,
        request: WorkerRunRequest,
        adapter: WorkerAdapter,
        event: WorkerEvent,
        *,
        claim_fence: dict[str, Any] | None = None,
    ) -> None:
        if event.tool_request is None:
            return
        tool_request = event.tool_request.model_copy(deep=False)
        fence = dict(claim_fence or request.execution_claim_fence)
        tool_request._execution_claim_fence = fence
        if tool_request.run_id != request.run_id:
            raise WorkerRunError(
                "TOOL_REQUEST_SCOPE_MISMATCH",
                "worker tool request does not match the controller run",
                details={
                    "controller_run_id": str(request.run_id),
                    "tool_request_run_id": str(tool_request.run_id),
                },
            )
        effect = await self._begin_tool_effect(tool_request, claim_fence=fence)
        disposition = str(effect.get("disposition") or "AMBIGUOUS")
        if disposition == "CLAIM_LOST":
            raise WorkerRunError(
                "WORKER_RUN_CLAIM_LOST",
                "worker run claim is no longer current for tool dispatch",
                details={"current_state": "CLAIM_FENCE_REJECTED"},
            )
        if disposition == "CONFLICT":
            response = WorkerToolResponse(
                request_id=tool_request.request_id,
                run_id=request.run_id,
                tool_name=tool_request.tool_name,
                success=False,
                error={
                    "code": "TOOL_IDEMPOTENCY_CONFLICT",
                    "message": "The tool idempotency key was reused with different request arguments",
                    "terminal": True,
                    "category": "policy",
                },
            )
        elif disposition in {"REPLAY", "AMBIGUOUS"}:
            stored_response = effect.get("response_json")
            if stored_response is not None:
                response = WorkerToolResponse.model_validate(stored_response).model_copy(
                    update={
                        "request_id": tool_request.request_id,
                        "run_id": request.run_id,
                        "tool_name": tool_request.tool_name,
                    }
                )
            else:
                response = WorkerToolResponse(
                    request_id=tool_request.request_id,
                    run_id=request.run_id,
                    tool_name=tool_request.tool_name,
                    success=False,
                    error={
                        "code": "TOOL_EFFECT_AMBIGUOUS",
                        "message": "A previous tool attempt may have performed an effect; automatic replay is blocked",
                        "retryable": False,
                        "terminal": True,
                        "category": "recovery",
                    },
                )
        else:
            effect_state = "COMPLETED"
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
                        # The tool may have completed an external effect before
                        # its response reached AIAT.  Record the ambiguity and
                        # do not allow a later controller to execute it again.
                        effect_state = "AMBIGUOUS"
                        response = WorkerToolResponse(
                            request_id=tool_request.request_id,
                            run_id=request.run_id,
                            tool_name=tool_request.tool_name,
                            success=False,
                            error={
                                "code": "TOOL_EFFECT_AMBIGUOUS",
                                "message": "Tool mediation failed; the effect outcome requires reconciliation before retry",
                                "retryable": False,
                                "terminal": True,
                                "category": "recovery",
                                "details": {"cause_type": type(exc).__name__},
                            },
                        )
            await self._complete_tool_effect(
                tool_request,
                effect,
                state=effect_state,
                response=response,
            )
        deliver = getattr(adapter, "deliver_tool_response", None)
        if deliver is None:
            raise WorkerRunError("TOOL_RESPONSE_UNSUPPORTED", "adapter does not support mediated tool responses")
        try:
            self._record_file_write_artifact(request, tool_request, response, effect)
        except WorkerRunError as exc:
            # The file effect may already have happened. Preserve its durable
            # effect row, but do not tell the worker it has a project artifact
            # unless the Tool Service supplied verifiable integrity metadata.
            # Delivering a bounded failure also lets the runtime exit cleanly
            # instead of waiting for its tool-response timeout.
            response = WorkerToolResponse(
                request_id=tool_request.request_id,
                run_id=request.run_id,
                tool_name=tool_request.tool_name,
                success=False,
                error={
                    "code": exc.code,
                    "message": str(exc),
                    "terminal": True,
                    "category": "artifact",
                },
            )
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
        skill_bundle_id: UUID | None = None,
        steward_id: UUID | None = None,
        model_resolution_snapshot_id: UUID | None = None,
        retry_of_run_id: UUID | None = None,
        retry_context_json: dict[str, Any] | None = None,
    ) -> WorkerRunOutcome:
        created_row = await self.create_run(
            request,
            worker_registry_id=worker_registry_id,
            worker_shell_version_id=worker_shell_version_id,
            adapter_id=adapter_id,
            skill_bundle_id=skill_bundle_id,
            steward_id=steward_id,
            model_resolution_snapshot_id=model_resolution_snapshot_id,
            retry_of_run_id=retry_of_run_id,
            retry_context_json=retry_context_json,
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
        execution_claim_fence = self._claim_fence(row)
        self._tool_artifacts_by_run[request.run_id] = {}
        request = request.model_copy(deep=False)
        request._execution_claim_fence = dict(execution_claim_fence)
        events: list[WorkerEvent] = []
        accepted: WorkerRunAccepted | None = None
        negotiation: dict[str, Any] | None = None
        readiness: WorkerReadiness | None = None
        result: WorkerResult | None = None
        try:
            async def ensure_claim_current() -> None:
                if not execution_claim_fence:
                    return
                current_row = await self.get_run(request.run_id)
                if not self._claim_is_current(current_row, execution_claim_fence):
                    raise WorkerRunError(
                        "WORKER_RUN_CLAIM_LOST",
                        "worker run claim is no longer current",
                        details={"current_state": str((current_row or {}).get("state") or "UNKNOWN")},
                    )

            async def transition_before_dispatch(
                target: str,
                *,
                expected: str,
                **kwargs: Any,
            ) -> dict[str, Any]:
                persisted = await self.transition(
                    request.run_id,
                    target,
                    expected=expected,
                    **execution_claim_fence,
                    **kwargs,
                )
                if persisted is not None:
                    return persisted
                current = await self.get_run(request.run_id)
                current_state = str((current or {}).get("state") or "UNKNOWN")
                if current_state == "CANCELLED":
                    raise WorkerRunError(
                        "CANCELLED",
                        "worker run was cancelled before runtime dispatch",
                        state="CANCELLED",
                    )
                raise WorkerRunError(
                    "RUN_STATE_CONFLICT",
                    "worker run state changed before runtime dispatch",
                    details={"expected_state": expected, "current_state": current_state},
                )

            current_state = str((await self.get_run(request.run_id) or {}).get("state") or "CREATED")
            await transition_before_dispatch("VALIDATING", expected=current_state)
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
            await transition_before_dispatch(
                "READY",
                expected="VALIDATING",
                negotiation=negotiation,
            )
            dispatch_lock = self._dispatch_locks.setdefault(request.run_id, asyncio.Lock())
            cancel_event = self._cancel_events.setdefault(request.run_id, asyncio.Event())
            self._dispatch_phase[request.run_id] = "NOT_STARTED"
            await transition_before_dispatch("DISPATCHING", expected="READY")
            async with dispatch_lock:
                dispatch_row = await self.get_run(request.run_id)
                if (
                    str((dispatch_row or {}).get("state") or "") == "CANCELLED"
                    or cancel_event.is_set()
                ):
                    raise WorkerRunError(
                        "CANCELLED",
                        "worker run was cancelled before adapter startup",
                        state="CANCELLED",
                    )
                # This marker is set immediately before entering the adapter.
                # A concurrent cancellation can still mark the adapter's
                # pending-cancel bit while start awaits readiness, but it may
                # no longer claim that no runtime startup was attempted.
                self._dispatch_phase[request.run_id] = "STARTING"
                accepted = await adapter.start(request)
                self._dispatch_phase[request.run_id] = "ACCEPTED"
            runtime_run_id = accepted.runtime_run_id if accepted else None
            accepted_metadata = accepted.metadata if accepted else {}
            if runtime_run_id is None and isinstance(accepted_metadata, dict):
                for key in ("runtime_run_id", "opencode_session_id", "external_run_id"):
                    candidate = accepted_metadata.get(key)
                    if candidate:
                        runtime_run_id = str(candidate)
                        break
            current_after_accept = await self.get_run(request.run_id) or {}
            runtime_binding = {
                "attempt_count": int(current_after_accept.get("attempt_count") or 0),
                "adapter_id": str(adapter_id) if adapter_id is not None else None,
                "runtime_type": str(getattr(adapter, "runtime_type", "unknown") or "unknown"),
                "runtime_run_id": runtime_run_id,
                "status": "RUNNING",
                # Only immutable, non-secret runtime identity metadata is
                # persisted here.  The legacy transition metadata continues
                # to retain its compatibility payload separately.
                "metadata": {
                    "runtime_version": str(getattr(adapter, "runtime_version", "") or ""),
                    "adapter_api_version": str(
                        getattr(adapter, "adapter_api_version", "") or ""
                    ),
                },
            }
            persisted_running = await self.transition(
                request.run_id,
                "RUNNING",
                expected="DISPATCHING",
                transition_metadata={
                    "runtime_run_id": runtime_run_id,
                    "accepted_metadata": accepted_metadata,
                    "runtime_binding": runtime_binding,
                },
                **execution_claim_fence,
            )
            if persisted_running is None:
                # The adapter may have started a process before this write
                # discovers that recovery reclaimed its lease. Stop this
                # attempt's runtime before consuming events or persisting
                # result evidence; its owner/attempt fence cannot settle the
                # canonical run.
                try:
                    cancellation = WorkerCancellation(
                        run_id=request.run_id,
                        reason="worker run claim lost after runtime startup",
                        requested_by="worker-run-controller",
                        force=True,
                    )
                    cancellation._execution_claim_fence = dict(execution_claim_fence)
                    cancellation._runtime_run_id = runtime_run_id
                    await adapter.cancel(cancellation)
                except Exception as exc:
                    logger.warning(
                        "worker_runtime_cancel_after_claim_loss_failed",
                        extra={"run_id": str(request.run_id), "error": type(exc).__name__},
                    )
                current_row = await self.get_run(request.run_id) or {}
                return WorkerRunOutcome(
                    run_id=request.run_id,
                    state=str(current_row.get("state") or "UNKNOWN"),
                    accepted=accepted,
                    readiness=readiness,
                    negotiation=negotiation,
                )

            async def collect_events() -> None:
                nonlocal result
                async for event in adapter.events(request.run_id):
                    if event.run_id != request.run_id or event.worker_id != request.worker_id:
                        raise WorkerRunError("EVENT_SCOPE_MISMATCH", "worker event does not match the requested run")
                    await ensure_claim_current()
                    if events and event.sequence < events[-1].sequence:
                        # Duplicate events are persisted idempotently only when
                        # their content is identical; the store enforces that.
                        await self.append_event(event)
                        continue
                    events.append(event)
                    await self.append_event(event)
                    if event.event_type == EventType.TOOL_REQUEST:
                        await ensure_claim_current()
                        await self._mediate_tool_request(
                            request,
                            adapter,
                            event,
                            claim_fence=execution_claim_fence,
                        )
                    if event.event_type == EventType.CHECKPOINT and event.checkpoint is not None and self.storage is not None:
                        await ensure_claim_current()
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
            result = self._attach_tool_artifacts(request, result)
            await self._validate_result_model_attribution(
                request,
                result,
                model_resolution_snapshot_id,
            )
            terminal = "SUCCEEDED" if result.success else "CANCELLED" if result.error and result.error.code == "CANCELLED" else "FAILED"
            terminal_kwargs = {
                "result": result.model_dump(mode="json"),
                "error": None if result.success else result.error.model_dump(mode="json") if result.error else None,
                "replay_metadata": result.replay_metadata,
                "reason": "worker emitted a normalized terminal result",
                "correlation_id": request.idempotency_key,
            }
            if terminal in {"SUCCEEDED", "FAILED"} and execution_claim_fence:
                await ensure_claim_current()
                completion_args = (
                    {"terminal_success": terminal_kwargs}
                    if terminal == "SUCCEEDED"
                    else {"terminal_failure": terminal_kwargs}
                )
                persisted_terminal = await self._persist_result_evidence(
                    request,
                    result,
                    adapter=adapter,
                    **completion_args,
                )
            else:
                if terminal != "CANCELLED":
                    await ensure_claim_current()
                    await self._persist_result_evidence(
                        request,
                        result,
                        adapter=adapter,
                    )
                persisted_terminal = await self.transition(
                    request.run_id,
                    terminal,
                    expected="RUNNING",
                    **execution_claim_fence,
                    **terminal_kwargs,
                )
                # A pause request owns RUNNING -> PAUSING, but it does not
                # make a concurrently emitted terminal result disappear.
                # Allow the result to settle the run from PAUSING before the
                # pause can write PAUSED, preserving the legacy unclaimed and
                # non-success lifecycle behavior.
                if persisted_terminal is None:
                    persisted_terminal = await self.transition(
                        request.run_id,
                        terminal,
                        expected="PAUSING",
                        **execution_claim_fence,
                        **terminal_kwargs,
                    )
            if persisted_terminal is None:
                current = await self.get_run(request.run_id)
                current_state = str((current or {}).get("state") or terminal)
                return WorkerRunOutcome(run_id=request.run_id, state=current_state, accepted=accepted, result=result, events=tuple(events), readiness=readiness, negotiation=negotiation)
            return WorkerRunOutcome(run_id=request.run_id, state=str(persisted_terminal["state"]), accepted=accepted, result=result, events=tuple(events), readiness=readiness, negotiation=negotiation)
        except TimeoutError:
            cancellation = WorkerCancellation(
                run_id=request.run_id,
                reason="worker run timed out",
                requested_by="worker-run-controller",
                force=True,
            )
            cancellation._execution_claim_fence = dict(execution_claim_fence)
            if runtime_run_id is not None:
                cancellation._runtime_run_id = str(runtime_run_id)
            await adapter.cancel(cancellation)
            persisted = await self.transition(
                request.run_id,
                "TIMED_OUT",
                expected="RUNNING",
                error={"code": "TIMEOUT", "message": "worker run exceeded timeout"},
                **execution_claim_fence,
            )
            current = persisted or await self.get_run(request.run_id) or {}
            return WorkerRunOutcome(run_id=request.run_id, state=str(current.get("state") or "TIMED_OUT"), accepted=accepted, events=tuple(events), readiness=readiness, negotiation=negotiation)
        except WorkerStartCancelled as exc:
            current_row = await self.get_run(request.run_id)
            current = str((current_row or {}).get("state") or "")
            if current not in TERMINAL_RUN_STATES:
                await self.transition(
                    request.run_id,
                    "CANCELLED",
                    expected=current,
                    error={"code": "CANCELLED", "message": str(exc)},
                    **execution_claim_fence,
                )
                current_row = await self.get_run(request.run_id)
                current = str((current_row or {}).get("state") or "")
            outcome_state = current or "CANCELLED"
            return WorkerRunOutcome(
                run_id=request.run_id,
                state=outcome_state,
                accepted=accepted,
                events=tuple(events),
                readiness=readiness,
                negotiation=negotiation,
            )
        except WorkerRunError as exc:
            current_row = await self.get_run(request.run_id)
            current = str((current_row or {}).get("state") or "")
            if current not in TERMINAL_RUN_STATES:
                target = "CANCELLED" if exc.state == "CANCELLED" else "FAILED"
                try:
                    await self.transition(
                        request.run_id,
                        target,
                        expected=str(current),
                        error={"code": exc.code, "message": str(exc), "details": exc.details},
                        **execution_claim_fence,
                    )
                except Exception:
                    logger.exception("Failed to persist worker run failure")
                current_row = await self.get_run(request.run_id)
                current = str((current_row or {}).get("state") or "")
            fallback_state = "CANCELLED" if exc.state == "CANCELLED" else "FAILED"
            outcome_state = current or fallback_state
            return WorkerRunOutcome(run_id=request.run_id, state=outcome_state, accepted=accepted, events=tuple(events), readiness=readiness, negotiation=negotiation)
        except Exception as exc:
            logger.exception("Unexpected worker run controller failure")
            current_row = await self.get_run(request.run_id) or {}
            current = current_row.get("state")
            if current not in TERMINAL_RUN_STATES:
                try:
                    persisted = await self.transition(
                        request.run_id,
                        "FAILED",
                        expected=str(current),
                        error={"code": "CONTROLLER_ERROR", "message": str(exc)},
                        **execution_claim_fence,
                    )
                    current_row = persisted or await self.get_run(request.run_id) or current_row
                except Exception:
                    logger.exception("Failed to persist unexpected worker run failure")
            return WorkerRunOutcome(run_id=request.run_id, state=str(current_row.get("state") or "FAILED"), accepted=accepted, events=tuple(events), readiness=readiness, negotiation=negotiation)
        finally:
            self._dispatch_phase.pop(request.run_id, None)
            self._tool_artifacts_by_run.pop(request.run_id, None)
            self._cancel_events.pop(request.run_id, None)
            self._dispatch_locks.pop(request.run_id, None)

    async def cancel(
        self,
        run_id: UUID,
        adapter: WorkerAdapter,
        *,
        reason: str,
        requested_by: str,
        force: bool = False,
        allow_unfenced_cancel: bool = False,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
    ) -> dict[str, Any] | None:
        row = await self.get_run(run_id)
        if row is None:
            return None
        if row.get("state") in TERMINAL_RUN_STATES:
            return row
        claim_fence: dict[str, Any] = {}
        if not allow_unfenced_cancel:
            current_owner = row.get("claim_owner")
            current_attempt = int(row.get("attempt_count") or 0)
            if current_owner is not None or current_attempt > 0:
                claim_fence = {
                    "claim_owner": claim_owner,
                    "attempt_count": attempt_count,
                }
                if (
                    claim_owner != current_owner
                    or attempt_count != current_attempt
                    or not self._claim_is_current(row, claim_fence)
                ):
                    raise WorkerRunError(
                        "WORKER_RUN_CLAIM_LOST",
                        "worker run cancellation claim is no longer current",
                        details={"current_state": str(row.get("state") or "UNKNOWN")},
                    )
        current = str(row.get("state"))
        cancel_event = self._cancel_events.get(run_id)
        if cancel_event is not None:
            cancel_event.set()
        pre_dispatch_states = {"QUEUED", "CLAIMED", "CREATED", "VALIDATING", "READY"}
        while current in pre_dispatch_states:
            cancelled = await self.transition(
                run_id,
                "CANCELLED",
                expected=current,
                error={"code": "CANCELLED", "message": reason},
                actor=requested_by,
                allow_unfenced_cancel=allow_unfenced_cancel,
                **claim_fence,
            )
            if cancelled is not None:
                return cancelled
            row = await self.get_run(run_id)
            if row is None or row.get("state") in TERMINAL_RUN_STATES:
                return row
            observed = str(row.get("state"))
            if observed == current:
                raise WorkerRunError(
                    "RUN_STATE_CONFLICT",
                    "worker run cancellation could not claim its current state",
                    details={"state": current},
                )
            current = observed
        if current == "DISPATCHING" and self._dispatch_phase.get(run_id) == "NOT_STARTED":
            # The controller knows it has persisted DISPATCHING but has not
            # entered adapter.start yet. Serialize this CAS against the
            # controller's transition to STARTING so cancellation can win
            # without asking an adapter to infer whether a remote process
            # exists (which would be unsafe after a controller restart).
            dispatch_lock = self._dispatch_locks.get(run_id)
            if dispatch_lock is not None:
                async with dispatch_lock:
                    row = await self.get_run(run_id)
                    if row is None or row.get("state") in TERMINAL_RUN_STATES:
                        return row
                    current = str(row.get("state"))
                    if (
                        current == "DISPATCHING"
                        and self._dispatch_phase.get(run_id) == "NOT_STARTED"
                    ):
                        cancelled = await self.transition(
                            run_id,
                            "CANCELLED",
                            expected="DISPATCHING",
                            error={"code": "CANCELLED", "message": reason},
                            actor=requested_by,
                            allow_unfenced_cancel=allow_unfenced_cancel,
                            **claim_fence,
                        )
                        if cancelled is not None:
                            return cancelled
                        row = await self.get_run(run_id)
                        if row is None or row.get("state") in TERMINAL_RUN_STATES:
                            return row
                        current = str(row.get("state"))
        if current not in {
            "DISPATCHING",
            "RUNNING",
            "PAUSING",
            "PAUSED",
            "RESUMING",
        }:
            return row
        cancellation = WorkerCancellation(
            run_id=run_id,
            reason=reason,
            requested_by=requested_by,
            force=force,
        )
        current_execution_fence = self._claim_fence(row)
        cancellation._execution_claim_fence = dict(current_execution_fence)
        runtime_run_id = row.get("runtime_run_id")
        binding_reader = getattr(self.storage, "get_worker_runtime_binding", None)
        if callable(binding_reader):
            try:
                binding = await binding_reader(
                    run_id,
                    attempt_count=int(row.get("attempt_count") or 0),
                )
            except TypeError:
                binding = await binding_reader(run_id)
            except Exception as exc:
                logger.warning(
                    "worker_runtime_binding_read_failed_during_cancel",
                    extra={"run_id": str(run_id), "error": type(exc).__name__},
                )
                binding = None
            if isinstance(binding, dict) and binding.get("runtime_run_id"):
                runtime_run_id = binding["runtime_run_id"]
        if runtime_run_id is not None:
            cancellation._runtime_run_id = str(runtime_run_id)
        raw_receipt = await adapter.cancel(cancellation)
        receipt: WorkerCancellationReceipt | None
        if raw_receipt is None:
            # Compatibility path for pre-receipt adapters.  Preserve the
            # historical state transition, but keep it documented as an
            # acknowledgement-only path until that adapter is upgraded.
            receipt = None
        elif isinstance(raw_receipt, WorkerCancellationReceipt):
            receipt = raw_receipt
        else:
            receipt = WorkerCancellationReceipt.model_validate(raw_receipt)

        if receipt is not None:
            if receipt.run_id != run_id:
                raise WorkerRunError(
                    "CANCELLATION_SCOPE_MISMATCH",
                    "worker cancellation receipt does not match the requested run",
                )
            if not receipt.terminal:
                # The runtime accepted the request but did not prove
                # termination. Leave the canonical run non-terminal; a later
                # CANCELLED event or reconciliation must settle it.
                return await self.get_run(run_id)
            if receipt.runtime_status not in {"CANCELLED", "CANCELED"}:
                # A runtime that is already completed/failed, or whose
                # terminal status is unknown, wins over a late cancellation
                # request. Reconciliation/normal result settlement owns the
                # terminal state in that case.
                return await self.get_run(run_id)
        if current in {"DISPATCHING", "RUNNING", "PAUSING", "PAUSED", "RESUMING"}:
            await self.transition(
                run_id,
                "CANCELLED",
                expected=current,
                error={"code": "CANCELLED", "message": reason},
                actor=requested_by,
                allow_unfenced_cancel=allow_unfenced_cancel,
                **claim_fence,
            )
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
        claim_fence = self._claim_fence(row)
        pausing = await self.transition(
            run_id,
            "PAUSING",
            expected="RUNNING",
            actor=requested_by,
            reason=reason,
            **claim_fence,
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
                **claim_fence,
            )
            raise WorkerRunError("PAUSE_FAILED", str(exc)) from exc
        paused = await self.transition(
            run_id,
            "PAUSED",
            expected="PAUSING",
            actor=requested_by,
            reason=reason,
            **claim_fence,
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
        claim_fence = self._claim_fence(row)
        resuming = await self.transition(
            run_id,
            "RESUMING",
            expected="PAUSED",
            actor=requested_by,
            reason="operator requested resume",
            transition_metadata={"checkpoint_id": str(checkpoint_id) if checkpoint_id else None},
            **claim_fence,
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
                **claim_fence,
            )
            raise WorkerRunError("RESUME_FAILED", str(exc)) from exc
        resumed = await self.transition(
            run_id,
            "RUNNING",
            expected="RESUMING",
            actor=requested_by,
            reason="runtime acknowledged resume",
            transition_metadata={"checkpoint_id": str(checkpoint_id) if checkpoint_id else None},
            **claim_fence,
        )
        if resumed is None:
            raise WorkerRunError("RUN_STATE_CONFLICT", "worker run state changed while resume was acknowledged")
        return resumed
