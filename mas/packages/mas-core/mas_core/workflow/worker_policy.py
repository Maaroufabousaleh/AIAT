"""Typed task-node execution policy used by the flow validator/runtime."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetryStrategy(StrEnum):
    SAME_VERSION = "same_version"
    FALLBACK_MODEL = "fallback_model"
    ALTERNATE_ADAPTER = "alternate_adapter"
    CHECKPOINT = "checkpoint"
    LAST_SAFE_NODE = "last_safe_node"
    ESCALATE = "escalate"
    TERMINAL_FAILURE = "terminal_failure"


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=20)
    strategies: tuple[RetryStrategy, ...] = (RetryStrategy.SAME_VERSION,)
    backoff_seconds: float = Field(default=0, ge=0, le=86_400)
    idempotency_scope: str = "worker_run"


class CancellationPolicy(BaseModel):
    cooperative: bool = True
    force_after_seconds: int | None = Field(default=None, ge=1)
    orphan_reconciliation: str = "restart_or_escalate"


class CheckpointPolicy(BaseModel):
    mode: str = "unsupported"
    required: bool = False
    resume_from_last_safe_node: bool = False


class EscalationPolicy(BaseModel):
    target_worker_id: str | None = None
    target_team_id: str | None = None
    required_capabilities: tuple[str, ...] = ()
    reason_template: str | None = None

    @model_validator(mode="after")
    def target_required(self) -> "EscalationPolicy":
        if not self.target_worker_id and not self.target_team_id:
            raise ValueError("escalation requires target_worker_id or target_team_id")
        return self


class ArtifactExpectation(BaseModel):
    name: str
    kind: str = "other"
    required: bool = True
    mime_types: tuple[str, ...] = ()


class TaskNodePolicy(BaseModel):
    """Modern typed configuration; legacy action/team fields remain optional."""

    model_config = ConfigDict(extra="allow")

    worker_id: str | None = None
    team_id: str | None = None
    runtime_type: str | None = None
    adapter_version: str | None = None
    steward_id: str | None = None
    skill_bundle_version: str | None = None
    model_profile_id: str | None = None
    model_mode: str = "aiat_gateway"
    task_type: str | None = None
    required_capabilities: tuple[str, ...] = ()
    permission_requirements: tuple[str, ...] = ()
    project_workspace_mode: str = "isolated"
    tool_grants: tuple[str, ...] = ()
    budget: dict[str, float] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=1)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    cancellation_policy: CancellationPolicy = Field(default_factory=CancellationPolicy)
    checkpoint_policy: CheckpointPolicy = Field(default_factory=CheckpointPolicy)
    escalation_policy: EscalationPolicy | None = None
    artifact_expectations: tuple[ArtifactExpectation, ...] = ()
    completion_criteria: dict[str, Any] = Field(default_factory=dict)
    runtime_extensions: dict[str, Any] = Field(default_factory=dict)
    # Legacy fields accepted for migration; they are not model selection.
    action: str | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> "TaskNodePolicy":
        if not self.worker_id and not self.team_id and not self.action:
            raise ValueError("task requires a worker_id, team_id, or legacy action")
        if self.worker_id and self.team_id:
            raise ValueError("task assignment must select worker_id or team_id, not both")
        if self.model_mode not in {"none", "aiat_gateway", "certified_external_runtime", "hybrid"}:
            raise ValueError("invalid task model_mode")
        if self.model_mode != "none" and self.worker_id and not self.model_profile_id:
            raise ValueError("model-governed task workers require model_profile_id")
        if self.model_mode == "none" and self.model_profile_id:
            raise ValueError("model_mode none does not allow a model_profile_id")
        forbidden_raw = {"model", "model_id", "raw_model", "provider_model"}
        present = forbidden_raw & set(self.model_extra or {})
        if present:
            raise ValueError("raw model IDs are not permitted; use model_profile_id")
        return self


def validate_task_policy(config: dict[str, Any]) -> list[str]:
    try:
        TaskNodePolicy.model_validate(config)
    except ValueError as exc:
        return [str(error.get("msg", error)) for error in exc.errors()] if hasattr(exc, "errors") else [str(exc)]
    return []
