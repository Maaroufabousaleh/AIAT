"""Versioned wire models for AIAT worker execution.

These models intentionally use plain JSON-compatible values at the boundary.
Runtime-specific details belong under ``extensions`` and never become a source
of authority for permissions, budgets, model selection, or project state.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "aiat.worker.v1"
ADAPTER_API_VERSION = "aiat.adapter.v1"
SKILL_BUNDLE_FORMAT_VERSION = "aiat.skill-bundle.v1"
_TRACE_CONTEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ContractError(ValueError):
    """Raised when a contract payload cannot be safely accepted."""


class ProtocolVersion(BaseModel):
    """Protocol metadata persisted on every request, event, and snapshot."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    schema_version: str = "1.0"
    adapter_api_version: str = ADAPTER_API_VERSION
    runtime_api_version: str | None = None
    skill_bundle_format_version: str = SKILL_BUNDLE_FORMAT_VERSION
    capability_snapshot_version: str | None = None

    @field_validator("contract_version", "schema_version", "adapter_api_version", "skill_bundle_format_version")
    @classmethod
    def non_blank_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("version values must not be blank")
        return value.strip()


class ModelMode(StrEnum):
    NONE = "none"
    AIAT_GATEWAY = "aiat_gateway"
    CERTIFIED_EXTERNAL_RUNTIME = "certified_external_runtime"
    HYBRID = "hybrid"


class CheckpointMode(StrEnum):
    NATIVE = "native"
    WRAPPER = "wrapper"
    RESTART_ONLY = "restart_only"
    UNSUPPORTED = "unsupported"


class CancellationMode(StrEnum):
    IMMEDIATE = "immediate"
    COOPERATIVE = "cooperative"
    AFTER_CURRENT_STEP = "after_current_step"


class StreamingMode(StrEnum):
    EVENT_STREAM = "event_stream"
    POLLING = "polling"
    FINAL_ONLY = "final_only"


class ToolMode(StrEnum):
    AIAT_MEDIATED = "aiat_mediated"
    CERTIFIED_NATIVE_BRIDGE = "certified_native_bridge"


class MemoryMode(StrEnum):
    AIAT = "aiat"
    RUNTIME_NATIVE = "runtime_native"
    HYBRID = "hybrid"


class WorkspaceMode(StrEnum):
    ISOLATED = "isolated"
    SHARED_READONLY = "shared_readonly"
    APPROVED_WRITE = "approved_write"


class ArtifactKind(StrEnum):
    FILE = "file"
    DOCUMENT = "document"
    REPORT = "report"
    LOG = "log"
    CHECKPOINT = "checkpoint"
    OTHER = "other"


class EventType(StrEnum):
    ACCEPTED = "accepted"
    STARTED = "started"
    PROGRESS = "progress"
    TOOL_REQUEST = "tool_request"
    TOOL_RESPONSE = "tool_response"
    CHECKPOINT = "checkpoint"
    PAUSED = "paused"
    RESUMED = "resumed"
    RESULT = "result"
    ERROR = "error"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    AUDIT = "audit"
    HEARTBEAT = "heartbeat"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RuntimeExtension(_ContractModel):
    """Namespaced runtime extension; unknown optional fields are preserved."""

    namespace: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or not re.fullmatch(r"[a-z][a-z0-9_.-]*", value):
            raise ValueError("extension namespace must be a lowercase namespaced identifier")
        return value


class WorkerIdentity(_ContractModel):
    worker_id: str
    shell_version: str = "1.0.0"
    name: str
    department: str | None = None
    organizational_role: str | None = None
    lifecycle_state: str = "INACTIVE"
    steward_id: UUID | None = None
    active_adapter_version: str | None = None
    active_skill_bundle_id: UUID | None = None

    @field_validator("worker_id", "name")
    @classmethod
    def identity_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("worker identity values must not be blank")
        return value.strip()


class WorkerCapabilities(_ContractModel):
    checkpoint_mode: CheckpointMode = CheckpointMode.UNSUPPORTED
    cancellation_mode: CancellationMode = CancellationMode.COOPERATIVE
    streaming_mode: StreamingMode = StreamingMode.FINAL_ONLY
    tool_mode: ToolMode = ToolMode.AIAT_MEDIATED
    memory_mode: MemoryMode = MemoryMode.AIAT
    workspace_mode: WorkspaceMode = WorkspaceMode.ISOLATED
    model_mode: ModelMode = ModelMode.NONE
    capability_names: list[str] = Field(default_factory=list)
    required_model_capabilities: set[str] = Field(default_factory=set)
    supports_health: bool = True
    supports_readiness: bool = True
    supports_usage: bool = True
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capability_names")
    @classmethod
    def normalize_capabilities(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        return list(dict.fromkeys(normalized))


class CapabilityRequirement(_ContractModel):
    name: str
    minimum_version: str | None = None
    required: bool = True
    reason: str | None = None

    @field_validator("name")
    @classmethod
    def requirement_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("capability requirement name must not be blank")
        return value


# Alias matching the architecture document's terminology.
WorkerCapabilityRequirement = CapabilityRequirement


class ModelProfileReference(_ContractModel):
    profile_id: str
    version: str | None = None
    exact_model_id: str | None = None
    resolution_snapshot_id: UUID | None = None

    @field_validator("profile_id")
    @classmethod
    def profile_not_blank(cls, value: str) -> str:
        if not value.strip() or value.strip().lower() == "auto":
            raise ValueError("a governed model profile ID is required")
        return value.strip()


class WorkerManifest(_ContractModel):
    """Specialist Shell declaration used by the universal adapter factory."""

    protocol: ProtocolVersion = Field(default_factory=ProtocolVersion)
    identity: WorkerIdentity
    capabilities: WorkerCapabilities = Field(default_factory=WorkerCapabilities)
    capability_requirements: list[CapabilityRequirement] = Field(default_factory=list)
    model_requirements: set[str] = Field(default_factory=set)
    permissions: list[str] = Field(default_factory=list)
    tool_grants: list[str] = Field(default_factory=list)
    budget_limits: dict[str, float] = Field(default_factory=dict)
    sandbox_profile: str = "standard"
    transport: str = "native"
    adapter_type: str = "native"
    source_provenance: dict[str, Any] = Field(default_factory=dict)
    active_adapter_version: str | None = None
    active_skill_bundle_id: UUID | None = None
    model_profile: ModelProfileReference | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model_requirements(self) -> "WorkerManifest":
        if self.capabilities.model_mode == ModelMode.NONE and self.model_profile is not None:
            raise ValueError("model_profile is not allowed when model_mode is none")
        return self


class WorkerRunRequest(_ContractModel):
    protocol: ProtocolVersion = Field(default_factory=ProtocolVersion)
    run_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str
    worker_id: str
    task_type: str
    task_input: dict[str, Any] = Field(default_factory=dict)
    project_id: UUID | None = None
    flow_id: UUID | None = None
    flow_instance_id: UUID | None = None
    flow_node_execution_id: int | None = None
    requested_model_profile: ModelProfileReference | None = None
    resolved_model_profile: ModelProfileReference | None = None
    capability_requirements: list[CapabilityRequirement] = Field(default_factory=list)
    tool_grants: list[str] = Field(default_factory=list)
    permission_requirements: list[str] = Field(default_factory=list)
    workspace_mode: WorkspaceMode = WorkspaceMode.ISOLATED
    timeout_seconds: int | None = Field(default=None, ge=1)
    budget: dict[str, float] = Field(default_factory=dict)
    checkpoint_policy: dict[str, Any] = Field(default_factory=dict)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)
    # Safe correlation metadata is carried separately from task input so it
    # can be persisted on model/artifact evidence without exposing payloads.
    trace_id: str | None = None
    span_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("idempotency_key", "worker_id", "task_type")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("request identity values must not be blank")
        return value

    @field_validator("trace_id", "span_id")
    @classmethod
    def safe_trace_context(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _TRACE_CONTEXT_RE.fullmatch(normalized):
            raise ValueError("trace and span identifiers must be bounded safe values")
        return normalized


class WorkerRunAccepted(_ContractModel):
    protocol: ProtocolVersion = Field(default_factory=ProtocolVersion)
    run_id: UUID
    idempotency_key: str
    worker_id: str
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    runtime_run_id: str | None = None
    initial_state: str = "READY"
    negotiated_capabilities: WorkerCapabilities | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerProgress(_ContractModel):
    percent: float | None = Field(default=None, ge=0, le=100)
    message: str | None = None
    phase: str | None = None
    current_step: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerToolRequest(_ContractModel):
    request_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    permission_scope: list[str] = Field(default_factory=list)
    approval_required: bool = False
    idempotency_key: str


class WorkerToolResponse(_ContractModel):
    request_id: UUID
    run_id: UUID
    tool_name: str
    success: bool
    result: Any = None
    error: "WorkerError | None" = None
    usage: "WorkerUsage | None" = None


class WorkerArtifact(_ContractModel):
    artifact_id: str | None = None
    kind: ArtifactKind = ArtifactKind.OTHER
    name: str
    uri: str
    sha256: str
    size_bytes: int | None = Field(default=None, ge=0)
    mime_type: str | None = None
    retention_class: str = "project_default"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
            raise ValueError("artifact sha256 must be a 64-character hexadecimal digest")
        return value.lower()


class WorkerError(_ContractModel):
    code: str
    message: str
    retryable: bool = False
    terminal: bool = False
    category: str = "runtime"
    details: dict[str, Any] = Field(default_factory=dict)
    cause_type: str | None = None


class WorkerUsage(_ContractModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    duration_ms: float = Field(default=0, ge=0)
    cpu_seconds: float | None = Field(default=None, ge=0)
    memory_bytes: int | None = Field(default=None, ge=0)
    provider: str | None = None
    exact_model_id: str | None = None

    @model_validator(mode="after")
    def derive_total_tokens(self) -> "WorkerUsage":
        if self.total_tokens == 0 and (self.prompt_tokens or self.completion_tokens):
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


class WorkerResult(_ContractModel):
    protocol: ProtocolVersion = Field(default_factory=ProtocolVersion)
    run_id: UUID
    worker_id: str
    success: bool
    output: Any = None
    artifacts: list[WorkerArtifact] = Field(default_factory=list)
    usage: WorkerUsage = Field(default_factory=WorkerUsage)
    error: WorkerError | None = None
    completion_criteria: dict[str, Any] = Field(default_factory=dict)
    replay_metadata: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def result_error_consistency(self) -> "WorkerResult":
        if self.success and self.error is not None:
            raise ValueError("successful worker results cannot contain an error")
        if not self.success and self.error is None:
            raise ValueError("failed worker results require a structured error")
        return self


class WorkerCheckpoint(_ContractModel):
    protocol: ProtocolVersion = Field(default_factory=ProtocolVersion)
    checkpoint_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int = Field(default=0, ge=0)
    state: dict[str, Any] = Field(default_factory=dict)
    artifact: WorkerArtifact | None = None
    resumable: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkerCancellation(_ContractModel):
    run_id: UUID
    reason: str
    requested_by: str
    force: bool = False
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkerPause(_ContractModel):
    run_id: UUID
    reason: str
    requested_by: str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkerResume(_ContractModel):
    run_id: UUID
    requested_by: str
    checkpoint_id: UUID | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkerHealth(_ContractModel):
    worker_id: str
    healthy: bool
    status: str
    runtime_version: str | None = None
    adapter_version: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)


class WorkerReadiness(_ContractModel):
    worker_id: str
    ready: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkerAuditEvent(_ContractModel):
    audit_id: UUID = Field(default_factory=uuid4)
    run_id: UUID | None = None
    worker_id: str | None = None
    action: str
    actor: str
    outcome: str = "accepted"
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkerEvent(_ContractModel):
    protocol: ProtocolVersion = Field(default_factory=ProtocolVersion)
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    worker_id: str
    sequence: int = Field(default=0, ge=0)
    event_type: EventType
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    idempotency_key: str | None = None
    progress: WorkerProgress | None = None
    tool_request: WorkerToolRequest | None = None
    tool_response: WorkerToolResponse | None = None
    checkpoint: WorkerCheckpoint | None = None
    result: WorkerResult | None = None
    error: WorkerError | None = None
    usage: WorkerUsage | None = None
    audit: WorkerAuditEvent | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload_for_event(self) -> "WorkerEvent":
        payload_by_type = {
            EventType.PROGRESS: self.progress,
            EventType.TOOL_REQUEST: self.tool_request,
            EventType.TOOL_RESPONSE: self.tool_response,
            EventType.CHECKPOINT: self.checkpoint,
            EventType.RESULT: self.result,
            EventType.ERROR: self.error,
            EventType.AUDIT: self.audit,
        }
        payload = payload_by_type.get(self.event_type)
        if payload is None and self.event_type in payload_by_type:
            raise ValueError(f"{self.event_type.value} event requires its structured payload")
        if self.result is not None and self.result.run_id != self.run_id:
            raise ValueError("result run_id does not match event run_id")
        if self.checkpoint is not None and self.checkpoint.run_id != self.run_id:
            raise ValueError("checkpoint run_id does not match event run_id")
        return self


class ProtocolEnvelope(_ContractModel):
    """A small helper for carrying a typed contract payload in a message."""

    protocol: ProtocolVersion = Field(default_factory=ProtocolVersion)
    message_type: Literal[
        "WorkerRunRequest",
        "WorkerRunAccepted",
        "WorkerEvent",
        "WorkerResult",
        "WorkerCancellation",
        "WorkerPause",
        "WorkerResume",
    ]
    payload: dict[str, Any]
