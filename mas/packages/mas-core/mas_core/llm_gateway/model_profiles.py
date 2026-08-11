"""Governed, immutable Model Profile definitions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Iterable


class ModelProfileStatus(StrEnum):
    DRAFT = "draft"
    CERTIFIED = "certified"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    BLOCKED = "blocked"


MODEL_PROFILE_CATALOGUE_SCHEMA = "aiat.model-profile-catalogue.v1"


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
    def validate_model_id(self) -> ModelProfileVersion:
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
    def validate_versions(self) -> ModelProfile:
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


def build_model_profile_catalogue(
    profiles: Iterable[ModelProfile],
    registry: Any,
) -> dict[str, Any]:
    """Build a deterministic registry/profile reconciliation catalogue.

    The provider registry is the available-model declaration; persisted Model
    Profiles are the governed approval layer. This report keeps those concerns
    separate: an unprofiled registry model is visible as ``profile_pending``
    rather than silently becoming an approved route, while a profile version
    that names an unknown or differently-owned model is retained as a finding.

    No licence or redistribution field participates in this report. It is an
    operational identity/capability reconciliation surface only.
    """
    profile_list = sorted(profiles, key=lambda profile: profile.profile_id)
    bindings: dict[tuple[str, str], list[dict[str, Any]]] = {}
    profile_version_count = 0
    for profile in profile_list:
        for version in sorted(profile.versions, key=lambda item: item.version):
            profile_version_count += 1
            binding = {
                "profile_id": profile.profile_id,
                "profile_status": profile.status.value,
                "version": version.version,
                "version_status": version.status.value,
                "provider_id": version.provider_id,
                "exact_model_id": version.exact_model_id,
            }
            bindings.setdefault((version.provider_id, version.exact_model_id), []).append(binding)

    entries: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    covered_version_keys: set[tuple[str, str, str, str]] = set()
    registry_model_ids: set[str] = set()
    for model in sorted(registry.list_models(), key=lambda item: (item.provider, item.model_id)):
        registry_model_ids.add(model.model_id)
        model_bindings = sorted(
            bindings.get((model.provider, model.model_id), []),
            key=lambda item: (item["profile_id"], item["version"]),
        )
        for binding in model_bindings:
            covered_version_keys.add(
                (
                    binding["profile_id"],
                    binding["version"],
                    binding["provider_id"],
                    binding["exact_model_id"],
                )
            )
        if not model_bindings:
            profile_state = "profile_pending"
        elif any(item["profile_status"] == ModelProfileStatus.APPROVED.value and item["version_status"] == ModelProfileStatus.APPROVED.value for item in model_bindings):
            profile_state = "approved_profile_present"
        else:
            profile_state = "profile_present_not_approved"
        entries.append(
            {
                "model_id": model.model_id,
                "provider_id": model.provider,
                "api_style": model.api_style.value,
                "description": model.description,
                "profile_state": profile_state,
                "profile_bindings": model_bindings,
                "capabilities": {
                    **model.capabilities.model_dump(mode="json"),
                    "tool_calling": model.supports_tools,
                    "streaming": model.supports_streaming,
                },
                "max_context_tokens": model.max_context_tokens,
                "cost_per_1m_input": model.cost_per_1m_input,
                "cost_per_1m_output": model.cost_per_1m_output,
                "best_for": sorted(model.best_for),
                "limits": sorted(model.limits),
            }
        )

    # Keep stale/unknown profile versions visible rather than dropping them.
    for profile in profile_list:
        for version in sorted(profile.versions, key=lambda item: item.version):
            key = (profile.profile_id, version.version, version.provider_id, version.exact_model_id)
            if key in covered_version_keys:
                continue
            registered_provider_ids = sorted(
                {
                    item.provider
                    for item in registry.list_models()
                    if item.model_id == version.exact_model_id
                }
            )
            finding_code = (
                "PROFILE_PROVIDER_MISMATCH"
                if registered_provider_ids
                else "PROFILE_MODEL_NOT_REGISTERED"
            )
            findings.append(
                {
                    "code": finding_code,
                    "profile_id": profile.profile_id,
                    "version": version.version,
                    "provider_id": version.provider_id,
                    "exact_model_id": version.exact_model_id,
                    "registered_provider_ids": registered_provider_ids,
                }
            )
            entries.append(
                {
                    "model_id": version.exact_model_id,
                    "provider_id": version.provider_id,
                    "api_style": None,
                    "description": "Persisted Model Profile version is not present in the runtime registry",
                    "profile_state": "profile_not_registered",
                    "profile_bindings": [
                        {
                            "profile_id": profile.profile_id,
                            "profile_status": profile.status.value,
                            "version": version.version,
                            "version_status": version.status.value,
                            "provider_id": version.provider_id,
                            "exact_model_id": version.exact_model_id,
                        }
                    ],
                    "capabilities": {},
                    "max_context_tokens": version.context_window,
                    "cost_per_1m_input": version.cost_per_1k_input_usd * 1000,
                    "cost_per_1m_output": version.cost_per_1k_output_usd * 1000,
                    "best_for": [],
                    "limits": [],
                }
            )

    entries.sort(key=lambda item: (item["provider_id"], item["model_id"], item["profile_state"]))
    findings.sort(key=lambda item: (item["code"], item["provider_id"], item["exact_model_id"], item["profile_id"], item["version"]))
    duplicate_bindings = sorted(
        [
            {
                "provider_id": provider_id,
                "exact_model_id": exact_model_id,
                "binding_count": len(items),
                "profiles": [f"{item['profile_id']}:{item['version']}" for item in items],
            }
            for (provider_id, exact_model_id), items in bindings.items()
            if len(items) > 1
        ],
        key=lambda item: (item["provider_id"], item["exact_model_id"]),
    )
    return {
        "schema_version": MODEL_PROFILE_CATALOGUE_SCHEMA,
        "registry_model_count": len(registry_model_ids),
        "profile_count": len(profile_list),
        "profile_version_count": profile_version_count,
        "covered_profile_version_count": len(covered_version_keys),
        "profile_pending_model_count": sum(item["profile_state"] == "profile_pending" for item in entries),
        "duplicate_profile_bindings": duplicate_bindings,
        "findings": findings,
        "entries": entries,
    }


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

    def intersect(self, other: ModelPolicyConstraints) -> ModelPolicyConstraints:
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
    def reject_raw_route(self) -> ModelResolutionRequest:
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
