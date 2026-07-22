"""Policy-driven project completion and evidence completeness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class EvidencePolicy(BaseModel):
    policy_id: str
    version: str
    requires_flow_terminal: bool = False
    requires_documents: bool = False
    required_document_types: tuple[str, ...] = ()
    requires_document_bodies: bool = False
    requires_artifacts: bool = False
    requires_repository: bool = False
    requires_approvals_closed: bool = True
    requires_audit: bool = True
    required_artifact_kinds: tuple[str, ...] = ()
    allow_active_runs: bool = False


DEFAULT_EVIDENCE_POLICIES: dict[str, EvidencePolicy] = {
    "software_delivery": EvidencePolicy(
        policy_id="software_delivery",
        version="1.0",
        requires_flow_terminal=True,
        requires_documents=True,
        required_document_types=("PDR", "CDR", "RR"),
        requires_document_bodies=True,
        requires_artifacts=True,
        requires_repository=True,
    ),
    "research": EvidencePolicy(policy_id="research", version="1.0", requires_documents=True, requires_document_bodies=True),
    "documentation": EvidencePolicy(policy_id="documentation", version="1.0", requires_documents=True, requires_document_bodies=True),
    "operations": EvidencePolicy(policy_id="operations", version="1.0", requires_flow_terminal=True, requires_artifacts=True),
    "manual": EvidencePolicy(policy_id="manual", version="1.0"),
    "legacy_import": EvidencePolicy(policy_id="legacy_import", version="1.0"),
}


class EvidenceCheck(BaseModel):
    name: str
    required: bool
    passed: bool
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceCompleteness(BaseModel):
    project_id: str
    policy_id: str
    policy_version: str
    status: str
    completeness_score: float
    checks: list[EvidenceCheck]
    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def can_complete(self) -> bool:
        return self.status == "complete"


def policy_for(policy_id: str, *, version: str | None = None, requirements: dict[str, Any] | None = None) -> EvidencePolicy:
    if requirements is not None:
        return EvidencePolicy(policy_id=policy_id, version=version or "1.0", **requirements)
    return DEFAULT_EVIDENCE_POLICIES.get(policy_id, DEFAULT_EVIDENCE_POLICIES["manual"])


def evaluate_project_evidence(
    *,
    project_id: str,
    policy: EvidencePolicy,
    project: dict[str, Any],
    documents: list[dict[str, Any]] = (),
    artifacts: list[dict[str, Any]] = (),
    flow_instance: dict[str, Any] | None = None,
    approvals: list[dict[str, Any]] = (),
    worker_runs: list[dict[str, Any]] = (),
    repository: dict[str, Any] | None = None,
    audit_events: list[dict[str, Any]] = (),
) -> EvidenceCompleteness:
    checks: list[EvidenceCheck] = []
    refs: dict[str, list[str]] = {}

    active_runs = [run for run in worker_runs if str(run.get("state", "")).upper() not in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}]
    checks.append(EvidenceCheck(name="worker_runs_terminal", required=not policy.allow_active_runs, passed=policy.allow_active_runs or not active_runs, reason="active worker runs remain" if active_runs else None, evidence_refs=[str(run.get("id")) for run in active_runs]))

    if policy.requires_flow_terminal:
        passed = flow_instance is not None and str(flow_instance.get("status", "")).upper() in {"COMPLETED", "FAILED", "CANCELLED"}
        refs["flow"] = [str(flow_instance.get("id"))] if flow_instance else []
        checks.append(EvidenceCheck(name="flow_terminal", required=True, passed=passed, reason=None if passed else "required flow is not terminal", evidence_refs=refs["flow"]))
    if policy.requires_documents:
        refs["documents"] = [str(doc.get("id")) for doc in documents]
        checks.append(EvidenceCheck(name="documents_exist", required=True, passed=bool(documents), reason=None if documents else "required documents are missing", evidence_refs=refs["documents"]))
    if policy.required_document_types:
        present = {str(doc.get("doc_type", "")).upper() for doc in documents}
        missing = [doc_type for doc_type in policy.required_document_types if doc_type.upper() not in present]
        checks.append(EvidenceCheck(name="required_document_types", required=True, passed=not missing, reason=f"missing document types: {', '.join(missing)}" if missing else None, evidence_refs=refs.get("documents", [])))
    if policy.requires_document_bodies:
        body_docs = [doc for doc in documents if doc.get("blob_key") or doc.get("content_text")]
        checks.append(EvidenceCheck(name="document_bodies_retrievable", required=True, passed=len(body_docs) == len(documents) and bool(documents), reason="one or more document bodies cannot be retrieved" if len(body_docs) != len(documents) or not documents else None, evidence_refs=[str(doc.get("id")) for doc in body_docs]))
    if policy.requires_artifacts:
        refs["artifacts"] = [str(artifact.get("id")) for artifact in artifacts]
        checks.append(EvidenceCheck(name="artifacts_registered", required=True, passed=bool(artifacts), reason=None if artifacts else "required artifacts are missing", evidence_refs=refs["artifacts"]))
    if policy.requires_repository:
        passed = bool(repository and repository.get("initialized") and repository.get("adapter_health", "unknown") not in {"error", "unreachable"})
        refs["repository"] = [str(repository.get("id"))] if repository else []
        checks.append(EvidenceCheck(name="repository_known", required=True, passed=passed, reason=None if passed else "repository state is not known/initialized", evidence_refs=refs["repository"]))
    if policy.requires_approvals_closed:
        pending = [gate for gate in approvals if str(gate.get("status", "")).upper() in {"PENDING", "IN_PROGRESS"}]
        checks.append(EvidenceCheck(name="approval_gates_closed", required=True, passed=not pending, reason="pending approval gates remain" if pending else None, evidence_refs=[str(gate.get("id")) for gate in pending]))
    if policy.requires_audit:
        checks.append(EvidenceCheck(name="terminal_audit_present", required=True, passed=bool(audit_events), reason=None if audit_events else "terminal audit evidence is missing", evidence_refs=[str(event.get("id")) for event in audit_events]))

    required = [check for check in checks if check.required]
    passed_count = sum(1 for check in required if check.passed)
    score = passed_count / len(required) if required else 1.0
    status = "complete" if all(check.passed for check in required) else ("legacy/incomplete evidence" if policy.policy_id == "legacy_import" else "incomplete")
    return EvidenceCompleteness(project_id=project_id, policy_id=policy.policy_id, policy_version=policy.version, status=status, completeness_score=score, checks=checks, evidence_refs=refs)
