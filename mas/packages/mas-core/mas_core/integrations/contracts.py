"""Stable DTOs shared by the orchestrator, gateway, and provider adapters."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def validate_credential_references(value: Any) -> dict[str, Any]:
    """Reject provider secret material anywhere in connection configuration.

    References and explicitly test-only fixtures are allowed; arbitrary
    nested token/private-key/password fields are not.  This validator is shared
    by the HTTP request model and storage boundary so callers cannot bypass the
    credential rule by invoking storage directly.
    """
    secret_markers = ("secret", "token", "private_key", "password", "api_key")
    reference_suffixes = ("_ref", "_refs", "_test_only")

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                name = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if any(marker in name for marker in secret_markers) and not name.endswith(reference_suffixes):
                    raise ValueError(f"{child_path} must be a credential reference, not secret material")
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    result = dict(value or {})
    walk(result, "")
    return result


class ConnectionStatus(StrEnum):
    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    READ_ONLY = "READ_ONLY"
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"


class ProjectionStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    CONFLICTED = "conflicted"
    FAILED = "failed"


class LifecyclePlanStatus(StrEnum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class CanaryPlanStatus(StrEnum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    ROLLED_BACK = "ROLLED_BACK"


class LifecyclePlanError(RuntimeError):
    """Typed fail-closed error returned by lifecycle plan operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ObjectType(StrEnum):
    PROJECT = "project"
    SPRINT = "sprint"
    WORK_ITEM = "work_item"
    COMMENT = "comment"
    REPOSITORY = "repository"
    PULL_REQUEST = "pull_request"
    CHECK = "check"


class IntegrationActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    # Providers do not always include a stable user identifier in every
    # webhook.  Login/email are retained as resolution evidence only; ACTIVE
    # authorization requires ``actor_id`` to be immutable or server-enriched
    # from the authenticated provider API.
    immutable_actor_id: bool = False
    provider_login: str | None = None
    provider_email: str | None = None
    team_id: str | None = None
    role: str | None = None
    run_id: UUID | None = None
    approval_id: UUID | None = None
    evidence_id: str | None = None


class ProviderConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    provider_kind: str
    display_name: str
    base_url: str
    credential_ref: str
    capability_profile: str = "pm"
    config: dict[str, Any] = Field(default_factory=dict)
    status: ConnectionStatus = ConnectionStatus.DISABLED
    schema_version: int = Field(default=1, ge=1)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query, or fragment")
        return value.rstrip("/")

    @field_validator("credential_ref")
    @classmethod
    def validate_credential_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("credential_ref must not be blank")
        if any(
            marker in normalized
            for marker in ("ghp_", "github_pat_", "sk-", "-----BEGIN", "eyJ")
        ):
            raise ValueError("credential_ref must identify a managed secret, not contain secret material")
        if any(char.isspace() for char in normalized):
            raise ValueError("credential_ref must not contain whitespace")
        return normalized


class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_kind: str
    adapter_version: str
    work_management: bool = False
    source_control: bool = False
    projects: bool = False
    iterations: bool = False
    work_items: bool = False
    comments: bool = False
    links: bool = False
    repositories: bool = False
    pull_requests: bool = False
    checks: bool = False
    webhooks: bool = False
    incremental_sync: bool = False
    supported_fields: frozenset[str] = frozenset()


class BootstrapAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    resource: str
    desired: dict[str, Any] = Field(default_factory=dict)
    current: dict[str, Any] | None = None
    destructive: bool = False
    manual: bool = False
    reason: str | None = None


class BootstrapPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(default_factory=uuid4)
    connection_id: UUID
    provider_kind: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actions: list[BootstrapAction] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    rollback_actions: list[str] = Field(default_factory=list)

    @property
    def ready_to_apply(self) -> bool:
        return not self.blockers and not any(action.destructive for action in self.actions)

    def digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"generated_at"})
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class BootstrapApplyResult(BaseModel):
    """Provider-side effects produced by applying one exact bootstrap plan.

    The original plan is returned unchanged so its approved digest remains
    stable.  Resource records are deliberately provider-neutral and contain
    identifiers/metadata only; adapters must never place credential material
    in this result.
    """

    model_config = ConfigDict(extra="forbid")

    plan: BootstrapPlan
    created: list[dict[str, Any]] = Field(default_factory=list)
    adopted: list[dict[str, Any]] = Field(default_factory=list)


# A canonical project is normally projected to a provider project of its own.
# The single-project/issue-only shape is deliberately opt-in so adding a new
# canonical project cannot silently attach it to the bootstrap project's
# external namespace.
DEDICATED_PROJECT_MAPPING_PROFILE = "dedicated_project"
UMBRELLA_ISSUES_MAPPING_PROFILE = "umbrella_issues"
AIAT_STABLE_PROJECT_FIELDS = (
    "AIAT Object ID",
    "AIAT Object Type",
    "AIAT Revision",
    "AIAT Managed",
)


def normalize_project_mapping_profile(value: str | None) -> str:
    """Normalize the legacy default without making umbrella mapping implicit."""
    normalized = str(value or DEDICATED_PROJECT_MAPPING_PROFILE).strip().lower()
    if normalized in {"default", DEDICATED_PROJECT_MAPPING_PROFILE}:
        return DEDICATED_PROJECT_MAPPING_PROFILE
    if normalized in {UMBRELLA_ISSUES_MAPPING_PROFILE, "single_project_issues"}:
        return UMBRELLA_ISSUES_MAPPING_PROFILE
    raise ValueError(
        "mapping_profile must be dedicated_project or the explicit umbrella_issues profile"
    )


class ProjectProvisioningPlan(BaseModel):
    """Digest-bound plan for one canonical project's provider representation.

    This is separate from :class:`BootstrapPlan`: the latter bootstraps a
    connection-level control project (AIAT itself), while this plan is scoped
    to one future canonical project and can therefore be approved/applied
    independently.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(default_factory=uuid4)
    connection_id: UUID
    project_id: UUID
    provider_kind: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mapping_profile: str = DEDICATED_PROJECT_MAPPING_PROFILE
    external_project_id: str | None = None
    external_project_key: str | None = None
    actions: list[BootstrapAction] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    manual_actions: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    rollback_actions: list[str] = Field(default_factory=list)

    @property
    def ready_to_apply(self) -> bool:
        return not self.blockers and not any(action.destructive for action in self.actions)

    @property
    def activation_blocked(self) -> bool:
        """Manual webhook work blocks activation, not safe project setup."""
        return bool(self.blockers or self.manual_actions)

    def digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"generated_at"})
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class ProjectProvisioningApplyResult(BaseModel):
    """Provider-neutral result of applying one approved project plan."""

    model_config = ConfigDict(extra="forbid")

    plan: ProjectProvisioningPlan
    created: list[dict[str, Any]] = Field(default_factory=list)
    adopted: list[dict[str, Any]] = Field(default_factory=list)


class PMLifecycleTransitionPlan(BaseModel):
    """Durable, digest-bound plan for a PM connection or binding transition.

    Approval and execution fields intentionally live outside this DTO's
    canonical digest.  The immutable plan payload is therefore stable across
    approval, application, and audit updates.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(default_factory=uuid4)
    plan_kind: str = "pm_binding_transition"
    schema_version: int = Field(default=1, ge=1)
    target_type: Literal["pm_connection", "pm_binding"]
    target_id: UUID
    connection_id: UUID
    binding_id: UUID | None = None
    expected_connection_status: str | None = None
    expected_binding_status: str | None = None
    expected_connection_revision: int | None = Field(default=None, ge=1)
    expected_binding_revision: int | None = Field(default=None, ge=1)
    desired_connection_status: str | None = None
    desired_binding_status: str | None = None
    observed_versions: dict[str, Any] = Field(default_factory=dict)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    gate_results: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    rollback_operations: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    status: LifecyclePlanStatus = LifecyclePlanStatus.PLANNED

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"status"})

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class PMInboundCanaryPlan(BaseModel):
    """Durable, issue-scoped inbound priority canary while a binding is READ_ONLY."""

    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(default_factory=uuid4)
    plan_kind: str = "pm_inbound_priority_canary"
    schema_version: int = Field(default=1, ge=1)
    connection_id: UUID
    binding_id: UUID
    project_id: UUID
    canonical_issue_id: UUID
    external_issue_id: str
    mapping_id: UUID
    actor_mapping_id: UUID
    expected_connection_status: str = "SHADOW"
    expected_binding_status: str = "READ_ONLY"
    expected_connection_revision: int = Field(ge=1)
    expected_binding_revision: int = Field(ge=1)
    expected_canonical_revision: int = Field(ge=1)
    current_priority: str
    target_priority: str
    max_command_count: int = Field(default=1, ge=1, le=1)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    gate_results: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: dict[str, Any] = Field(default_factory=dict)
    rollback_operations: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    status: CanaryPlanStatus = CanaryPlanStatus.PLANNED

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"status"})

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def pm_binding_effective_policy(
    binding_status: str,
    connection_status: str,
    direction: str,
) -> dict[str, bool | str]:
    """Return the runtime policy for a PM binding/connection state pair.

    ``READ_ONLY`` is intentionally a binding-level policy: with a non-disabled
    connection it continues outbound projection, while inbound provider
    changes remain authenticated evidence and cannot mutate canonical state.
    Only ACTIVE/DRAINING on both sides permits inbound canonical mutation.
    """

    binding = str(binding_status or "DISABLED").upper()
    connection = str(connection_status or "DISABLED").upper()
    direction_value = str(direction or "outbound").lower()
    operational = connection != "DISABLED"
    outbound = operational and binding in {"SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"} and direction_value in {"outbound", "both"}
    inbound_evidence = operational and binding in {"SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"} and direction_value in {"inbound", "both"}
    inbound_mutation = inbound_evidence and binding in {"ACTIVE", "DRAINING"} and connection in {"ACTIVE", "DRAINING"}
    return {
        "outbound_projection": outbound,
        "inbound_evidence": inbound_evidence,
        "inbound_canonical_mutation": inbound_mutation,
        "policy": "read_only" if binding == "READ_ONLY" else "active" if inbound_mutation else "shadow",
    }


class CanonicalWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    title: str
    description: str | None = None
    item_type: str = "TASK"
    status: str = "backlog"
    priority: str = "medium"
    sprint_id: UUID | None = None
    parent_id: UUID | None = None
    assigned_team: str | None = None
    assigned_agent: str | None = None
    estimated_hours: float | None = None
    actual_hours: float | None = None
    story_points: int | None = None
    revision: int = Field(default=1, ge=1)
    updated_at: datetime | None = None


class CanonicalProject(BaseModel):
    """Portable project metadata used by work-management adapters."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    description: str | None = None
    state: str = "INIT"
    revision: int = Field(default=1, ge=1)
    updated_at: datetime | None = None


class CanonicalIteration(BaseModel):
    """Portable sprint/iteration metadata used by work-management adapters."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    number: int = Field(default=1, ge=1)
    name: str | None = None
    goal: str | None = None
    status: str = "PLANNED"
    revision: int = Field(default=1, ge=1)
    updated_at: datetime | None = None


class ExternalObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    object_type: ObjectType | str
    external_id: str
    external_key: str | None = None
    url: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    project_external_id: str | None = None
    iteration_external_id: str | None = None
    provider_version: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def stable_hash(self) -> str:
        values = self.model_dump(mode="json", exclude={"provider_version", "content_hash"})
        return hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def normalized_content_hash(
    object_type: ObjectType | str,
    external_id: str,
    fields: dict[str, Any],
    *,
    external_project_id: str | None = None,
    external_repository: str | None = None,
) -> str:
    """Hash the portable provider event vocabulary for drift comparison."""
    payload: dict[str, Any] = {
        "object_type": getattr(object_type, "value", str(object_type)),
        "external_id": str(external_id),
        "fields": fields,
    }
    if external_project_id is not None:
        payload["project"] = str(external_project_id)
    if external_repository is not None:
        payload["repository"] = str(external_repository)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class ExternalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    provider_delivery_id: str
    event_type: str
    payload: dict[str, Any]
    verified: bool = False
    correlation_id: str | None = None
    causation_id: str | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NormalizedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    binding_id: UUID | None = None
    object_type: ObjectType | str
    external_id: str
    operation: str
    fields: dict[str, Any] = Field(default_factory=dict)
    # Provider-side facts kept outside the canonical field vocabulary.  These
    # are used for scope enforcement and drift reconciliation, never as an
    # authorization decision supplied by the provider itself.
    external_project_id: str | None = None
    external_repository: str | None = None
    content_hash: str | None = None
    expected_provider_version: str | None = None
    # The provider adapter may carry an AIAT revision marker in a structured
    # command envelope.  When absent, the inbound boundary may resolve the
    # expected revision from the durable mapping observation; ACTIVE never
    # guesses from an arbitrary provider field.
    expected_canonical_revision: int | None = Field(default=None, ge=1)
    actor: IntegrationActor | None = None
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
    causation_id: str | None = None
    idempotency_key: str


class ProjectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProjectionStatus
    connection_id: UUID
    object_type: ObjectType | str
    aiat_object_id: UUID | None = None
    external_id: str | None = None
    external_key: str | None = None
    external_url: str | None = None
    provider_version: str | None = None
    message: str | None = None
