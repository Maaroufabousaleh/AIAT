"""Governed self-improvement opportunity and rollout lifecycle.

This module is the offline, deterministic contract behind the guarded
self-improvement roadmap phase.  It intentionally separates the technical
gates (coding, testing, review, security, migration, and rollback) from the
human promotion decision.  A proposal may be created by an agent, but an agent
cannot approve promotion or grant itself authority, credentials, or budget.
Licence/restriction values are retained as optional metadata and never enter a
gate predicate.

The durable project service remains the authority for persistence.  The
``canonical_project_request`` projection gives that service a typed,
project-scoped request without introducing a second project store.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SELF_IMPROVEMENT_SCHEMA = "aiat.self-improvement.v1"


class ImprovementRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ImprovementStatus(StrEnum):
    PROPOSED = "proposed"
    PROJECT_BOUND = "project_bound"
    SHADOW = "shadow"
    CANARY = "canary"
    PROMOTION_PENDING = "promotion_pending"
    PROMOTED = "promoted"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


class ImprovementOutcomeKind(StrEnum):
    """Terminal learning classification for one improvement attempt."""

    SUCCESS = "success"
    FAILURE = "failure"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class ImprovementArtifactKind(StrEnum):
    """Required immutable artifact classes for a governed change."""

    CHANGE = "change"
    PROVENANCE = "provenance"
    SBOM = "sbom"
    MIGRATION = "migration"
    ROLLBACK = "rollback"


_REQUIRED_ARTIFACT_KINDS = frozenset(ImprovementArtifactKind)


class GateName(StrEnum):
    CODING = "coding"
    TESTING = "testing"
    REVIEW = "review"
    SECURITY = "security"
    MIGRATION = "migration"
    ROLLBACK = "rollback"
    HUMAN_APPROVAL = "human_approval"


class GateStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


ActorKind = Literal["human", "agent", "system"]
_TECHNICAL_GATES = (
    GateName.CODING,
    GateName.TESTING,
    GateName.REVIEW,
    GateName.SECURITY,
    GateName.MIGRATION,
    GateName.ROLLBACK,
)
_ROLLOUT_STATUSES = {
    ImprovementStatus.SHADOW,
    ImprovementStatus.CANARY,
    ImprovementStatus.PROMOTION_PENDING,
    ImprovementStatus.PROMOTED,
}


class SelfImprovementAuthorityError(ValueError):
    """Raised when a lifecycle operation would let an agent self-authorise."""


class SelfImprovementTransitionError(ValueError):
    """Raised when a lifecycle transition lacks required evidence."""


class ImprovementOpportunity(BaseModel):
    """Canonical metadata required before an improvement project is created."""

    model_config = ConfigDict(extra="forbid")

    opportunity_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=160)
    owner_kind: ActorKind = "human"
    risk: ImprovementRisk
    budget_usd: Decimal = Field(ge=Decimal("0"))
    evidence_policy: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=160)
    created_by: str = Field(min_length=1, max_length=160)
    created_by_kind: ActorKind
    company_id: UUID | None = None
    licence_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("budget_usd", mode="before")
    @classmethod
    def _finite_budget(cls, value: object) -> Decimal:
        try:
            budget = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("budget_usd must be a finite non-negative decimal") from exc
        if not budget.is_finite() or budget < 0:
            raise ValueError("budget_usd must be a finite non-negative decimal")
        return budget

    @field_validator("title", "description", "owner", "evidence_policy", "source", "created_by")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text fields must not be blank")
        return value.strip()

    def canonical_project_request(self, *, project_id: UUID | None = None) -> dict[str, Any]:
        """Return the request consumed by the canonical project service."""

        resolved_project_id = project_id or uuid4()
        return {
            "project_id": resolved_project_id,
            "company_id": self.company_id,
            "name": f"Improvement: {self.title}",
            "description": self.description,
            "state": "INIT",
            "created_by": self.created_by,
            "human_requester": self.owner if self.owner_kind == "human" else None,
            "config": {
                "self_improvement": {
                    "schema_version": SELF_IMPROVEMENT_SCHEMA,
                    "opportunity_id": str(self.opportunity_id),
                    "owner": self.owner,
                    "owner_kind": self.owner_kind,
                    "risk": self.risk,
                    "budget_usd": str(self.budget_usd),
                    "evidence_policy": self.evidence_policy,
                    "source": self.source,
                    # Licence data is copied for provenance/metadata only.
                    "licence_metadata": dict(self.licence_metadata),
                }
            },
        }


class GateRecord(BaseModel):
    """Independent evidence state for one mandatory gate."""

    model_config = ConfigDict(extra="forbid")

    name: GateName
    status: GateStatus = GateStatus.PENDING
    actor: str | None = None
    actor_kind: ActorKind | None = None
    evidence_refs: tuple[str, ...] = ()
    detail: str | None = None


class RolloutObservation(BaseModel):
    """Bounded shadow/canary comparison evidence."""

    model_config = ConfigDict(extra="forbid")

    stage: Literal["shadow", "canary"]
    sample_count: int = Field(gt=0)
    regression_fraction: float = Field(ge=0.0, le=1.0)
    irreversible_side_effects: int = Field(ge=0)

    @field_validator("regression_fraction")
    @classmethod
    def _finite_regression(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("regression_fraction must be finite")
        return value


class ImprovementOutcome(BaseModel):
    """Bounded, secret-safe result and learning record for an improvement.

    This record is deliberately a small projection of outcome evidence.  Raw
    logs, credentials, source files, and external records remain owned by
    their canonical services and are referenced through ``evidence_refs`` or
    ``integration_refs``.  Licence/restriction information, when present in
    the opportunity, remains metadata and is not evaluated here.
    """

    model_config = ConfigDict(extra="forbid")

    outcome_id: UUID = Field(default_factory=uuid4)
    outcome: ImprovementOutcomeKind
    cost_usd: Decimal = Field(ge=Decimal("0"), le=Decimal("1000000"))
    incident_count: int = Field(ge=0, le=100000)
    rollback_performed: bool = False
    kpi_learning: dict[str, float] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    recorded_by: str = Field(min_length=1, max_length=160)
    recorded_by_kind: ActorKind
    detail: str | None = Field(default=None, max_length=2000)

    @field_validator("cost_usd", mode="before")
    @classmethod
    def _finite_cost(cls, value: object) -> Decimal:
        try:
            cost = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("cost_usd must be a finite non-negative decimal") from exc
        if not cost.is_finite() or cost < 0 or cost > Decimal("1000000"):
            raise ValueError("cost_usd must be a finite decimal between 0 and 1000000")
        return cost

    @field_validator("recorded_by", mode="before")
    @classmethod
    def _non_blank_recorder(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("recorded_by must not be blank")
        return normalized

    @field_validator("detail", mode="before")
    @classmethod
    def _normalize_detail(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _normalize_evidence_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("evidence_refs must be a sequence of references")
        references = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        normalized = tuple(str(reference).strip() for reference in references)
        if any(not reference for reference in normalized):
            raise ValueError("evidence_refs must not contain blank values")
        if any(len(reference) > 512 for reference in normalized):
            raise ValueError("evidence_refs entries must be at most 512 characters")
        return tuple(dict.fromkeys(normalized))

    @field_validator("kpi_learning", mode="before")
    @classmethod
    def _normalize_kpi_learning(cls, value: object) -> dict[str, float]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("kpi_learning must be a mapping")
        if len(value) > 32:
            raise ValueError("kpi_learning may contain at most 32 metrics")
        normalized: dict[str, float] = {}
        for key, raw_value in value.items():
            normalized_key = str(key).strip()
            if not normalized_key or len(normalized_key) > 96:
                raise ValueError("kpi_learning metric names must be 1-96 characters")
            try:
                metric = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("kpi_learning values must be finite numbers") from exc
            if not math.isfinite(metric) or abs(metric) > 1_000_000_000_000:
                raise ValueError("kpi_learning values must be finite and bounded")
            if normalized_key in normalized:
                raise ValueError("kpi_learning metric names must be unique after trimming")
            normalized[normalized_key] = metric
        return normalized


class ImprovementArtifact(BaseModel):
    """One immutable pointer to an artifact owned by a canonical service.

    The lifecycle stores identity, checksum, and bounded metadata only.  It
    never copies artifact bytes, executes a migration, or treats a licence
    notice as a gate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID = Field(default_factory=uuid4)
    kind: ImprovementArtifactKind
    uri: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0, le=1_000_000_000_000)
    candidate_version: str = Field(min_length=1, max_length=240)
    source_revision: str = Field(min_length=1, max_length=240)
    target_version: str | None = Field(default=None, max_length=240)
    canonical_artifact_id: str | None = Field(default=None, max_length=160)
    immutable: Literal[True] = True
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "uri",
        "candidate_version",
        "source_revision",
        "target_version",
        "canonical_artifact_id",
        mode="before",
    )
    @classmethod
    def _normalize_artifact_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("artifact text fields must not be blank")
        return normalized

    @field_validator("sha256", mode="before")
    @classmethod
    def _normalize_sha256(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("artifact sha256 must be a 64-character hexadecimal digest")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_artifact_metadata(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("artifact metadata must be a mapping")
        if len(value) > 20:
            raise ValueError("artifact metadata may contain at most 20 entries")
        normalized: dict[str, str] = {}
        for key, raw_value in value.items():
            normalized_key = str(key).strip()
            normalized_value = str(raw_value).strip()
            if not normalized_key or len(normalized_key) > 96:
                raise ValueError("artifact metadata keys must be 1-96 characters")
            if len(normalized_value) > 512:
                raise ValueError("artifact metadata values must be at most 512 characters")
            if normalized_key in normalized:
                raise ValueError("artifact metadata keys must be unique after trimming")
            normalized[normalized_key] = normalized_value
        return normalized


class ImprovementArtifactBundle(BaseModel):
    """Immutable manifest for the five required self-improvement artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "aiat.self-improvement-artifacts.v1"
    bundle_id: UUID = Field(default_factory=uuid4)
    candidate_version: str = Field(min_length=1, max_length=240)
    generated_by: str = Field(min_length=1, max_length=160)
    generated_by_kind: ActorKind
    artifacts: tuple[ImprovementArtifact, ...] = Field(min_length=5, max_length=20)
    metadata: dict[str, str] = Field(default_factory=dict)
    manifest_sha256: str = Field(default="", min_length=64, max_length=64)

    @field_validator("candidate_version", "generated_by", mode="before")
    @classmethod
    def _normalize_bundle_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("artifact bundle text fields must not be blank")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_bundle_metadata(cls, value: object) -> dict[str, str]:
        return ImprovementArtifact._normalize_artifact_metadata(value)

    @field_validator("manifest_sha256", mode="before")
    @classmethod
    def _normalize_manifest_sha256(cls, value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized and (
            len(normalized) != 64
            or any(character not in "0123456789abcdef" for character in normalized)
        ):
            raise ValueError("artifact manifest_sha256 must be a 64-character hexadecimal digest")
        return normalized

    @field_validator("artifacts", mode="before")
    @classmethod
    def _normalize_artifacts(cls, value: object) -> tuple[ImprovementArtifact, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("artifact bundle artifacts must be a sequence")
        artifacts = tuple(
            artifact if isinstance(artifact, ImprovementArtifact) else ImprovementArtifact.model_validate(artifact)
            for artifact in value
        )
        if len({artifact.artifact_id for artifact in artifacts}) != len(artifacts):
            raise ValueError("artifact bundle artifact IDs must be unique")
        kinds = {artifact.kind for artifact in artifacts}
        missing = _REQUIRED_ARTIFACT_KINDS - kinds
        if missing:
            raise ValueError(
                "artifact bundle is missing required kinds: "
                + ", ".join(sorted(kind.value for kind in missing))
            )
        if len(kinds) != len(artifacts):
            raise ValueError("artifact bundle may contain only one artifact per kind")
        candidate_versions = {artifact.candidate_version for artifact in artifacts}
        if len(candidate_versions) != 1:
            raise ValueError("artifact bundle artifacts must share one candidate version")
        return artifacts

    @model_validator(mode="after")
    def _set_manifest_hash(self) -> ImprovementArtifactBundle:
        expected = self._calculate_content_hash()
        if self.manifest_sha256 and self.manifest_sha256 != expected:
            raise ValueError("artifact manifest_sha256 does not match immutable contents")
        object.__setattr__(self, "manifest_sha256", expected)
        return self

    @property
    def content_hash(self) -> str:
        """Return the deterministic hash of the immutable manifest contents."""

        return self.manifest_sha256

    def _calculate_content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"bundle_id", "manifest_sha256"})
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_worker_artifacts(
        cls,
        worker_artifacts: Sequence[Mapping[str, Any]],
        *,
        generated_by: str,
        generated_by_kind: ActorKind,
        candidate_version: str | None = None,
        source_revision: str | None = None,
        bundle_id: UUID | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ImprovementArtifactBundle:
        """Build the governed manifest from normalized worker-result records.

        Worker runs remain the source of artifact bytes and canonical storage
        owns the integer artifact row.  The worker must label each returned
        record with ``self_improvement_kind`` and the candidate/source values
        in its bounded metadata.  If a runtime does not provide a UUID, a
        stable UUID is derived from its canonical artifact ID (or URI and
        checksum) so retries describe the same immutable record.
        """

        if isinstance(worker_artifacts, (str, bytes)) or not isinstance(worker_artifacts, Sequence):
            raise ValueError("worker_artifacts must be a sequence of mappings")
        normalized_artifacts: list[ImprovementArtifact] = []
        for index, raw in enumerate(worker_artifacts):
            if not isinstance(raw, Mapping):
                model_dump = getattr(raw, "model_dump", None)
                if callable(model_dump):
                    raw = model_dump(mode="json")
            if not isinstance(raw, Mapping):
                raise ValueError(f"worker artifact {index} must be a mapping")
            record = dict(raw)
            raw_metadata = record.get("metadata") or {}
            if not isinstance(raw_metadata, Mapping):
                raise ValueError(f"worker artifact {index} metadata must be a mapping")
            artifact_metadata = {
                str(key).strip(): str(value).strip() for key, value in raw_metadata.items()
            }
            raw_kind = artifact_metadata.get("self_improvement_kind")
            if not raw_kind:
                raise ValueError(
                    f"worker artifact {index} metadata must include self_improvement_kind"
                )
            try:
                kind = ImprovementArtifactKind(str(raw_kind).strip().lower())
            except ValueError as exc:
                raise ValueError(
                    f"worker artifact {index} has unknown self-improvement kind: {raw_kind}"
                ) from exc
            resolved_candidate = candidate_version or artifact_metadata.get("candidate_version")
            resolved_revision = source_revision or artifact_metadata.get("source_revision")
            if not resolved_candidate or not resolved_revision:
                raise ValueError(
                    f"worker artifact {index} must include candidate_version and source_revision"
                )
            uri = str(record.get("uri") or "").strip()
            sha256 = str(record.get("sha256") or "").strip().lower()
            size_bytes = record.get("size_bytes")
            if not uri or not sha256 or size_bytes is None:
                raise ValueError(
                    f"worker artifact {index} must include uri, sha256, and size_bytes"
                )
            canonical_id = artifact_metadata.get("canonical_artifact_id")
            raw_id = record.get("artifact_id")
            identity = str(raw_id or canonical_id or f"{uri}|{sha256}|{kind.value}").strip()
            try:
                artifact_id = UUID(identity)
            except ValueError:
                artifact_id = uuid5(NAMESPACE_URL, f"aiat:self-improvement-artifact:{identity}")
            normalized_artifacts.append(
                ImprovementArtifact(
                    artifact_id=artifact_id,
                    kind=kind,
                    uri=uri,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    candidate_version=resolved_candidate,
                    source_revision=resolved_revision,
                    canonical_artifact_id=canonical_id,
                    metadata={
                        key: value
                        for key, value in artifact_metadata.items()
                        if key
                        not in {
                            "self_improvement_kind",
                            "candidate_version",
                            "source_revision",
                            "canonical_artifact_id",
                        }
                    },
                )
            )
        resolved_bundle_candidate = candidate_version or (
            normalized_artifacts[0].candidate_version if normalized_artifacts else ""
        )
        return cls(
            bundle_id=bundle_id or uuid4(),
            candidate_version=resolved_bundle_candidate,
            generated_by=generated_by,
            generated_by_kind=generated_by_kind,
            artifacts=tuple(normalized_artifacts),
            metadata=dict(metadata or {}),
        )


class ImprovementArtifactReadback(BaseModel):
    """Checksum/size evidence returned after a provider object read-back."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_id: UUID
    artifact_id: UUID
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0, le=1_000_000_000_000)
    source: str = Field(min_length=1, max_length=240)
    canonical_artifact_id: str | None = Field(default=None, max_length=160)
    verified: Literal[True] = True
    verified_by: str = Field(min_length=1, max_length=160)
    verified_by_kind: ActorKind

    @field_validator("sha256", mode="before")
    @classmethod
    def _normalize_readback_sha256(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("read-back sha256 must be a 64-character hexadecimal digest")
        return normalized

    @field_validator("source", "canonical_artifact_id", "verified_by", mode="before")
    @classmethod
    def _normalize_readback_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("read-back text fields must not be blank")
        return normalized

    @classmethod
    def from_bytes(
        cls,
        *,
        bundle_id: UUID,
        artifact: ImprovementArtifact,
        data: bytes,
        source: str,
        verified_by: str,
        verified_by_kind: ActorKind,
        canonical_artifact_id: str | None = None,
    ) -> ImprovementArtifactReadback:
        """Verify provider bytes without retaining them in the lifecycle."""

        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != artifact.sha256:
            raise SelfImprovementTransitionError(
                f"artifact read-back checksum mismatch for {artifact.artifact_id}"
            )
        if len(data) != artifact.size_bytes:
            raise SelfImprovementTransitionError(
                f"artifact read-back size mismatch for {artifact.artifact_id}"
            )
        if (
            artifact.canonical_artifact_id is not None
            and canonical_artifact_id is not None
            and artifact.canonical_artifact_id != canonical_artifact_id
        ):
            raise SelfImprovementTransitionError(
                f"artifact read-back canonical ID mismatch for {artifact.artifact_id}"
            )
        return cls(
            bundle_id=bundle_id,
            artifact_id=artifact.artifact_id,
            sha256=actual_sha256,
            size_bytes=len(data),
            source=source,
            canonical_artifact_id=canonical_artifact_id or artifact.canonical_artifact_id,
            verified_by=verified_by,
            verified_by_kind=verified_by_kind,
        )

class LifecycleTransition(BaseModel):
    """Small, secret-safe transition record retained in the evidence report."""

    from_status: ImprovementStatus
    to_status: ImprovementStatus
    actor: str
    actor_kind: ActorKind
    reason: str | None = None


class SelfImprovementLifecycle(BaseModel):
    """State machine for one canonical improvement project."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: str = SELF_IMPROVEMENT_SCHEMA
    revision: int = Field(default=1, ge=1)
    opportunity: ImprovementOpportunity
    project_id: UUID | None = None
    status: ImprovementStatus = ImprovementStatus.PROPOSED
    candidate_version: str | None = None
    active_version: str | None = None
    prior_version: str | None = None
    gates: dict[GateName, GateRecord] = Field(default_factory=dict)
    observations: list[RolloutObservation] = Field(default_factory=list)
    outcomes: list[ImprovementOutcome] = Field(default_factory=list)
    artifact_bundle: ImprovementArtifactBundle | None = None
    artifact_readbacks: list[ImprovementArtifactReadback] = Field(default_factory=list)
    history: list[LifecycleTransition] = Field(default_factory=list)
    integration_refs: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        opportunity: ImprovementOpportunity,
        *,
        active_version: str | None = None,
    ) -> SelfImprovementLifecycle:
        return cls(
            opportunity=opportunity,
            active_version=active_version,
            gates={
                name: GateRecord(name=name) for name in (*_TECHNICAL_GATES, GateName.HUMAN_APPROVAL)
            },
        )

    def canonical_project_request(self, *, project_id: UUID | None = None) -> dict[str, Any]:
        return self.opportunity.canonical_project_request(project_id=project_id or self.project_id)

    def bind_project(self, project_id: UUID, *, actor: str, actor_kind: ActorKind) -> None:
        if not actor.strip():
            raise SelfImprovementAuthorityError("project binding requires an actor")
        if self.status != ImprovementStatus.PROPOSED:
            raise SelfImprovementTransitionError("project can only be bound from proposed")
        self.project_id = project_id
        self._transition(
            ImprovementStatus.PROJECT_BOUND,
            actor=actor,
            actor_kind=actor_kind,
            reason="canonical project created",
        )

    def record_gate(
        self,
        name: GateName | str,
        *,
        passed: bool,
        actor: str,
        actor_kind: ActorKind,
        evidence_refs: tuple[str, ...] = (),
        detail: str | None = None,
    ) -> None:
        try:
            gate_name = GateName(name)
        except ValueError as exc:
            raise SelfImprovementTransitionError(f"unknown self-improvement gate: {name}") from exc
        if not actor.strip():
            raise SelfImprovementAuthorityError("gate evidence requires an actor")
        if gate_name == GateName.HUMAN_APPROVAL and actor_kind != "human":
            raise SelfImprovementAuthorityError(
                "promotion approval must be recorded by a human operator"
            )
        if passed and not evidence_refs:
            raise SelfImprovementTransitionError(
                f"passed {gate_name} gate requires evidence references"
            )
        self.gates[gate_name] = GateRecord(
            name=gate_name,
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            actor=actor,
            actor_kind=actor_kind,
            evidence_refs=tuple(evidence_refs),
            detail=detail,
        )

    def start_shadow(self, *, candidate_version: str, actor: str, actor_kind: ActorKind) -> None:
        self._require_project()
        if self.status != ImprovementStatus.PROJECT_BOUND:
            raise SelfImprovementTransitionError(
                "shadow can only start after canonical project binding"
            )
        if not candidate_version.strip():
            raise SelfImprovementTransitionError("shadow requires an immutable candidate version")
        self._require_technical_gates()
        self.candidate_version = candidate_version.strip()
        self._transition(
            ImprovementStatus.SHADOW,
            actor=actor,
            actor_kind=actor_kind,
            reason="technical gates passed",
        )

    def record_observation(
        self,
        *,
        stage: Literal["shadow", "canary"],
        sample_count: int,
        regression_fraction: float,
        irreversible_side_effects: int = 0,
    ) -> None:
        if self.status not in {ImprovementStatus.SHADOW, ImprovementStatus.CANARY}:
            raise SelfImprovementTransitionError(
                "observations require an active shadow or canary stage"
            )
        expected_stage = "shadow" if self.status == ImprovementStatus.SHADOW else "canary"
        if stage != expected_stage:
            raise SelfImprovementTransitionError(f"expected {expected_stage} observation")
        self.observations.append(
            RolloutObservation(
                stage=stage,
                sample_count=sample_count,
                regression_fraction=regression_fraction,
                irreversible_side_effects=irreversible_side_effects,
            )
        )

    def record_outcome(
        self,
        *,
        outcome: ImprovementOutcomeKind | str,
        cost_usd: Decimal | str | float | int,
        incident_count: int,
        rollback_performed: bool = False,
        kpi_learning: Mapping[str, float] | None = None,
        evidence_refs: tuple[str, ...] = (),
        actor: str,
        actor_kind: ActorKind,
        outcome_id: UUID | None = None,
        detail: str | None = None,
    ) -> ImprovementOutcome:
        """Persist one terminal result without granting execution authority.

        The lifecycle snapshot is persisted by the canonical project writer.
        Reusing an ``outcome_id`` with identical content is idempotent; a
        conflicting reuse fails closed so a retry cannot silently rewrite
        historical cost, incident, rollback, or KPI evidence.
        """

        if self.status not in {
            ImprovementStatus.PROMOTED,
            ImprovementStatus.ROLLED_BACK,
            ImprovementStatus.REJECTED,
        }:
            raise SelfImprovementTransitionError(
                "outcomes require a promoted, rolled-back, or rejected lifecycle"
            )
        if not actor.strip():
            raise SelfImprovementAuthorityError("outcome evidence requires an actor")
        record = ImprovementOutcome(
            outcome_id=outcome_id or uuid4(),
            outcome=outcome,
            cost_usd=cost_usd,
            incident_count=incident_count,
            rollback_performed=rollback_performed,
            kpi_learning=dict(kpi_learning or {}),
            evidence_refs=tuple(evidence_refs),
            recorded_by=actor,
            recorded_by_kind=actor_kind,
            detail=detail,
        )
        if record.rollback_performed and record.outcome != ImprovementOutcomeKind.ROLLED_BACK:
            raise SelfImprovementTransitionError(
                "rollback_performed outcomes must use the rolled_back classification"
            )
        if record.outcome == ImprovementOutcomeKind.ROLLED_BACK and (
            self.status != ImprovementStatus.ROLLED_BACK or not record.rollback_performed
        ):
            raise SelfImprovementTransitionError(
                "rolled_back outcomes require an exact rolled-back lifecycle"
            )
        for existing in self.outcomes:
            if existing.outcome_id != record.outcome_id:
                continue
            if existing != record:
                raise SelfImprovementTransitionError(
                    f"outcome id already exists with different evidence: {record.outcome_id}"
                )
            return existing
        self.outcomes.append(record)
        self.assert_invariants()
        return record

    def record_artifact_bundle(
        self,
        bundle: ImprovementArtifactBundle | Mapping[str, Any],
        *,
        actor: str,
        actor_kind: ActorKind,
    ) -> ImprovementArtifactBundle:
        """Attach an immutable five-kind artifact manifest to this project.

        Artifact bytes and execution remain owned by the artifact/worker
        services.  This aggregate records only a checksum-bearing manifest and
        links each artifact identity through the existing canonical reference
        map.  Repeating an identical bundle is safe; a conflicting bundle ID
        fails closed.
        """

        self._require_project()
        if not actor.strip():
            raise SelfImprovementAuthorityError("artifact bundle evidence requires an actor")
        normalized = (
            bundle
            if isinstance(bundle, ImprovementArtifactBundle)
            else ImprovementArtifactBundle.model_validate(dict(bundle))
        )
        if self.candidate_version and normalized.candidate_version != self.candidate_version:
            raise SelfImprovementTransitionError(
                "artifact bundle candidate version must match the lifecycle candidate"
            )
        if self.artifact_bundle is not None:
            if self.artifact_bundle.bundle_id != normalized.bundle_id:
                raise SelfImprovementTransitionError(
                    "an artifact bundle is already recorded for this lifecycle"
                )
            if self.artifact_bundle != normalized:
                raise SelfImprovementTransitionError(
                    f"artifact bundle id already exists with different evidence: {normalized.bundle_id}"
                )
            for artifact in normalized.artifacts:
                self.link_reference("artifact", str(artifact.artifact_id))
            return self.artifact_bundle
        self.artifact_bundle = normalized
        for artifact in normalized.artifacts:
            self.link_reference("artifact", str(artifact.artifact_id))
        self.assert_invariants()
        return normalized

    def record_artifact_readback(
        self,
        *,
        artifact_id: UUID,
        actual_sha256: str,
        actual_size_bytes: int,
        source: str,
        actor: str,
        actor_kind: ActorKind,
        canonical_artifact_id: str | None = None,
    ) -> ImprovementArtifactReadback:
        """Persist provider read-back parity for one manifest artifact.

        The caller/provider supplies only the observed digest and size; raw
        bytes stay in object storage.  A matching artifact ID, checksum, and
        size are required, and retries with identical evidence are idempotent.
        """

        self._require_project()
        if self.artifact_bundle is None:
            raise SelfImprovementTransitionError(
                "artifact read-back requires a recorded artifact bundle"
            )
        if not actor.strip():
            raise SelfImprovementAuthorityError("artifact read-back requires an actor")
        expected = next(
            (artifact for artifact in self.artifact_bundle.artifacts if artifact.artifact_id == artifact_id),
            None,
        )
        if expected is None:
            raise SelfImprovementTransitionError(
                f"artifact read-back references an unknown artifact: {artifact_id}"
            )
        normalized_sha256 = str(actual_sha256).strip().lower()
        if normalized_sha256 != expected.sha256:
            raise SelfImprovementTransitionError(
                f"artifact read-back checksum mismatch for {artifact_id}"
            )
        if actual_size_bytes != expected.size_bytes:
            raise SelfImprovementTransitionError(
                f"artifact read-back size mismatch for {artifact_id}"
            )
        if (
            expected.canonical_artifact_id is not None
            and canonical_artifact_id is not None
            and expected.canonical_artifact_id != canonical_artifact_id
        ):
            raise SelfImprovementTransitionError(
                f"artifact read-back canonical ID mismatch for {artifact_id}"
            )
        record = ImprovementArtifactReadback(
            bundle_id=self.artifact_bundle.bundle_id,
            artifact_id=artifact_id,
            sha256=normalized_sha256,
            size_bytes=actual_size_bytes,
            source=source,
            canonical_artifact_id=canonical_artifact_id or expected.canonical_artifact_id,
            verified_by=actor,
            verified_by_kind=actor_kind,
        )
        for existing in self.artifact_readbacks:
            if existing.artifact_id != record.artifact_id:
                continue
            if existing != record:
                raise SelfImprovementTransitionError(
                    f"artifact read-back already exists with different evidence: {artifact_id}"
                )
            return existing
        self.artifact_readbacks.append(record)
        self.link_reference(
            "artifact_readback",
            f"{record.bundle_id}:{record.artifact_id}",
        )
        self.assert_invariants()
        return record

    def record_artifact_readback_bytes(
        self,
        *,
        artifact_id: UUID,
        data: bytes,
        source: str,
        actor: str,
        actor_kind: ActorKind,
        canonical_artifact_id: str | None = None,
    ) -> ImprovementArtifactReadback:
        """Verify object bytes and persist only their secret-safe evidence."""

        if self.artifact_bundle is None:
            raise SelfImprovementTransitionError(
                "artifact read-back requires a recorded artifact bundle"
            )
        expected = next(
            (artifact for artifact in self.artifact_bundle.artifacts if artifact.artifact_id == artifact_id),
            None,
        )
        if expected is None:
            raise SelfImprovementTransitionError(
                f"artifact read-back references an unknown artifact: {artifact_id}"
            )
        readback = ImprovementArtifactReadback.from_bytes(
            bundle_id=self.artifact_bundle.bundle_id,
            artifact=expected,
            data=data,
            source=source,
            verified_by=actor,
            verified_by_kind=actor_kind,
            canonical_artifact_id=canonical_artifact_id,
        )
        return self.record_artifact_readback(
            artifact_id=readback.artifact_id,
            actual_sha256=readback.sha256,
            actual_size_bytes=readback.size_bytes,
            source=readback.source,
            actor=readback.verified_by,
            actor_kind=readback.verified_by_kind,
            canonical_artifact_id=readback.canonical_artifact_id,
        )

    @property
    def artifact_readback_complete(self) -> bool:
        """Whether every required manifest artifact has verified read-back."""

        if self.artifact_bundle is None:
            return False
        expected_ids = {artifact.artifact_id for artifact in self.artifact_bundle.artifacts}
        return expected_ids == {record.artifact_id for record in self.artifact_readbacks}

    def link_reference(self, kind: str, reference: str) -> None:
        """Attach a bounded reference to an existing canonical record.

        References are pointers only.  The referenced issue, worker run,
        artifact, budget reservation, branch/SBOM, deployment, or evidence
        record remains owned by its existing service/table; this aggregate
        never copies those records or treats a licence notice as a gate.
        """

        normalized_kind = kind.strip().lower()
        normalized_reference = reference.strip()
        allowed_kinds = {
            "issue",
            "worker_run",
            "artifact",
            "artifact_readback",
            "budget_reservation",
            "branch",
            "sbom",
            "deployment",
            "evidence",
            "repository",
        }
        if normalized_kind not in allowed_kinds:
            raise SelfImprovementTransitionError(
                f"unknown self-improvement reference kind: {normalized_kind or kind}"
            )
        if not normalized_reference:
            raise SelfImprovementTransitionError("self-improvement reference must not be blank")
        if len(normalized_reference) > 512:
            raise SelfImprovementTransitionError("self-improvement reference is too long")
        current = list(self.integration_refs.get(normalized_kind, ()))
        if normalized_reference not in current:
            current.append(normalized_reference)
        self.integration_refs[normalized_kind] = tuple(current)

    def start_canary(self, *, actor: str, actor_kind: ActorKind) -> None:
        if self.status != ImprovementStatus.SHADOW:
            raise SelfImprovementTransitionError("canary can only start from shadow")
        observation = self._latest_observation("shadow")
        self._require_safe_observation(observation)
        self._transition(
            ImprovementStatus.CANARY,
            actor=actor,
            actor_kind=actor_kind,
            reason="shadow evidence passed",
        )

    def request_promotion(self, *, actor: str, actor_kind: ActorKind) -> None:
        if self.status != ImprovementStatus.CANARY:
            raise SelfImprovementTransitionError("promotion request requires canary stage")
        observation = self._latest_observation("canary")
        self._require_safe_observation(observation)
        self._transition(
            ImprovementStatus.PROMOTION_PENDING,
            actor=actor,
            actor_kind=actor_kind,
            reason="canary evidence passed",
        )

    def approve_promotion(self, *, actor: str, actor_kind: ActorKind) -> None:
        if actor_kind != "human":
            raise SelfImprovementAuthorityError(
                "an agent or system cannot approve self-improvement promotion"
            )
        if self.status != ImprovementStatus.PROMOTION_PENDING:
            raise SelfImprovementTransitionError(
                "promotion approval requires a pending promotion request"
            )
        approval = self.gates[GateName.HUMAN_APPROVAL]
        if approval.status != GateStatus.PASSED or approval.actor_kind != "human":
            raise SelfImprovementAuthorityError(
                "promotion requires an independent human approval gate"
            )
        if not self.candidate_version:
            raise SelfImprovementTransitionError("promotion requires a candidate version")
        self.prior_version = self.active_version
        self.active_version = self.candidate_version
        self._transition(
            ImprovementStatus.PROMOTED, actor=actor, actor_kind=actor_kind, reason="human approval"
        )

    def rollback(self, *, actor: str, actor_kind: ActorKind, reason: str) -> None:
        if actor_kind == "agent":
            raise SelfImprovementAuthorityError("agents cannot execute self-improvement rollback")
        if self.status not in _ROLLOUT_STATUSES:
            raise SelfImprovementTransitionError(
                "rollback requires a shadow, canary, pending, or promoted lifecycle"
            )
        if not reason.strip():
            raise SelfImprovementTransitionError("rollback requires a reason")
        self._transition(
            ImprovementStatus.ROLLING_BACK,
            actor=actor,
            actor_kind=actor_kind,
            reason=reason.strip(),
        )
        if self.prior_version is not None:
            self.active_version = self.prior_version
        self._transition(
            ImprovementStatus.ROLLED_BACK,
            actor=actor,
            actor_kind=actor_kind,
            reason="prior immutable version restored",
        )

    def assert_invariants(self) -> None:
        if self.schema_version != SELF_IMPROVEMENT_SCHEMA:
            raise SelfImprovementTransitionError("invalid self-improvement schema")
        if self.status in _ROLLOUT_STATUSES and self.project_id is None:
            raise SelfImprovementTransitionError("rollout state requires a canonical project")
        if set(self.gates) != {*_TECHNICAL_GATES, GateName.HUMAN_APPROVAL}:
            raise SelfImprovementTransitionError(
                "all independent self-improvement gates must be represented"
            )
        if (
            self.status == ImprovementStatus.PROMOTED
            and self.active_version != self.candidate_version
        ):
            raise SelfImprovementTransitionError("promoted version must equal candidate version")
        if (
            self.status == ImprovementStatus.ROLLED_BACK
            and self.prior_version is not None
            and self.active_version != self.prior_version
        ):
            raise SelfImprovementTransitionError("rollback did not restore the exact prior version")
        if self.artifact_bundle is not None and (
            self.candidate_version
            and self.artifact_bundle.candidate_version != self.candidate_version
        ):
            raise SelfImprovementTransitionError(
                "artifact bundle candidate version must match the lifecycle candidate"
            )
        if self.artifact_bundle is None and self.artifact_readbacks:
            raise SelfImprovementTransitionError(
                "artifact read-backs require a recorded artifact bundle"
            )
        expected_artifacts = {
            artifact.artifact_id: artifact for artifact in self.artifact_bundle.artifacts
        } if self.artifact_bundle is not None else {}
        seen_readbacks: set[UUID] = set()
        for readback in self.artifact_readbacks:
            expected = expected_artifacts.get(readback.artifact_id)
            if expected is None:
                raise SelfImprovementTransitionError(
                    f"artifact read-back references an unknown artifact: {readback.artifact_id}"
                )
            if self.artifact_bundle is None or readback.bundle_id != self.artifact_bundle.bundle_id:
                raise SelfImprovementTransitionError(
                    "artifact read-back bundle ID does not match the lifecycle manifest"
                )
            if readback.artifact_id in seen_readbacks:
                raise SelfImprovementTransitionError(
                    f"duplicate artifact read-back: {readback.artifact_id}"
                )
            if readback.sha256 != expected.sha256 or readback.size_bytes != expected.size_bytes:
                raise SelfImprovementTransitionError(
                    f"artifact read-back parity failed for {readback.artifact_id}"
                )
            if (
                expected.canonical_artifact_id is not None
                and readback.canonical_artifact_id != expected.canonical_artifact_id
            ):
                raise SelfImprovementTransitionError(
                    f"artifact read-back canonical ID does not match {readback.artifact_id}"
                )
            seen_readbacks.add(readback.artifact_id)
        seen_outcomes: set[UUID] = set()
        for outcome in self.outcomes:
            if outcome.outcome_id in seen_outcomes:
                raise SelfImprovementTransitionError(
                    f"duplicate self-improvement outcome id: {outcome.outcome_id}"
                )
            seen_outcomes.add(outcome.outcome_id)
            if outcome.rollback_performed and outcome.outcome != ImprovementOutcomeKind.ROLLED_BACK:
                raise SelfImprovementTransitionError(
                    "rollback_performed outcomes must use the rolled_back classification"
                )
            if outcome.outcome == ImprovementOutcomeKind.ROLLED_BACK and self.status != ImprovementStatus.ROLLED_BACK:
                raise SelfImprovementTransitionError(
                    "rolled_back outcomes require an exact rolled-back lifecycle"
                )
        for kind, references in self.integration_refs.items():
            if not kind.strip() or any(not str(reference).strip() for reference in references):
                raise SelfImprovementTransitionError(
                    "self-improvement references must be non-blank"
                )

    def as_dict(self) -> dict[str, Any]:
        self.assert_invariants()
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "status": self.status,
            "project_id": str(self.project_id) if self.project_id else None,
            "candidate_version": self.candidate_version,
            "active_version": self.active_version,
            "prior_version": self.prior_version,
            "opportunity": {
                "opportunity_id": str(self.opportunity.opportunity_id),
                "title": self.opportunity.title,
                "description": self.opportunity.description,
                "owner": self.opportunity.owner,
                "owner_kind": self.opportunity.owner_kind,
                "risk": self.opportunity.risk,
                "budget_usd": str(self.opportunity.budget_usd),
                "evidence_policy": self.opportunity.evidence_policy,
                "source": self.opportunity.source,
                "created_by": self.opportunity.created_by,
                "created_by_kind": self.opportunity.created_by_kind,
                "company_id": str(self.opportunity.company_id)
                if self.opportunity.company_id
                else None,
                "licence_metadata": dict(self.opportunity.licence_metadata),
            },
            "gates": {
                name.value: gate.model_dump(mode="json")
                for name, gate in sorted(self.gates.items(), key=lambda item: item[0].value)
            },
            "observations": [
                observation.model_dump(mode="json") for observation in self.observations
            ],
            "outcomes": [outcome.model_dump(mode="json") for outcome in self.outcomes],
            "artifact_bundle": (
                self.artifact_bundle.model_dump(mode="json")
                if self.artifact_bundle is not None
                else None
            ),
            "artifact_readbacks": [
                readback.model_dump(mode="json") for readback in self.artifact_readbacks
            ],
            "history": [transition.model_dump(mode="json") for transition in self.history],
            "integration_refs": {
                kind: list(references) for kind, references in sorted(self.integration_refs.items())
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SelfImprovementLifecycle:
        """Rehydrate and validate a lifecycle snapshot from project config."""

        lifecycle = cls.model_validate(dict(payload))
        lifecycle.assert_invariants()
        return lifecycle

    def _require_project(self) -> None:
        if self.project_id is None:
            raise SelfImprovementTransitionError("a canonical project must be bound first")

    def _require_technical_gates(self) -> None:
        missing = [
            name.value for name in _TECHNICAL_GATES if self.gates[name].status != GateStatus.PASSED
        ]
        if missing:
            raise SelfImprovementTransitionError(
                f"technical gates are not all passed: {', '.join(missing)}"
            )

    def _latest_observation(self, stage: Literal["shadow", "canary"]) -> RolloutObservation:
        for observation in reversed(self.observations):
            if observation.stage == stage:
                return observation
        raise SelfImprovementTransitionError(f"missing {stage} observation")

    @staticmethod
    def _require_safe_observation(observation: RolloutObservation) -> None:
        if observation.regression_fraction > 0.10:
            raise SelfImprovementTransitionError("regression threshold exceeded")
        if observation.irreversible_side_effects:
            raise SelfImprovementTransitionError("irreversible side effects block promotion")

    def _transition(
        self,
        target: ImprovementStatus,
        *,
        actor: str,
        actor_kind: ActorKind,
        reason: str | None,
    ) -> None:
        if not actor.strip():
            raise SelfImprovementAuthorityError("lifecycle transition requires an actor")
        prior = self.status
        self.status = target
        self.history.append(
            LifecycleTransition(
                from_status=prior,
                to_status=target,
                actor=actor.strip(),
                actor_kind=actor_kind,
                reason=reason,
            )
        )
        self.assert_invariants()


__all__ = [
    "SELF_IMPROVEMENT_SCHEMA",
    "ActorKind",
    "GateName",
    "GateRecord",
    "GateStatus",
    "ImprovementArtifact",
    "ImprovementArtifactBundle",
    "ImprovementArtifactKind",
    "ImprovementArtifactReadback",
    "ImprovementOpportunity",
    "ImprovementOutcome",
    "ImprovementOutcomeKind",
    "ImprovementRisk",
    "ImprovementStatus",
    "LifecycleTransition",
    "RolloutObservation",
    "SelfImprovementAuthorityError",
    "SelfImprovementLifecycle",
    "SelfImprovementTransitionError",
]
