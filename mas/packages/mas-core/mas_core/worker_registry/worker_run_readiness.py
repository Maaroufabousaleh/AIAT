"""Evaluate prerequisites for one bounded model-backed Worker Run.

The readiness evaluator is deliberately read-model only.  It does not select
or activate a worker, create a project, reserve a budget, provision identity,
or dispatch a run.  It turns the control-plane records that a live preflight
can read into explicit, secret-safe blockers so a later dispatch can be
operator-selected and reviewed.

Licence and resource-restriction fields are not inputs to this evaluator's
decision.  They remain provenance metadata and operator notices only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

WORKER_RUN_READINESS_SCHEMA = "aiat.worker-run-readiness.v1"

# These are the project states in which a governed worker task can be
# selected.  Terminal, security-blocked, and failed projects must be resumed
# through their own operator workflow before a new live task is considered.
DISPATCHABLE_PROJECT_STATES = frozenset(
    {
        "INIT",
        "FEASIBILITY_CHECK",
        "FEASIBILITY_REPORT",
        "PDR_CREATION",
        "PDR_REVIEW",
        "CDR_CREATION",
        "CDR_REVIEW",
        "HUMAN_APPROVAL",
        "RR_CREATION",
        "SPRINT_PLANNING",
        "INFRA_PROVISIONING",
        "IN_PROGRESS",
        "RETROSPECTIVE",
        "KPI_PERSISTENCE",
    }
)
TERMINAL_PROJECT_STATES = frozenset({"COMPLETED", "ARCHIVED", "FAILED"})
VALID_SANDBOX_PROFILES = frozenset({"standard", "restricted", "gvisor", "firecracker"})
HARDENED_SANDBOX_PROFILES = frozenset({"gvisor", "firecracker"})
REQUIRED_BUDGETS = ("max_concurrent_runs", "max_cost_usd")
REQUIRED_WORKER_POINTERS = (
    "active_shell_version_id",
    "active_adapter_id",
    "active_skill_bundle_id",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _blocker(code: str, reason: str) -> dict[str, str]:
    return {"code": code, "reason": reason}


def _rows_by_key(rows: Iterable[Any], key: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value = _text(row.get(key))
        if value:
            result[value] = row
    return result


def _profile_status(
    profiles: Iterable[Any], profile_id: str,
) -> tuple[bool, int, Mapping[str, Any] | None]:
    for profile in profiles:
        if not isinstance(profile, Mapping) or _text(profile.get("profile_id")) != profile_id:
            continue
        versions = profile.get("versions")
        approved_versions = [
            version
            for version in versions if isinstance(version, Mapping) and _text(version.get("status")).lower() == "approved"
        ] if isinstance(versions, list) else []
        approved = _text(profile.get("status")).lower() == "approved" and bool(approved_versions)
        return approved, len(approved_versions), profile
    return False, 0, None


def evaluate_worker_run_readiness(
    *,
    worker: Mapping[str, Any] | None,
    project: Mapping[str, Any] | None,
    company: Mapping[str, Any] | None,
    assignments: Iterable[Any] = (),
    budgets: Iterable[Any] = (),
    model_profiles: Iterable[Any] = (),
    worker_id: str,
    project_id: str,
    requested_model_profile_id: str | None = None,
    required_budget_usd: float | Decimal = Decimal("0.10"),
    require_model_backed: bool = True,
    require_sandbox: bool = False,
    health: Mapping[str, Any] | None = None,
    fetch_errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a bounded readiness report for one explicitly selected run.

    ``worker_id`` and ``project_id`` are caller-selected identifiers.  The
    evaluator never chooses a row when either selected row is missing.  A
    report is ``pass`` only when all required control-plane prerequisites are
    present; otherwise it is ``blocked`` and includes stable blocker codes.
    """

    selected_worker_id = _text(worker_id)
    selected_project_id = _text(project_id)
    requested_profile = _text(requested_model_profile_id)
    blockers: list[dict[str, str]] = []
    fetch_status = {str(key): "blocked" for key in (fetch_errors or {})}

    if fetch_errors:
        for key, reason in sorted(fetch_errors.items()):
            blockers.append(_blocker(f"read_{key}_unavailable", reason))

    worker_status = _text(worker.get("status")) if worker else "missing"
    worker_model_mode = _text(worker.get("model_mode")) if worker else ""
    worker_profile_id = _text(worker.get("model_profile_id")) if worker else ""
    selected_profile_id = requested_profile or worker_profile_id
    pointer_status = {
        pointer: bool(worker and _text(worker.get(pointer)))
        for pointer in REQUIRED_WORKER_POINTERS
    }

    if worker is None:
        blockers.append(_blocker("worker_not_found", "the selected worker was not returned by the control plane"))
    else:
        fetch_status.setdefault("worker", "observed")
        if _text(worker.get("id")) and _text(worker.get("id")) != selected_worker_id:
            blockers.append(_blocker("worker_selection_mismatch", "the returned worker ID differs from the selected worker ID"))
        if worker_status not in {"ACTIVE", "DRAINING"}:
            blockers.append(_blocker("worker_not_active", "the selected worker must be ACTIVE or DRAINING"))
        if not _text(worker.get("version_pin")):
            blockers.append(_blocker("worker_version_pin_missing", "the worker has no immutable source/runtime version pin"))
        if _text(worker.get("evaluation_status")).lower() != "approved":
            blockers.append(_blocker("worker_evaluation_not_approved", "the worker evaluation status is not approved"))
        for pointer, present in pointer_status.items():
            if not present:
                blockers.append(_blocker(f"{pointer}_missing", f"the worker has no active immutable {pointer}"))
        if require_model_backed and worker_model_mode.lower() == "none":
            blockers.append(_blocker("worker_not_model_backed", "the selected worker is not model-backed"))
        if not require_model_backed and worker_model_mode.lower() == "none" and (requested_profile or worker_profile_id):
            blockers.append(_blocker("non_model_worker_has_profile", "a non-model worker cannot select a model profile"))

        sandbox_profile = _text(worker.get("sandbox_profile")).lower()
        if sandbox_profile not in VALID_SANDBOX_PROFILES:
            blockers.append(_blocker("sandbox_profile_invalid", "the worker has no recognized sandbox profile"))
        elif require_sandbox and sandbox_profile not in HARDENED_SANDBOX_PROFILES:
            blockers.append(_blocker("sandbox_profile_not_hardened", "the selected run requires gVisor or Firecracker sandboxing"))

        if health is not None:
            health_status = _text(health.get("health_status")).lower()
            if health_status in {"blocked", "unhealthy", "error"}:
                blockers.append(_blocker("worker_health_not_ready", "the worker health read model reports a blocked or unhealthy state"))

    project_state = _text(project.get("state")) if project else "missing"
    project_company_id = _text(project.get("company_id")) if project else ""
    if project is None:
        blockers.append(_blocker("project_not_found", "the selected project was not returned by the control plane"))
    else:
        fetch_status.setdefault("project", "observed")
        if _text(project.get("id")) and _text(project.get("id")) != selected_project_id:
            blockers.append(_blocker("project_selection_mismatch", "the returned project ID differs from the selected project ID"))
        if project_state not in DISPATCHABLE_PROJECT_STATES:
            if project_state in TERMINAL_PROJECT_STATES:
                blockers.append(_blocker("project_terminal", "the selected project is terminal and requires its recovery/archive workflow"))
            else:
                blockers.append(_blocker("project_not_dispatchable", "the selected project is not in a dispatchable workflow state"))
        if not project_company_id:
            blockers.append(_blocker("project_company_missing", "the selected project has no owning company"))

    company_status = _text(company.get("status")) if company else "missing"
    company_id = project_company_id
    if company is None:
        blockers.append(_blocker("company_not_found", "the selected project's owning company was not returned"))
    else:
        fetch_status.setdefault("company", "observed")
        if _text(company.get("id")) and _text(company.get("id")) != company_id:
            blockers.append(_blocker("company_selection_mismatch", "the returned company ID differs from the project company"))
        if company_status.upper() != "ACTIVE":
            blockers.append(_blocker("company_not_active", "the project company must be ACTIVE"))

    assignment_rows = _rows_by_key(assignments, "worker_id")
    assignment = assignment_rows.get(selected_worker_id)
    assignment_status = _text(assignment.get("status")) if assignment else "missing"
    if not assignments and not (fetch_errors or {}).get("assignments"):
        fetch_status.setdefault("assignments", "observed")
    if assignment is None:
        blockers.append(_blocker("worker_assignment_missing", "the selected worker has no assignment for the project company"))
    elif assignment_status.upper() != "ACTIVE":
        blockers.append(_blocker("worker_assignment_not_active", "the selected worker assignment is not ACTIVE"))
    else:
        if bool(assignment.get("approval_required")):
            blockers.append(_blocker("worker_assignment_approval_required", "the company assignment still requires approval"))
        assignment_profile = _text(assignment.get("model_profile_id"))
        if assignment_profile and selected_profile_id and assignment_profile != selected_profile_id:
            blockers.append(_blocker("assignment_model_profile_mismatch", "the active assignment model profile differs from the selected worker profile"))

    profile_approved = False
    approved_version_count = 0
    profile_row: Mapping[str, Any] | None = None
    if require_model_backed:
        if not selected_profile_id:
            blockers.append(_blocker("model_profile_missing", "a model-backed worker requires an explicitly approved model profile"))
        elif worker_profile_id and requested_profile and requested_profile != worker_profile_id:
            blockers.append(_blocker("model_profile_override_not_authorized", "a profile different from the worker binding requires a governed override"))
        else:
            profile_approved, approved_version_count, profile_row = _profile_status(model_profiles, selected_profile_id)
            if not profile_approved:
                blockers.append(_blocker("model_profile_not_approved", "the selected model profile has no effective approved version"))

    required_budget = _decimal(required_budget_usd)
    if required_budget is None or required_budget < 0:
        blockers.append(_blocker("required_budget_invalid", "the requested bounded budget is invalid"))
        required_budget = Decimal("0")
    budget_rows = _rows_by_key(budgets, "budget_key")
    budget_status: dict[str, str] = {}
    for budget_key in REQUIRED_BUDGETS:
        row = budget_rows.get(budget_key)
        if row is None:
            budget_status[budget_key] = "missing"
            blockers.append(_blocker(f"budget_{budget_key}_missing", f"company budget {budget_key} is not configured"))
            continue
        if row.get("configured") is False:
            budget_status[budget_key] = "not_configured"
            blockers.append(_blocker(f"budget_{budget_key}_not_configured", f"company budget {budget_key} is not configured"))
            continue
        available = _decimal(row.get("available"))
        required = Decimal("1") if budget_key == "max_concurrent_runs" else required_budget
        if available is None:
            budget_status[budget_key] = "invalid"
            blockers.append(_blocker(f"budget_{budget_key}_invalid", f"company budget {budget_key} has no bounded available balance"))
        elif available < required:
            budget_status[budget_key] = "exhausted"
            blockers.append(_blocker(f"budget_{budget_key}_exhausted", f"company budget {budget_key} cannot reserve the bounded run allowance"))
        else:
            budget_status[budget_key] = "available"
    fetch_status.setdefault("budgets", "observed")
    fetch_status.setdefault("model_profiles", "observed")

    sandbox_profile = _text(worker.get("sandbox_profile")).lower() if worker else "missing"
    health_status = _text(health.get("health_status")).lower() if health else "not_checked"
    report = {
        "schema_version": WORKER_RUN_READINESS_SCHEMA,
        "status": "pass" if not blockers else "blocked",
        "licence_metadata_is_gate": False,
        "selected": {
            "worker_id": selected_worker_id,
            "project_id": selected_project_id,
            "company_id": company_id or None,
            "model_profile_id": selected_profile_id or None,
        },
        "checks": {
            "worker": {
                "status": worker_status,
                "model_mode": worker_model_mode or "unknown",
                "evaluation_status": _text(worker.get("evaluation_status")) if worker else "missing",
                "immutable_pointers": pointer_status,
            },
            "project": {"state": project_state},
            "company": {"status": company_status},
            "assignment": {"status": assignment_status},
            "model_profile": {
                "required": bool(require_model_backed),
                "profile_id": selected_profile_id or None,
                "status": "approved" if profile_approved else ("not_checked" if not selected_profile_id else "blocked"),
                "approved_version_count": approved_version_count,
                "profile_present": profile_row is not None,
            },
            "budgets": budget_status,
            "sandbox": {
                "profile": sandbox_profile,
                "runtime_status": "not_checked",
                "hardened_required": bool(require_sandbox),
            },
            "health": {"status": health_status},
            "identity": {"status": "not_checked"},
            "provider": {"status": "not_checked"},
            "retention": {"status": "not_checked"},
        },
        "fetch_status": fetch_status,
        "blockers": blockers,
        "scope": "read-only selected model-backed worker-run readiness; no activation, identity provisioning, budget reservation, dispatch, or payload access",
    }
    return report


__all__ = [
    "DISPATCHABLE_PROJECT_STATES",
    "HARDENED_SANDBOX_PROFILES",
    "REQUIRED_BUDGETS",
    "REQUIRED_WORKER_POINTERS",
    "TERMINAL_PROJECT_STATES",
    "VALID_SANDBOX_PROFILES",
    "WORKER_RUN_READINESS_SCHEMA",
    "evaluate_worker_run_readiness",
]
