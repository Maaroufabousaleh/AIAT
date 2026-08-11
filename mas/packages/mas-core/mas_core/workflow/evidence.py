"""Policy-driven project completion and evidence completeness."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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


def evidence_policy_catalog() -> dict[str, Any]:
    """Return the selectable built-in evidence policies for operator clients."""

    return {
        "schema_version": "aiat.evidence-policy.v1",
        "policies": {
            policy_id: policy.model_dump(mode="json")
            for policy_id, policy in sorted(DEFAULT_EVIDENCE_POLICIES.items())
        },
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


EVIDENCE_PACKAGE_SCHEMA = "aiat.project-evidence-package.v1"
_PACKAGE_CATEGORIES = (
    "repository",
    "documents",
    "tests",
    "security",
    "deployment",
    "cost",
    "approvals",
    "flow",
    "workers",
    "artifacts",
    "audit",
)


class EvidencePackageCategory(BaseModel):
    """One bounded, read-only category in a project evidence package."""

    category: str
    status: str
    required: bool = False
    item_count: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str | None = None


class EvidencePackageItem(BaseModel):
    """Secret-safe identity and integrity metadata for one evidence item."""

    id: str
    category: str
    kind: str
    status: str | None = None
    source: str | None = None
    producer: str | None = None
    occurred_at: str | None = None
    version: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidencePackage(BaseModel):
    """Canonical project evidence read model.

    The package is deliberately a projection over existing project, flow,
    worker, repository, artifact, usage, approval, and audit authorities.  It
    never creates a second completion predicate: ``checks`` and ``status``
    come from :func:`evaluate_project_evidence`, while categories only make
    the source coverage visible to operators.  Resource licence/restriction
    values, when present in item metadata, are notices and never gate status.
    """

    schema_version: str = EVIDENCE_PACKAGE_SCHEMA
    project_id: str
    generated_at: str | None = None
    policy_id: str
    policy_version: str
    status: str
    completeness_score: float
    checks: list[EvidenceCheck] = Field(default_factory=list)
    categories: list[EvidencePackageCategory] = Field(default_factory=list)
    items: list[EvidencePackageItem] = Field(default_factory=list)
    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)
    notices: list[dict[str, str]] = Field(default_factory=list)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _artifact_kind(artifact: Mapping[str, Any]) -> str:
    metadata = artifact.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return str(
        artifact.get("kind")
        or artifact.get("artifact_kind")
        or metadata.get("kind")
        or metadata.get("artifact_kind")
        or "artifact"
    ).strip().lower()


def _artifact_category(kind: str) -> str:
    tokens = {token for token in kind.replace("_", "-").split("-") if token}
    if tokens & {"test", "tests", "coverage", "pytest", "playwright", "qa"}:
        return "tests"
    if tokens & {"security", "semgrep", "scan", "vulnerability", "sbom", "sast"}:
        return "security"
    if tokens & {"deployment", "deploy", "release", "rollback", "rollout"}:
        return "deployment"
    if tokens & {"cost", "usage", "budget", "billing"}:
        return "cost"
    return "artifacts"


def _required_categories(policy: EvidencePolicy) -> set[str]:
    required = {"repository"} if policy.requires_repository else set()
    if policy.requires_documents or policy.required_document_types:
        required.add("documents")
    if policy.requires_artifacts:
        required.add("artifacts")
    for kind in policy.required_artifact_kinds:
        required.add(_artifact_category(str(kind).strip().lower()))
    if policy.requires_flow_terminal:
        required.add("flow")
    if policy.requires_approvals_closed:
        required.add("approvals")
    if policy.requires_audit:
        required.add("audit")
    return required


def _package_notice(artifact: Mapping[str, Any]) -> list[dict[str, str]]:
    """Extract bounded resource notices without making them policy inputs."""

    metadata = artifact.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    artifact_id = _text(artifact.get("id")) or "unknown"
    notices: list[dict[str, str]] = []
    for key in ("license", "licence", "license_id", "restriction", "restrictions"):
        value = metadata.get(key)
        if value is None:
            continue
        rendered = str(value).strip()
        if rendered:
            notices.append({"artifact_id": artifact_id, "field": key, "value": rendered[:1000]})
    return notices


def build_evidence_package(
    *,
    completeness: EvidenceCompleteness,
    policy: EvidencePolicy | None = None,
    documents: Iterable[Mapping[str, Any]] = (),
    artifacts: Iterable[Mapping[str, Any]] = (),
    flow_instance: Mapping[str, Any] | None = None,
    approvals: Iterable[Mapping[str, Any]] = (),
    worker_runs: Iterable[Mapping[str, Any]] = (),
    repository: Mapping[str, Any] | None = None,
    audit_events: Iterable[Mapping[str, Any]] = (),
    usage: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> EvidencePackage:
    """Build a deterministic, secret-safe project evidence projection.

    Source rows are sorted by stable identity before they are emitted.  The
    evaluator remains the only completion authority; this function merely
    groups existing facts so repository, test, security, deployment, cost,
    and recovery evidence can be inspected in one response.
    """

    document_rows = [row for row in documents if isinstance(row, Mapping)]
    artifact_rows = [row for row in artifacts if isinstance(row, Mapping)]
    approval_rows = [row for row in approvals if isinstance(row, Mapping)]
    run_rows = [row for row in worker_runs if isinstance(row, Mapping)]
    audit_rows = [row for row in audit_events if isinstance(row, Mapping)]
    items: list[EvidencePackageItem] = []
    notices: list[dict[str, str]] = []

    def add_item(item: EvidencePackageItem) -> None:
        items.append(item)

    for row in sorted(document_rows, key=lambda value: str(value.get("id", ""))):
        add_item(
            EvidencePackageItem(
                id=_text(row.get("id")) or "unknown",
                category="documents",
                kind=str(row.get("doc_type") or "document").lower(),
                status=_text(row.get("status")),
                source="documents",
                producer=_text(row.get("created_by")),
                occurred_at=_text(row.get("updated_at") or row.get("created_at")),
                version=_text(row.get("version")),
                checksum=_text(row.get("blob_sha256")),
                metadata={"lineage_id": _text(row.get("lineage_id")) or _text(row.get("id"))},
            )
        )

    for row in sorted(artifact_rows, key=lambda value: str(value.get("id", ""))):
        kind = _artifact_kind(row)
        category = _artifact_category(kind)
        add_item(
            EvidencePackageItem(
                id=_text(row.get("id")) or "unknown",
                category=category,
                kind=kind,
                status=_text(row.get("status")),
                source="artifacts",
                producer=_text(row.get("agent_id")),
                occurred_at=_text(row.get("updated_at") or row.get("created_at")),
                checksum=_text(row.get("sha256")),
                size_bytes=int(row["size_bytes"]) if row.get("size_bytes") is not None else None,
                metadata={"path": _text(row.get("path")) or ""},
            )
        )
        notices.extend(_package_notice(row))

    if repository:
        add_item(
            EvidencePackageItem(
                id=_text(repository.get("id")) or "repository",
                category="repository",
                kind=str(repository.get("repository_mode") or "repository"),
                status="ready" if repository.get("initialized") else "uninitialized",
                source="project_repository_records",
                occurred_at=_text(repository.get("updated_at") or repository.get("created_at")),
                version=_text(repository.get("head_commit")),
                metadata={
                    "branch": _text(repository.get("branch")) or "",
                    "adapter_health": _text(repository.get("adapter_health")) or "unknown",
                    "dirty": str(bool(repository.get("dirty"))),
                },
            )
        )

    if flow_instance:
        add_item(
            EvidencePackageItem(
                id=_text(flow_instance.get("id")) or "flow",
                category="flow",
                kind="flow_instance",
                status=_text(flow_instance.get("status")),
                source="flow_instances",
                occurred_at=_text(flow_instance.get("updated_at") or flow_instance.get("created_at")),
                version=_text(flow_instance.get("flow_version")),
            )
        )

    for row in sorted(run_rows, key=lambda value: str(value.get("id", ""))):
        add_item(
            EvidencePackageItem(
                id=_text(row.get("id")) or "unknown",
                category="workers",
                kind="worker_run",
                status=_text(row.get("state")),
                source="worker_runs",
                producer=_text(row.get("worker_id") or row.get("created_by")),
                occurred_at=_text(row.get("updated_at") or row.get("created_at")),
                version=_text(row.get("worker_version") or row.get("adapter_version")),
            )
        )

    for row in sorted(approval_rows, key=lambda value: str(value.get("id", ""))):
        add_item(
            EvidencePackageItem(
                id=_text(row.get("id")) or "unknown",
                category="approvals",
                kind=str(row.get("gate_type") or "approval_gate"),
                status=_text(row.get("status")),
                source="approval_gates",
                producer=_text(row.get("decided_by")),
                occurred_at=_text(row.get("decided_at") or row.get("created_at")),
            )
        )

    for row in sorted(audit_rows, key=lambda value: str(value.get("occurred_at") or value.get("id") or "")):
        add_item(
            EvidencePackageItem(
                id=_text(row.get("id")) or f"audit:{len(items)}",
                category="audit",
                kind=str(row.get("event_type") or "audit_event"),
                status="recorded",
                source="project_history",
                producer=_text(row.get("actor")),
                occurred_at=_text(row.get("occurred_at")),
            )
        )

    if usage:
        usage_id = f"usage:{completeness.project_id}"
        add_item(
            EvidencePackageItem(
                id=usage_id,
                category="cost",
                kind="project_usage",
                status="available" if usage.get("available", True) else "unavailable",
                source=_text(usage.get("source")) or "project_usage_events",
                occurred_at=_text(usage.get("last_event_at") or usage.get("first_event_at")),
                metadata={
                    "llm_calls": str(int(usage.get("llm_calls") or 0)),
                    "tool_calls": str(int(usage.get("tool_calls") or 0)),
                    "total_tokens": str(int(usage.get("total_tokens") or 0)),
                    "total_cost_usd": str(usage.get("total_cost_usd") or 0),
                },
            )
        )

    counts = {category: 0 for category in _PACKAGE_CATEGORIES}
    refs: dict[str, list[str]] = {key: list(value) for key, value in completeness.evidence_refs.items()}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
        refs.setdefault(item.category, []).append(item.id)
        # ``artifacts`` is the general inventory category; classified
        # test/security/deployment/cost items remain visible in their more
        # useful category while still counting toward the general requirement.
        if item.source == "artifacts" and item.category != "artifacts":
            counts["artifacts"] += 1
            refs.setdefault("artifacts", []).append(item.id)
    for key, values in list(refs.items()):
        refs[key] = sorted(dict.fromkeys(str(value) for value in values))

    required = _required_categories(policy) if policy is not None else set()
    # The evaluator's checks are still authoritative.  Include their names as
    # a compatibility fallback for callers that only have a completeness row.
    check_names = {check.name for check in completeness.checks if check.required}
    if "repository_known" in check_names:
        required.add("repository")
    if "documents_exist" in check_names or "required_document_types" in check_names:
        required.add("documents")
    if "artifacts_registered" in check_names or "required_artifact_kinds" in check_names:
        required.add("artifacts")
    if "flow_terminal" in check_names:
        required.add("flow")
    if "approval_gates_closed" in check_names:
        required.add("approvals")
    if "terminal_audit_present" in check_names:
        required.add("audit")

    categories: list[EvidencePackageCategory] = []
    for category in _PACKAGE_CATEGORIES:
        category_refs = refs.get(category, [])
        is_required = category in required
        present = counts.get(category, 0) > 0
        categories.append(
            EvidencePackageCategory(
                category=category,
                status="present" if present else ("missing" if is_required else "not_required"),
                required=is_required,
                item_count=counts.get(category, 0),
                evidence_refs=category_refs,
                reason=("required evidence is missing" if is_required and not present else None),
            )
        )

    items.sort(key=lambda item: (item.category, item.id, item.kind))
    return EvidencePackage(
        project_id=completeness.project_id,
        generated_at=generated_at,
        policy_id=completeness.policy_id,
        policy_version=completeness.policy_version,
        status=completeness.status,
        completeness_score=completeness.completeness_score,
        checks=completeness.checks,
        categories=categories,
        items=items,
        evidence_refs=refs,
        notices=sorted(notices, key=lambda notice: (notice["artifact_id"], notice["field"])),
    )


def policy_for(policy_id: str, *, version: str | None = None, requirements: dict[str, Any] | None = None) -> EvidencePolicy:
    if requirements is not None:
        return EvidencePolicy(policy_id=policy_id, version=version or "1.0", **requirements)
    return DEFAULT_EVIDENCE_POLICIES.get(policy_id, DEFAULT_EVIDENCE_POLICIES["manual"])


def _usable_policy_selection(selection: Any) -> bool:
    """Return whether a persisted policy selection can participate in resolution."""

    if isinstance(selection, str):
        return bool(selection.strip())
    if isinstance(selection, Mapping):
        return bool(selection)
    return selection is not None


def _milestone_policy_selection(
    policies: Mapping[str, Any] | None,
    milestone: str | None,
) -> Any:
    if not isinstance(policies, Mapping) or not isinstance(milestone, str):
        return None
    name = milestone.strip()
    if not name:
        return None
    selection = policies.get(name)
    return selection if _usable_policy_selection(selection) else None


def resolve_evidence_policy_selection(
    *,
    milestone: str | None = None,
    project_milestone_policies: Mapping[str, Any] | None = None,
    project_selection: Any = None,
    flow_selection: Any = None,
    company_milestone_policies: Mapping[str, Any] | None = None,
    company_selection: Any = None,
) -> tuple[Any, str]:
    """Resolve one policy selection using the canonical scope precedence.

    The order is deliberately explicit and deterministic: project milestone,
    project default, flow metadata, company milestone, company default, then
    the safe ``manual`` fallback.  This helper only selects a policy; it does
    not evaluate evidence, persist state, or inspect resource licence fields.
    The returned source is operator-facing provenance for the selected value.
    """

    candidates = (
        ("project_milestone", _milestone_policy_selection(project_milestone_policies, milestone)),
        ("project", project_selection),
        ("flow", flow_selection),
        ("company_milestone", _milestone_policy_selection(company_milestone_policies, milestone)),
        ("company", company_selection),
    )
    for source, selection in candidates:
        if _usable_policy_selection(selection):
            return selection, source
    return "manual", "fallback"


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
    if policy.required_artifact_kinds:
        refs["artifacts"] = [str(artifact.get("id")) for artifact in artifacts]
        present = {str(artifact.get("kind") or artifact.get("artifact_kind") or "").lower() for artifact in artifacts}
        missing = [kind for kind in policy.required_artifact_kinds if str(kind).lower() not in present]
        checks.append(
            EvidenceCheck(
                name="required_artifact_kinds",
                required=True,
                passed=not missing,
                reason=f"missing artifact kinds: {', '.join(missing)}" if missing else None,
                evidence_refs=refs["artifacts"],
            )
        )
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
