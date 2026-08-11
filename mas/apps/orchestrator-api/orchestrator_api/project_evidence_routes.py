"""Project evidence-package and evidence-policy API routes.

This router is intentionally kept separate from the large orchestrator module.
It projects existing storage authorities through the deterministic core model;
it does not create a second completion predicate.  Resource licence or
restriction values are carried only as bounded package notices.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from mas_core.company_manifest import DEFAULT_COMPANY_ID
from mas_core.workflow import (
    build_evidence_package,
    evidence_policy_catalog,
    evaluate_project_evidence,
    policy_for,
    resolve_evidence_policy_selection,
)

router = APIRouter(tags=["project-evidence"])


class ProjectEvidencePolicyRequest(BaseModel):
    """A policy selection persisted at project or milestone scope."""

    policy_id: str = Field(min_length=1, max_length=160)
    policy_version: str = Field(default="1.0", min_length=1, max_length=32)
    requirements: dict[str, Any] = Field(default_factory=dict)
    scope: Literal["project", "milestone"] = "project"
    milestone: str | None = Field(default=None, max_length=160)

    @field_validator("milestone")
    @classmethod
    def normalize_milestone(cls, value: str | None) -> str | None:
        normalized = value.strip() if isinstance(value, str) else value
        return normalized or None

    def validate_scope(self) -> None:
        if self.scope == "milestone" and not self.milestone:
            raise ValueError("milestone is required for milestone-scoped evidence policy")
        if self.scope == "project" and self.milestone:
            raise ValueError("milestone is only valid for milestone-scoped evidence policy")


def _main_module():
    """Load main lazily so importing this router cannot create a cycle."""

    from orchestrator_api import main

    return main


def _storage(request: Request) -> Any:
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise HTTPException(503, "Database not available")
    return storage


def _serialize(value: dict[str, Any]) -> dict[str, Any]:
    return _main_module()._serialize(value)


async def _maybe_call(storage: Any, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    method = getattr(storage, name, None)
    if not inspect.iscoroutinefunction(method):
        return default
    return await method(*args, **kwargs)


async def _artifact_rows(storage: Any, project_id: UUID) -> list[dict[str, Any]]:
    rows = await _maybe_call(storage, "list_artifacts", limit=100, default=[])
    project_key = str(project_id)
    result: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        path = str(row.get("path") or "")
        if (
            str(metadata.get("project_id") or "") == project_key
            or path.startswith(f"{project_key}/")
            or f"/{project_key}/" in path
        ):
            result.append(row)
    return result


def _manifest_scopes(record: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(record, dict):
        return {}, None
    manifest: Any = record.get("manifest_json")
    if not isinstance(manifest, dict):
        nested = record.get("manifest")
        manifest = nested.get("manifest_json") if isinstance(nested, dict) else None
    evidence = manifest.get("evidence_policy") if isinstance(manifest, dict) else None
    if not isinstance(evidence, dict):
        return {}, None
    milestones = evidence.get("milestone_policies")
    default = evidence.get("default_policy")
    return (
        milestones if isinstance(milestones, dict) else {},
        default if isinstance(default, dict) else None,
    )


async def _active_milestone(project: dict[str, Any], storage: Any) -> str | None:
    config = dict(project.get("config") or {})
    explicit = config.get("active_milestone") or project.get("milestone")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    rows = await _maybe_call(storage, "list_sprints", project["id"], default=[])
    candidates = [
        row
        for row in rows or []
        if isinstance(row, dict)
        and isinstance(row.get("milestone"), str)
        and row["milestone"].strip()
        and str(row.get("status") or "").upper() not in {"COMPLETED", "CLOSED", "CANCELLED"}
    ]
    candidates.sort(key=lambda row: int(row.get("sprint_number") or 0), reverse=True)
    return str(candidates[0]["milestone"]).strip() if candidates else None


async def _company_scopes(project: dict[str, Any], storage: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    raw_company_id = project.get("company_id") or DEFAULT_COMPANY_ID
    try:
        company_id = UUID(str(raw_company_id))
    except (TypeError, ValueError):
        return {}, None
    record = await _maybe_call(storage, "get_company_manifest", company_id, default=None)
    return _manifest_scopes(record)


async def _collect(project_id: UUID, storage: Any) -> tuple[dict[str, Any], Any, dict[str, Any], Any]:
    project = await _maybe_call(storage, "get_project", project_id, default=None)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    config = dict(project.get("config") or {})
    flow_instance = await _maybe_call(storage, "get_flow_instance_by_project", project_id, default=None)
    milestone = await _active_milestone(project, storage)
    flow_selection: Any = None
    if isinstance(flow_instance, dict):
        flow = await _maybe_call(storage, "get_flow", flow_instance.get("flow_id"), default=None)
        definition = flow.get("definition_json") if isinstance(flow, dict) else None
        metadata = definition.get("metadata") if isinstance(definition, dict) else None
        flow_selection = metadata.get("evidence_policy") if isinstance(metadata, dict) else None

    project_milestones = config.get("evidence_policy_milestones")
    selected, source = resolve_evidence_policy_selection(
        milestone=milestone,
        project_milestone_policies=project_milestones if isinstance(project_milestones, dict) else None,
        project_selection=config.get("evidence_policy"),
        flow_selection=flow_selection,
    )
    if source == "fallback":
        company_milestones, company_default = await _company_scopes(project, storage)
        selected, _source = resolve_evidence_policy_selection(
            milestone=milestone,
            project_milestone_policies=project_milestones if isinstance(project_milestones, dict) else None,
            project_selection=config.get("evidence_policy"),
            flow_selection=flow_selection,
            company_milestone_policies=company_milestones,
            company_selection=company_default,
        )

    if isinstance(selected, dict):
        policy = policy_for(
            str(selected.get("policy_id") or "custom"),
            version=selected.get("version"),
            requirements=dict(selected.get("requirements") or {}),
        )
    else:
        policy = policy_for(str(selected))

    documents = await _maybe_call(storage, "list_documents", project_id, default=[])
    artifacts = await _artifact_rows(storage, project_id)
    approvals = await _maybe_call(storage, "list_approval_gates", project_id=project_id, default=[])
    worker_runs = await _maybe_call(storage, "list_worker_runs", project_id=project_id, limit=1000, default=[])
    repository = await _maybe_call(storage, "get_project_repository_record", project_id, default=None)
    history = await _maybe_call(storage, "get_project_history", project_id, default=[])
    evidence = evaluate_project_evidence(
        project_id=str(project_id),
        policy=policy,
        project=project,
        documents=documents or [],
        artifacts=artifacts,
        flow_instance=flow_instance,
        approvals=approvals or [],
        worker_runs=worker_runs or [],
        repository=repository,
        audit_events=history or [],
    )
    return project, evidence, {
        "documents": documents or [],
        "artifacts": artifacts,
        "flow_instance": flow_instance,
        "approvals": approvals or [],
        "worker_runs": worker_runs or [],
        "repository": repository,
        "audit_events": history or [],
    }, policy


async def _package(project_id: UUID, storage: Any) -> dict[str, Any]:
    _project, completeness, sources, policy = await _collect(project_id, storage)
    usage = await _maybe_call(storage, "get_project_usage", project_id, default=None)
    package = build_evidence_package(
        completeness=completeness,
        policy=policy,
        usage=usage,
        generated_at=datetime.now(tz=UTC).isoformat(),
        **sources,
    ).model_dump(mode="json")
    snapshot = await _maybe_call(
        storage,
        "get_project_evidence_package",
        project_id,
        policy_id=completeness.policy_id,
        default=None,
    )
    package["snapshot"] = _serialize(snapshot) if isinstance(snapshot, dict) else None
    return package


@router.get("/evidence-policies")
async def list_evidence_policies() -> dict[str, Any]:
    return evidence_policy_catalog()


@router.get("/projects/{project_id}/evidence/package")
async def get_project_evidence_package(project_id: UUID, request: Request) -> dict[str, Any]:
    return await _package(project_id, _storage(request))


@router.post("/projects/{project_id}/evidence/package")
async def persist_project_evidence_package(project_id: UUID, request: Request) -> dict[str, Any]:
    _main_module()._require_operator_identity(request)
    storage = _storage(request)
    package = await _package(project_id, storage)
    writer = getattr(storage, "create_project_evidence_package", None)
    if not inspect.iscoroutinefunction(writer):
        raise HTTPException(501, "Storage does not support evidence package snapshots")
    snapshot = await writer(
        project_id=project_id,
        policy_id=package["policy_id"],
        policy_version=package["policy_version"],
        status=package["status"],
        checks={
            "schema_version": package["schema_version"],
            "checks": package["checks"],
            "categories": package["categories"],
            "items": package["items"],
            "notices": package["notices"],
        },
        evidence_refs=package["evidence_refs"],
        completeness_score=package["completeness_score"],
    )
    return {"package": package, "snapshot": _serialize(snapshot), "stored": True}


@router.put("/projects/{project_id}/evidence-policy")
async def set_project_evidence_policy(
    project_id: UUID,
    request: Request,
    body: ProjectEvidencePolicyRequest,
) -> dict[str, Any]:
    try:
        body.validate_scope()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _main_module()._require_operator_identity(request)
    storage = _storage(request)
    project = await _maybe_call(storage, "get_project", project_id, default=None)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    updater = getattr(storage, "update_project_config", None)
    if not inspect.iscoroutinefunction(updater):
        raise HTTPException(501, "Storage does not support project policy persistence")
    config = dict(project.get("config") or {})
    selection = {
        "policy_id": body.policy_id,
        "version": body.policy_version,
        "requirements": body.requirements,
    }
    if body.scope == "milestone":
        milestone_policies = dict(config.get("evidence_policy_milestones") or {})
        milestone_policies[body.milestone or ""] = selection
        config["evidence_policy_milestones"] = milestone_policies
    else:
        config["evidence_policy"] = selection
    updated = await updater(project_id, config=config)
    if updated is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return {
        "project": _serialize(updated),
        "evidence_policy": selection,
        "scope": body.scope,
        "milestone": body.milestone,
    }


@router.put("/companies/{company_id}/evidence-policy")
async def set_company_evidence_policy(
    company_id: UUID,
    request: Request,
    body: ProjectEvidencePolicyRequest,
) -> dict[str, Any]:
    """Persist the company default policy in the active manifest."""

    try:
        body.validate_scope()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.scope != "project":
        raise HTTPException(422, "company evidence policy must use project scope")
    main = _main_module()
    main._require_operator_identity(request)
    storage = _storage(request)
    company = await _maybe_call(storage, "get_company", company_id, default=None)
    if company is None:
        raise HTTPException(404, "company not found")
    manifest_reader = getattr(storage, "get_company_manifest", None)
    manifest_writer = getattr(storage, "apply_company_manifest", None)
    if not inspect.iscoroutinefunction(manifest_reader) or not inspect.iscoroutinefunction(manifest_writer):
        raise HTTPException(501, "Storage does not support company policy persistence")
    current = await manifest_reader(company_id)
    if not isinstance(current, dict) or not isinstance(current.get("manifest_json"), dict):
        raise HTTPException(409, "company has no active manifest to update")
    raw_manifest = dict(current["manifest_json"])
    evidence_policy = dict(raw_manifest.get("evidence_policy") or {})
    evidence_policy["default_policy"] = {
        "policy_id": body.policy_id,
        "version": body.policy_version,
        "requirements": body.requirements,
    }
    raw_manifest["evidence_policy"] = evidence_policy
    try:
        manifest, digest, canonical = main.compile_company_manifest(raw_manifest)
        result = await manifest_writer(
            company_id=company_id,
            manifest=manifest,
            digest=digest,
            canonical=canonical,
            source="api:company-evidence-policy",
            actor=main._authenticated_principal(request),
        )
    except main.CompanyManifestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"company": _serialize(result), "evidence_policy": evidence_policy["default_policy"]}


__all__ = ["ProjectEvidencePolicyRequest", "router"]
