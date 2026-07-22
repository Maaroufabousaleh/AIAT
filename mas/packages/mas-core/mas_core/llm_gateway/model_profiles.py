"""Governed, immutable Model Profile definitions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelProfileStatus(StrEnum):
    DRAFT = "draft"
    CERTIFIED = "certified"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    BLOCKED = "blocked"


class PrivacyClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ModelProfileVersion(BaseModel):
    """One immutable approved provider/model mapping."""

    model_config = ConfigDict(extra="allow", frozen=True)

    version: str
    provider_id: str
    exact_model_id: str
    api_version: str | None = None
    capabilities: frozenset[str] = frozenset()
    context_window: int = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=0, ge=0)
    tool_calling: bool = False
    structured_output: bool = False
    vision: bool = False
    reasoning: bool = False
    streaming: bool = False
    embedding: bool = False
    cost_per_1k_input_usd: float = Field(default=0, ge=0)
    cost_per_1k_output_usd: float = Field(default=0, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_tokens_per_request: int | None = Field(default=None, ge=0)
    latency_target_ms: int | None = Field(default=None, ge=0)
    max_concurrency: int | None = Field(default=None, ge=1)
    privacy_class: PrivacyClass = PrivacyClass.INTERNAL
    regions: frozenset[str] = frozenset()
    local: bool = False
    status: ModelProfileStatus = ModelProfileStatus.DRAFT
    certified_at: datetime | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    provider_settings: dict[str, Any] = Field(default_factory=dict)
    compatibility_history: tuple[dict[str, Any], ...] = ()

    @field_validator("provider_id", "exact_model_id", "version")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model profile version values must not be blank")
        return value

    @model_validator(mode="after")
    def validate_model_id(self) -> "ModelProfileVersion":
        if self.exact_model_id.lower() in {"auto", "default", "latest"}:
            raise ValueError("unmanaged model routing values are not valid Model Profiles")
        return self

    def supports(self, required: set[str] | frozenset[str]) -> tuple[bool, list[str]]:
        missing = sorted(set(required) - set(self.capabilities))
        feature_map = {
            "tool_calling": self.tool_calling,
            "structured_output": self.structured_output,
            "vision": self.vision,
            "reasoning": self.reasoning,
            "streaming": self.streaming,
            "embedding": self.embedding,
            "local": self.local,
        }
        for feature, supported in feature_map.items():
            if feature in required and not supported:
                missing.append(feature)
        return not missing, sorted(set(missing))

    def estimate_cost(self, prompt_tokens: int, output_tokens: int) -> float:
        return (
            prompt_tokens / 1000 * self.cost_per_1k_input_usd
            + output_tokens / 1000 * self.cost_per_1k_output_usd
        )


class ModelProfile(BaseModel):
    """Logical profile with immutable versions and policy metadata."""

    model_config = ConfigDict(extra="allow")

    profile_id: str
    purpose: str
    approved_provider_ids: frozenset[str] = frozenset()
    versions: tuple[ModelProfileVersion, ...] = ()
    required_capabilities: frozenset[str] = frozenset()
    fallback_profile_ids: tuple[str, ...] = ()
    status: ModelProfileStatus = ModelProfileStatus.DRAFT
    owner: str = "aiat"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deprecation_reason: str | None = None

    @field_validator("profile_id", "purpose")
    @classmethod
    def profile_text(cls, value: str) -> str:
        value = value.strip()
        if not value or (value.lower() == "auto" and value == value):
            raise ValueError("Model Profile ID/purpose must be governed and non-blank")
        return value

    @model_validator(mode="after")
    def validate_versions(self) -> "ModelProfile":
        if len({version.version for version in self.versions}) != len(self.versions):
            raise ValueError("Model Profile versions must be unique")
        if self.approved_provider_ids:
            invalid = [version.provider_id for version in self.versions if version.provider_id not in self.approved_provider_ids]
            if invalid:
                raise ValueError("profile version provider is not in approved_provider_ids")
        return self

    def approved_versions(self, *, now: datetime | None = None) -> list[ModelProfileVersion]:
        at = now or datetime.now(UTC)
        return [
            version
            for version in self.versions
            if version.status == ModelProfileStatus.APPROVED
            and (version.effective_from is None or version.effective_from <= at)
            and (version.effective_until is None or version.effective_until > at)
        ]


class ModelPolicyConstraints(BaseModel):
    """A policy layer contributes constraints that are intersected."""

    model_config = ConfigDict(extra="allow")

    allowed_profile_ids: frozenset[str] | None = None
    allowed_provider_ids: frozenset[str] | None = None
    allowed_exact_model_ids: frozenset[str] | None = None
    denied_provider_ids: frozenset[str] = frozenset()
    denied_exact_model_ids: frozenset[str] = frozenset()
    required_capabilities: frozenset[str] = frozenset()
    minimum_context_window: int = Field(default=0, ge=0)
    maximum_cost_usd: float | None = Field(default=None, ge=0)
    maximum_tokens: int | None = Field(default=None, ge=0)
    privacy_class_at_most: PrivacyClass | None = None
    allowed_regions: frozenset[str] | None = None
    local_only: bool = False
    require_streaming: bool = False
    require_tool_calling: bool = False
    require_structured_output: bool = False
    require_vision: bool = False
    require_reasoning: bool = False

    def intersect(self, other: "ModelPolicyConstraints") -> "ModelPolicyConstraints":
        def intersection(left: frozenset[str] | None, right: frozenset[str] | None) -> frozenset[str] | None:
            if left is None:
                return right
            if right is None:
                return left
            return left & right

        privacy = self.privacy_class_at_most or other.privacy_class_at_most
        if self.privacy_class_at_most and other.privacy_class_at_most:
            order = list(PrivacyClass)
            privacy = min((self.privacy_class_at_most, other.privacy_class_at_most), key=order.index)
        max_cost = _minimum_optional(self.maximum_cost_usd, other.maximum_cost_usd)
        max_tokens = _minimum_optional(self.maximum_tokens, other.maximum_tokens)
        return ModelPolicyConstraints(
            allowed_profile_ids=intersection(self.allowed_profile_ids, other.allowed_profile_ids),
            allowed_provider_ids=intersection(self.allowed_provider_ids, other.allowed_provider_ids),
            allowed_exact_model_ids=intersection(self.allowed_exact_model_ids, other.allowed_exact_model_ids),
            denied_provider_ids=self.denied_provider_ids | other.denied_provider_ids,
            denied_exact_model_ids=self.denied_exact_model_ids | other.denied_exact_model_ids,
            required_capabilities=self.required_capabilities | other.required_capabilities,
            minimum_context_window=max(self.minimum_context_window, other.minimum_context_window),
            maximum_cost_usd=max_cost,
            maximum_tokens=max_tokens,
            privacy_class_at_most=privacy,
            allowed_regions=intersection(self.allowed_regions, other.allowed_regions),
            local_only=self.local_only or other.local_only,
            require_streaming=self.require_streaming or other.require_streaming,
            require_tool_calling=self.require_tool_calling or other.require_tool_calling,
            require_structured_output=self.require_structured_output or other.require_structured_output,
            require_vision=self.require_vision or other.require_vision,
            require_reasoning=self.require_reasoning or other.require_reasoning,
        )


class ModelPolicyLayer(BaseModel):
    name: str
    constraints: ModelPolicyConstraints = Field(default_factory=ModelPolicyConstraints)
    preferred_profile_ids: tuple[str, ...] = ()
    preferred_exact_model_ids: tuple[str, ...] = ()


def _minimum_optional(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


class ModelResolutionRequest(BaseModel):
    """Inputs for deterministic hierarchical profile resolution."""

    task_type: str
    requested_profile_id: str | None = None
    layers: tuple[ModelPolicyLayer, ...] = ()
    worker_required_capabilities: frozenset[str] = frozenset()
    steward_required_capabilities: frozenset[str] = frozenset()
    task_required_capabilities: frozenset[str] = frozenset()
    adapter_required_capabilities: frozenset[str] = frozenset()
    prompt_tokens: int = Field(default=0, ge=0)
    expected_output_tokens: int = Field(default=0, ge=0)
    budget_usd: float | None = Field(default=None, ge=0)
    override_approval_id: UUID | None = None
    requested_raw_model_id: str | None = None

    @model_validator(mode="after")
    def reject_raw_route(self) -> "ModelResolutionRequest":
        if self.requested_raw_model_id:
            raise ValueError("raw model IDs are not accepted; request a governed Model Profile")
        if not self.task_type.strip():
            raise ValueError("task_type must not be blank")
        return self


class RejectedModelCandidate(BaseModel):
    profile_id: str
    version: str | None = None
    exact_model_id: str | None = None
    reasons: tuple[str, ...]


class ModelResolutionSnapshot(BaseModel):
    """Immutable evidence of one model decision."""

    model_config = ConfigDict(frozen=True, extra="allow")

    snapshot_id: UUID = Field(default_factory=uuid4)
    requested_profile_id: str | None = None
    resolved_profile_id: str | None = None
    resolved_profile_version: str | None = None
    provider_id: str | None = None
    exact_model_id: str | None = None
    api_version: str | None = None
    effective_constraints: ModelPolicyConstraints
    effective_configuration: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: frozenset[str] = frozenset()
    capability_checks: dict[str, bool] = Field(default_factory=dict)
    rejected_candidates: tuple[RejectedModelCandidate, ...] = ()
    fallback_chain: tuple[str, ...] = ()
    fallback_decisions: tuple[dict[str, Any], ...] = ()
    cost_estimate_usd: float = 0
    override_approval_id: UUID | None = None
    selection_reason: str = ""
    policy_failure_code: str | None = None
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def authorized(self) -> bool:
        return self.policy_failure_code is None and self.exact_model_id is not None


class ModelResolutionError(ValueError):
    """Typed model-policy failure."""

    def __init__(self, code: str, message: str, *, rejected_candidates: list[RejectedModelCandidate] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.rejected_candidates = rejected_candidates or []
