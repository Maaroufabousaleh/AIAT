"""External worker stewards, immutable candidates, certification, and rollout.

The steward is an AIAT-owned governance runtime. It can inspect untrusted
upstream material and prepare candidates, but only explicit certification,
approval, and rollout transitions change the active bundle/adapter references.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Iterable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mas_core.worker_contract import ConformanceReport, WorkerCapabilities


class StewardStatus(StrEnum):
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class CandidateIntakeStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    SOURCE_REVIEW = "SOURCE_REVIEW"
    LICENSE_REVIEW = "LICENSE_REVIEW"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    INTERFACE_RESEARCH = "INTERFACE_RESEARCH"
    GENERATED = "GENERATED"
    CERTIFYING = "CERTIFYING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class BundleStatus(StrEnum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    CERTIFIED = "CERTIFIED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class RolloutStatus(StrEnum):
    PENDING = "PENDING"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    PROMOTING = "PROMOTING"
    ACTIVE = "ACTIVE"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"


class StewardTransitionError(ValueError):
    """Raised when a governance transition would bypass a gate."""


class ExternalProvenance(BaseModel):
    """Pinned identity and supply-chain evidence for an external runtime."""

    model_config = ConfigDict(extra="allow", frozen=True)

    canonical_source_repository: str
    source_provider: str = "unknown"
    exact_release: str | None = None
    commit_sha: str | None = None
    package_version: str | None = None
    oci_image_digest: str | None = None
    dependency_lock_hash: str | None = None
    protocol_api_version: str | None = None
    adapter_version: str | None = None
    transport_type: str
    runtime_fingerprint: str | None = None
    license_id: str | None = None
    redistribution_status: str = "pending"
    security_scan_status: str = "pending"
    documentation_snapshot_version: str | None = None
    last_verified_documentation_at: datetime | None = None

    @model_validator(mode="after")
    def require_pin(self) -> "ExternalProvenance":
        if not any((self.exact_release, self.commit_sha, self.package_version, self.oci_image_digest)):
            raise ValueError("external provenance requires an exact release, commit, package, or OCI digest")
        if self.transport_type == "oci" and not self.oci_image_digest:
            raise ValueError("OCI provenance requires an image digest")
        return self


class DocumentationSource(BaseModel):
    source_id: UUID = Field(default_factory=uuid4)
    uri: str
    source_type: str = "official"
    trusted: bool = False
    allowed_domains: tuple[str, ...] = ()


class DocumentationSnapshot(BaseModel):
    snapshot_id: UUID = Field(default_factory=uuid4)
    source: DocumentationSource
    version: str
    content_sha256: str
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    content_ref: str | None = None
    extracted_interfaces: dict[str, Any] = Field(default_factory=dict)
    security_findings: tuple[str, ...] = ()
    # External docs are data, never executable instructions.
    untrusted: bool = True

    @field_validator("content_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("documentation snapshot hash must be SHA-256")
        return value.lower()


class CapabilitySnapshot(BaseModel):
    snapshot_id: UUID = Field(default_factory=uuid4)
    version: str
    capabilities: WorkerCapabilities
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence_refs: tuple[str, ...] = ()


class CompatibilityMatrix(BaseModel):
    matrix_id: UUID = Field(default_factory=uuid4)
    runtime_version: str
    adapter_version: str
    contract_version: str
    model_profiles: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    capabilities: dict[str, str] = Field(default_factory=dict)
    fixtures: tuple[str, ...] = ()
    passed: bool = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SkillBundle(BaseModel):
    """Immutable generated runtime knowledge and procedures."""

    model_config = ConfigDict(extra="allow", frozen=True)

    bundle_id: UUID = Field(default_factory=uuid4)
    semantic_version: str
    upstream_compatibility_range: str
    steward_id: UUID
    source_provenance: ExternalProvenance
    documentation_refs: tuple[UUID, ...] = ()
    verified_capabilities: CapabilitySnapshot | None = None
    model_requirements: frozenset[str] = frozenset()
    command_templates: dict[str, Any] = Field(default_factory=dict)
    api_request_templates: dict[str, Any] = Field(default_factory=dict)
    event_parsers: dict[str, Any] = Field(default_factory=dict)
    configuration_schema: dict[str, Any] = Field(default_factory=dict)
    permission_mappings: dict[str, Any] = Field(default_factory=dict)
    sandbox_requirements: dict[str, Any] = Field(default_factory=dict)
    tool_mappings: dict[str, Any] = Field(default_factory=dict)
    workspace_behavior: dict[str, Any] = Field(default_factory=dict)
    checkpoint_recovery: dict[str, Any] = Field(default_factory=dict)
    error_taxonomy: dict[str, Any] = Field(default_factory=dict)
    known_limitations: tuple[str, ...] = ()
    evaluation_fixtures: tuple[str, ...] = ()
    migration_notes: tuple[str, ...] = ()
    generated_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    status: BundleStatus = BundleStatus.DRAFT

    @property
    def content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"bundle_id", "status"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AdapterCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")

    adapter_id: UUID = Field(default_factory=uuid4)
    version: str
    steward_id: UUID
    source_provenance: ExternalProvenance
    transport_type: str
    entrypoint: str | None = None
    implementation_ref: str | None = None
    content_hash: str
    status: BundleStatus = BundleStatus.DRAFT
    conformance_report: dict[str, Any] | None = None


class CandidateRecord(BaseModel):
    candidate_id: UUID = Field(default_factory=uuid4)
    worker_id: str
    steward_id: UUID
    intake_status: CandidateIntakeStatus = CandidateIntakeStatus.DISCOVERED
    bundle: SkillBundle
    adapter: AdapterCandidate
    source_provenance: ExternalProvenance
    diff: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    certification_id: UUID | None = None
    approval_record_id: UUID | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CertificationRun(BaseModel):
    certification_id: UUID = Field(default_factory=uuid4)
    candidate_id: UUID
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    conformance: dict[str, Any]
    checks: dict[str, bool] = Field(default_factory=dict)
    failures: tuple[str, ...] = ()
    passed: bool = False
    approved_by: str | None = None


class RolloutRecord(BaseModel):
    rollout_id: UUID = Field(default_factory=uuid4)
    worker_id: str
    steward_id: UUID
    candidate_id: UUID
    status: RolloutStatus = RolloutStatus.PENDING
    eligible_task_classes: tuple[str, ...] = ()
    shadow_sample_target: int = 10
    readonly_canary_sample_target: int = 5
    live_canary_sample_target: int = 3
    sample_count: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    comparison_metrics: dict[str, float] = Field(default_factory=dict)
    rollback_thresholds: dict[str, float] = Field(default_factory=lambda: {"regression_fraction": 0.10})
    in_flight_policy: str = "finish_pinned_version"
    promotion_actor: str | None = None
    rollback_reason: str | None = None
    # These are captured when a candidate becomes active so a direct steward
    # rollback can restore the exact prior immutable pointers.  The API also
    # persists the corresponding registry pointers; keeping the snapshots on
    # the in-memory record makes the domain object safe on its own.
    previous_active_bundle: SkillBundle | None = None
    previous_active_adapter: AdapterCandidate | None = None
    previous_provenance: ExternalProvenance | None = None


_STEWard_TRANSITIONS: dict[StewardStatus, set[StewardStatus]] = {
    StewardStatus.PROVISIONING: {StewardStatus.READY, StewardStatus.DEGRADED, StewardStatus.SUSPENDED},
    StewardStatus.READY: {StewardStatus.DEGRADED, StewardStatus.SUSPENDED, StewardStatus.RETIRED},
    StewardStatus.DEGRADED: {StewardStatus.READY, StewardStatus.SUSPENDED, StewardStatus.RETIRED},
    StewardStatus.SUSPENDED: {StewardStatus.READY, StewardStatus.RETIRED},
    StewardStatus.RETIRED: set(),
}


class ExternalWorkerSteward:
    """One dedicated steward for one external worker."""

    def __init__(
        self,
        *,
        worker_id: str,
        provenance: ExternalProvenance,
        steward_id: UUID | None = None,
        official_documentation: Iterable[DocumentationSource] = (),
        status: StewardStatus = StewardStatus.PROVISIONING,
    ) -> None:
        self.worker_id = worker_id
        self.steward_id = steward_id or uuid4()
        self.provenance = provenance
        self.status = status
        self.documentation_sources = list(official_documentation)
        self.documentation_snapshots: list[DocumentationSnapshot] = []
        self.candidates: dict[UUID, CandidateRecord] = {}
        self.certifications: dict[UUID, CertificationRun] = {}
        self.rollouts: dict[UUID, RolloutRecord] = {}
        self.active_bundle: SkillBundle | None = None
        self.active_adapter: AdapterCandidate | None = None
        self.capability_snapshots: list[CapabilitySnapshot] = []
        self.compatibility_matrices: list[CompatibilityMatrix] = []
        self.transition_history: list[dict[str, Any]] = []

    def transition(self, target: StewardStatus, *, actor: str, reason: str | None = None) -> None:
        if target not in _STEWard_TRANSITIONS[self.status]:
            raise StewardTransitionError(f"invalid steward transition {self.status} -> {target}")
        prior = self.status
        self.status = target
        self.transition_history.append({"from": prior, "to": target, "actor": actor, "reason": reason, "at": datetime.now(UTC).isoformat()})

    def add_documentation_snapshot(self, snapshot: DocumentationSnapshot) -> None:
        if snapshot.source.trusted is False and snapshot.source.source_type == "official":
            # Official means provenance, not executable trust. Keep it explicitly
            # untrusted so generated instructions cannot bypass review.
            snapshot.untrusted = True
        self.documentation_snapshots.append(snapshot)
        self.provenance = self.provenance.model_copy(update={
            "documentation_snapshot_version": snapshot.version,
            "last_verified_documentation_at": snapshot.captured_at,
        })

    def record_capabilities(self, snapshot: CapabilitySnapshot) -> None:
        self.capability_snapshots.append(snapshot)

    def generate_candidate(
        self,
        *,
        semantic_version: str,
        adapter_version: str,
        upstream_compatibility_range: str,
        adapter_entrypoint: str | None = None,
        implementation_ref: str | None = None,
        diff: dict[str, Any] | None = None,
        migration_notes: Iterable[str] = (),
    ) -> CandidateRecord:
        if self.status in {StewardStatus.SUSPENDED, StewardStatus.RETIRED}:
            raise StewardTransitionError("cannot generate a candidate from a suspended or retired steward")
        bundle = SkillBundle(
            semantic_version=semantic_version,
            upstream_compatibility_range=upstream_compatibility_range,
            steward_id=self.steward_id,
            source_provenance=self.provenance,
            documentation_refs=tuple(snapshot.snapshot_id for snapshot in self.documentation_snapshots),
            verified_capabilities=self.capability_snapshots[-1] if self.capability_snapshots else None,
            model_requirements=frozenset(self.capability_snapshots[-1].capabilities.required_model_capabilities) if self.capability_snapshots else frozenset(),
            known_limitations=(),
            migration_notes=tuple(migration_notes),
            status=BundleStatus.DRAFT,
        )
        adapter = AdapterCandidate(
            version=adapter_version,
            steward_id=self.steward_id,
            source_provenance=self.provenance,
            transport_type=self.provenance.transport_type,
            entrypoint=adapter_entrypoint,
            implementation_ref=implementation_ref,
            content_hash=hashlib.sha256(f"{adapter_version}:{implementation_ref or ''}".encode()).hexdigest(),
        )
        candidate = CandidateRecord(
            worker_id=self.worker_id,
            steward_id=self.steward_id,
            bundle=bundle,
            adapter=adapter,
            source_provenance=self.provenance,
            diff=diff or {},
        )
        self.candidates[candidate.candidate_id] = candidate
        return candidate

    def advance_candidate(self, candidate_id: UUID, target: CandidateIntakeStatus) -> CandidateRecord:
        candidate = self._candidate(candidate_id)
        target = CandidateIntakeStatus(target)
        legal: dict[CandidateIntakeStatus, set[CandidateIntakeStatus]] = {
            CandidateIntakeStatus.DISCOVERED: {CandidateIntakeStatus.SOURCE_REVIEW, CandidateIntakeStatus.BLOCKED},
            CandidateIntakeStatus.SOURCE_REVIEW: {CandidateIntakeStatus.LICENSE_REVIEW, CandidateIntakeStatus.BLOCKED},
            CandidateIntakeStatus.LICENSE_REVIEW: {CandidateIntakeStatus.SECURITY_REVIEW, CandidateIntakeStatus.BLOCKED},
            CandidateIntakeStatus.SECURITY_REVIEW: {CandidateIntakeStatus.INTERFACE_RESEARCH, CandidateIntakeStatus.BLOCKED},
            CandidateIntakeStatus.INTERFACE_RESEARCH: {CandidateIntakeStatus.GENERATED, CandidateIntakeStatus.BLOCKED},
            CandidateIntakeStatus.GENERATED: {CandidateIntakeStatus.CERTIFYING, CandidateIntakeStatus.BLOCKED},
            CandidateIntakeStatus.CERTIFYING: {CandidateIntakeStatus.APPROVED, CandidateIntakeStatus.REJECTED, CandidateIntakeStatus.BLOCKED},
            CandidateIntakeStatus.APPROVED: set(),
            CandidateIntakeStatus.REJECTED: set(),
            CandidateIntakeStatus.BLOCKED: set(),
        }
        if target not in legal[candidate.intake_status]:
            raise StewardTransitionError(f"invalid candidate transition {candidate.intake_status} -> {target}")
        candidate.intake_status = target
        return candidate

    def certify_candidate(
        self,
        candidate_id: UUID,
        *,
        conformance: ConformanceReport | dict[str, Any],
        checks: dict[str, bool],
        approved_by: str | None = None,
    ) -> CertificationRun:
        candidate = self._candidate(candidate_id)
        if candidate.intake_status != CandidateIntakeStatus.CERTIFYING:
            raise StewardTransitionError("candidate must be in CERTIFYING before certification")
        conformance_data = conformance.as_dict() if isinstance(conformance, ConformanceReport) else dict(conformance)
        # Provenance gates are derived from the immutable steward evidence,
        # never trusted from caller-selected check names.  Supplemental
        # attestations may make certification stricter but cannot manufacture
        # a passing license or security result.
        mandatory_checks = {
            "provenance_pin": bool(
                self.provenance.exact_release
                or self.provenance.commit_sha
                or self.provenance.package_version
                or self.provenance.oci_image_digest
            ),
            "license": bool(self.provenance.license_id)
            and self.provenance.redistribution_status == "approved",
            "security": self.provenance.security_scan_status == "passed",
        }
        effective_checks = {**dict(checks), **mandatory_checks}
        passed = bool(conformance_data.get("passed")) and all(effective_checks.values())
        failures = tuple(sorted([name for name, result in effective_checks.items() if not result] + ([] if conformance_data.get("passed") else ["adapter_conformance"])))
        certification = CertificationRun(
            candidate_id=candidate_id,
            conformance=conformance_data,
            checks=effective_checks,
            failures=failures,
            passed=passed,
            completed_at=datetime.now(UTC),
            # Certification and approval are separate governance operations.
            # Keep this field for wire/storage compatibility, but never let a
            # certification request promote the candidate.
            approved_by=None,
        )
        self.certifications[certification.certification_id] = certification
        candidate.certification_id = certification.certification_id
        candidate.evidence["certification"] = certification.model_dump(mode="json")
        if passed:
            candidate.intake_status = CandidateIntakeStatus.CERTIFYING
            candidate.bundle = candidate.bundle.model_copy(update={"status": BundleStatus.CERTIFIED})
            candidate.adapter = candidate.adapter.model_copy(update={"status": BundleStatus.CERTIFIED, "conformance_report": conformance_data})
        else:
            candidate.intake_status = CandidateIntakeStatus.REJECTED
        return certification

    def approve_candidate(self, candidate_id: UUID, *, approval_record_id: UUID | None = None) -> CandidateRecord:
        candidate = self._candidate(candidate_id)
        certification = self.certifications.get(candidate.certification_id) if candidate.certification_id else None
        if certification is None or not certification.passed:
            raise StewardTransitionError("candidate approval requires a passed certification")
        if candidate.intake_status != CandidateIntakeStatus.CERTIFYING and candidate.intake_status != CandidateIntakeStatus.APPROVED:
            raise StewardTransitionError("candidate is not eligible for approval")
        candidate.intake_status = CandidateIntakeStatus.APPROVED
        candidate.approval_record_id = approval_record_id or uuid4()
        candidate.bundle = candidate.bundle.model_copy(update={"status": BundleStatus.APPROVED})
        candidate.adapter = candidate.adapter.model_copy(update={"status": BundleStatus.APPROVED})
        return candidate

    def start_rollout(self, candidate_id: UUID, *, actor: str, eligible_task_classes: Iterable[str] = ()) -> RolloutRecord:
        candidate = self._candidate(candidate_id)
        if candidate.intake_status != CandidateIntakeStatus.APPROVED:
            raise StewardTransitionError("only approved candidates may enter rollout")
        if any(record.candidate_id == candidate_id for record in self.rollouts.values()):
            # A rollout is immutable evidence for one exact candidate.  Retrying
            # that candidate would collide with the persisted
            # (worker_id, candidate_id) uniqueness contract and, more
            # importantly, would erase the distinction between a rolled-back
            # artifact and a newly reviewed one.  A retry therefore requires a
            # new immutable candidate.
            raise StewardTransitionError(
                "candidate already has rollout history; generate and approve a new immutable candidate"
            )
        if any(record.status in {RolloutStatus.PENDING, RolloutStatus.SHADOW, RolloutStatus.CANARY, RolloutStatus.PROMOTING} for record in self.rollouts.values()):
            raise StewardTransitionError("another rollout is already in progress for this worker")
        rollout = RolloutRecord(
            worker_id=self.worker_id,
            steward_id=self.steward_id,
            candidate_id=candidate_id,
            eligible_task_classes=tuple(eligible_task_classes),
            promotion_actor=actor,
        )
        self.rollouts[rollout.rollout_id] = rollout
        return rollout

    def advance_rollout(self, rollout_id: UUID, target: RolloutStatus, *, sample_count: int | None = None, metrics: dict[str, float] | None = None) -> RolloutRecord:
        rollout = self._rollout(rollout_id)
        legal = {
            RolloutStatus.PENDING: {RolloutStatus.SHADOW, RolloutStatus.ROLLING_BACK},
            RolloutStatus.SHADOW: {RolloutStatus.CANARY, RolloutStatus.ROLLING_BACK},
            RolloutStatus.CANARY: {RolloutStatus.PROMOTING, RolloutStatus.ROLLING_BACK},
            RolloutStatus.PROMOTING: {RolloutStatus.ACTIVE, RolloutStatus.ROLLING_BACK},
            RolloutStatus.ACTIVE: {RolloutStatus.ROLLING_BACK},
            RolloutStatus.ROLLING_BACK: {RolloutStatus.ROLLED_BACK},
            RolloutStatus.ROLLED_BACK: set(),
        }
        if target not in legal[rollout.status]:
            raise StewardTransitionError(f"invalid rollout transition {rollout.status} -> {target}")
        next_sample_count = rollout.sample_count if sample_count is None else sample_count
        if target == RolloutStatus.CANARY:
            if next_sample_count < rollout.shadow_sample_target:
                raise StewardTransitionError(
                    f"shadow rollout requires at least {rollout.shadow_sample_target} samples before canary"
                )
            if not rollout.eligible_task_classes or any(
                task_class not in {"read_only", "low_risk", "shadow"}
                for task_class in rollout.eligible_task_classes
            ):
                raise StewardTransitionError("early canary is limited to explicitly read-only or low-risk task classes")
        if target == RolloutStatus.PROMOTING and next_sample_count < rollout.readonly_canary_sample_target:
            raise StewardTransitionError(
                f"read-only canary requires at least {rollout.readonly_canary_sample_target} samples before promotion"
            )
        if target == RolloutStatus.ACTIVE:
            if next_sample_count < rollout.live_canary_sample_target:
                raise StewardTransitionError(
                    f"live canary requires at least {rollout.live_canary_sample_target} samples before activation"
                )
            supplied_metrics = dict(metrics or rollout.comparison_metrics)
            regression = float(supplied_metrics.get("regression_fraction", 0.0))
            threshold = float(rollout.rollback_thresholds.get("regression_fraction", 0.10))
            if regression > threshold:
                raise StewardTransitionError(
                    f"rollout regression {regression:.4f} exceeds rollback threshold {threshold:.4f}"
                )
            if float(supplied_metrics.get("irreversible_side_effects", 0.0)) > 0:
                raise StewardTransitionError("candidate produced irreversible side effects during canary")
        rollout.status = target
        if sample_count is not None:
            rollout.sample_count = sample_count
        if metrics:
            rollout.comparison_metrics.update(metrics)
        if target == RolloutStatus.ACTIVE:
            candidate = self._candidate(rollout.candidate_id)
            rollout.previous_active_bundle = self.active_bundle
            rollout.previous_active_adapter = self.active_adapter
            rollout.previous_provenance = self.provenance
            self.active_bundle = candidate.bundle
            self.active_adapter = candidate.adapter
            self.provenance = candidate.source_provenance
            candidate.bundle = candidate.bundle.model_copy(update={"status": BundleStatus.APPROVED})
        if target == RolloutStatus.ROLLED_BACK:
            self.active_bundle = rollout.previous_active_bundle
            self.active_adapter = rollout.previous_active_adapter
            if rollout.previous_provenance is not None:
                self.provenance = rollout.previous_provenance
            rollout.completed_at = datetime.now(UTC)
        return rollout

    def rollback(self, rollout_id: UUID, *, reason: str) -> RolloutRecord:
        rollout = self._rollout(rollout_id)
        if rollout.status not in {RolloutStatus.PENDING, RolloutStatus.SHADOW, RolloutStatus.CANARY, RolloutStatus.PROMOTING, RolloutStatus.ACTIVE}:
            raise StewardTransitionError("rollout cannot be rolled back from its current state")
        rollout.rollback_reason = reason
        self.advance_rollout(rollout_id, RolloutStatus.ROLLING_BACK)
        return self.advance_rollout(rollout_id, RolloutStatus.ROLLED_BACK)

    def _candidate(self, candidate_id: UUID) -> CandidateRecord:
        try:
            return self.candidates[candidate_id]
        except KeyError as exc:
            raise StewardTransitionError(f"candidate {candidate_id} not found") from exc

    def _rollout(self, rollout_id: UUID) -> RolloutRecord:
        try:
            return self.rollouts[rollout_id]
        except KeyError as exc:
            raise StewardTransitionError(f"rollout {rollout_id} not found") from exc

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "steward_id": str(self.steward_id),
            "worker_id": self.worker_id,
            "status": self.status,
            "active_bundle_id": str(self.active_bundle.bundle_id) if self.active_bundle else None,
            "active_adapter_id": str(self.active_adapter.adapter_id) if self.active_adapter else None,
            "candidate_count": len(self.candidates),
            "pending_rollouts": sum(1 for record in self.rollouts.values() if record.status in {RolloutStatus.PENDING, RolloutStatus.SHADOW, RolloutStatus.CANARY, RolloutStatus.PROMOTING}),
            "provenance": self.provenance.model_dump(mode="json"),
        }


class OpenCodeSteward(ExternalWorkerSteward):
    """Steward specialization that requires an approved interface report."""

    def __init__(self, *, interface_verification: Any, **kwargs: Any) -> None:
        if not getattr(interface_verification, "approved", False):
            raise StewardTransitionError("OpenCode steward requires an approved interface verification report")
        super().__init__(**kwargs)
        self.interface_verification = interface_verification
