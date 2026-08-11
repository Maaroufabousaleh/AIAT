"""Deterministic executive reconciliation over durable control-plane reads."""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


EXECUTIVE_RECONCILIATION_SCHEMA = "aiat.executive-reconciliation.v1"
EXECUTIVE_VIEWS_SCHEMA = "aiat.executive-views.v1"
_TERMINAL_PROJECT_STATES = frozenset({"COMPLETED", "ARCHIVED", "FAILED"})
_TERMINAL_RUN_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"})


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _counts(values: Iterable[Any]) -> dict[str, int]:
    return {str(key): count for key, count in sorted(Counter(str(value) for value in values).items())}


def _usage_totals(usage_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(usage_rows)
    totals = {
        "projects_with_usage": 0,
        "llm_calls": 0,
        "tool_calls": 0,
        "failed_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
    }
    for row in rows:
        if row.get("available", True):
            totals["projects_with_usage"] += 1
        totals["llm_calls"] += _integer(row.get("llm_calls"))
        totals["tool_calls"] += _integer(row.get("tool_calls"))
        totals["failed_calls"] += _integer(row.get("failed_calls"))
        totals["prompt_tokens"] += _integer(row.get("prompt_tokens"))
        totals["completion_tokens"] += _integer(row.get("completion_tokens"))
        totals["total_tokens"] += _integer(
            row.get("total_tokens", _integer(row.get("prompt_tokens")) + _integer(row.get("completion_tokens")))
        )
        totals["total_cost_usd"] += _number(row.get("total_cost_usd"))
    totals["total_cost_usd"] = round(totals["total_cost_usd"], 8)
    return totals


def build_executive_views(report: Mapping[str, Any]) -> dict[str, Any]:
    """Project one reconciled report into bounded CFO/CTO/CEO summaries.

    These are read-only views over the same report, not separate policy or
    accounting authorities. Keeping the projections pure makes the role
    surfaces deterministic and prevents dashboard-specific calculations from
    drifting away from durable control-plane evidence.
    """
    projects = dict(report.get("projects") or {})
    delivery = dict(report.get("delivery") or {})
    budgets = dict(report.get("budgets") or {})
    reservations = dict(budgets.get("reservation_reconciliation") or {})
    models = dict(report.get("models") or {})
    coverage = dict(report.get("coverage") or {})
    findings = [dict(item) for item in report.get("findings") or []]
    finding_codes = sorted({str(item.get("code", "")) for item in findings if item.get("code")})
    budget_attention = bool(budgets.get("overages")) or int(reservations.get("anomaly_count") or 0) > 0
    delivery_attention = int(delivery.get("failed_run_count") or 0) > 0
    model_pending = int(models.get("profile_pending_model_count") or 0)

    return {
        "schema_version": EXECUTIVE_VIEWS_SCHEMA,
        "cfo": {
            "status": "attention" if budget_attention else "clear",
            "spend_usd": dict(projects.get("usage") or {}).get("total_cost_usd", 0.0),
            "budget_limit_usd": budgets.get("limit_usd", 0.0),
            "budget_used_usd": budgets.get("used_usd", 0.0),
            "budget_available_usd": budgets.get("available_usd", 0.0),
            "reservation_active_usd": reservations.get("active_amount_usd", 0.0),
            "overage_count": len(budgets.get("overages") or []),
            "reservation_anomaly_count": int(reservations.get("anomaly_count") or 0),
        },
        "cto": {
            "status": "attention" if delivery_attention or model_pending else "clear",
            "active_projects": int(projects.get("active_count") or 0),
            "active_worker_runs": int(delivery.get("active_run_count") or 0),
            "terminal_worker_runs": int(delivery.get("terminal_run_count") or 0),
            "successful_worker_runs": int(delivery.get("successful_run_count") or 0),
            "failed_worker_runs": int(delivery.get("failed_run_count") or 0),
            "success_rate": delivery.get("success_rate"),
            "registered_models": int(models.get("registry_model_count") or 0),
            "profile_coverage": {
                "approved_versions": int(models.get("covered_profile_version_count") or 0),
                "total_versions": int(models.get("profile_version_count") or 0),
                "pending_models": model_pending,
            },
        },
        "ceo": {
            "status": "attention" if str(report.get("status")) != "reconciled" else "clear",
            "active_projects": int(projects.get("active_count") or 0),
            "total_projects": int(coverage.get("project_count") or 0),
            "active_worker_runs": int(delivery.get("active_run_count") or 0),
            "budget_available_usd": budgets.get("available_usd", 0.0),
            "finding_count": len(findings),
            "finding_codes": finding_codes,
        },
    }


def build_executive_reconciliation(
    *,
    projects: Iterable[Mapping[str, Any]],
    project_usage: Mapping[str, Mapping[str, Any]],
    worker_runs: Iterable[Mapping[str, Any]],
    budget_states: Iterable[Mapping[str, Any]],
    budget_reservations: Iterable[Mapping[str, Any]] = (),
    model_catalogue: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable executive report from already-authorized durable reads.

    This function performs no writes and treats model/profile and budget data
    as separate evidence sources. Missing optional reads remain visible in
    ``coverage``/``findings``; licence or restriction metadata is not an
    operational input.
    """
    project_rows = sorted((dict(row) for row in projects), key=lambda row: str(row.get("id", "")))
    usage_by_project = {str(key): dict(value) for key, value in project_usage.items()}
    usage_rows: list[dict[str, Any]] = []
    usage_unavailable: list[str] = []
    for project in project_rows:
        project_id = str(project.get("id", ""))
        usage = dict(usage_by_project.get(project_id, {"available": False}))
        usage["project_id"] = project_id
        usage_rows.append(usage)
        if usage.get("available", True) is False:
            usage_unavailable.append(project_id)

    run_rows = sorted((dict(row) for row in worker_runs), key=lambda row: str(row.get("id", "")))
    run_states = [str(row.get("state", "UNKNOWN")) for row in run_rows]
    run_state_by_id = {str(row.get("id")): str(row.get("state", "UNKNOWN")) for row in run_rows}
    terminal_runs = [state for state in run_states if state in _TERMINAL_RUN_STATES]
    successful_runs = sum(state == "SUCCEEDED" for state in terminal_runs)
    failed_runs = sum(state in {"FAILED", "TIMED_OUT"} for state in terminal_runs)

    budget_rows = sorted(
        (dict(row) for row in budget_states if row.get("configured", True)),
        key=lambda row: (str(row.get("company_id", "")), str(row.get("budget_key", ""))),
    )
    budget_totals = {
        "configured_count": len(budget_rows),
        "limit_usd": round(sum(_number(row.get("limit")) for row in budget_rows), 8),
        "used_usd": round(sum(_number(row.get("used")) for row in budget_rows), 8),
        "available_usd": round(sum(_number(row.get("available")) for row in budget_rows), 8),
    }
    overages = [
        {
            "company_id": str(row.get("company_id", "")),
            "budget_key": str(row.get("budget_key", "")),
            "limit": _number(row.get("limit")),
            "used": _number(row.get("used")),
        }
        for row in budget_rows
        if _number(row.get("used")) > _number(row.get("limit"))
    ]

    reservation_rows = [dict(row) for row in budget_reservations]
    reservation_states = _counts(row.get("state", "UNKNOWN") for row in reservation_rows)
    reservation_amounts: dict[tuple[str, str], float] = {}
    idempotency_groups: dict[str, list[str]] = {}
    budget_anomalies: list[dict[str, Any]] = []
    for row in reservation_rows:
        state = str(row.get("state", "UNKNOWN"))
        reservation_id = str(row.get("id", ""))
        idempotency_key = str(row.get("idempotency_key", ""))
        if idempotency_key:
            idempotency_groups.setdefault(idempotency_key, []).append(reservation_id)
        if state not in {"RESERVED", "COMMITTED", "RELEASED"}:
            budget_anomalies.append(
                {"code": "UNKNOWN_RESERVATION_STATE", "reservation_id": reservation_id, "state": state}
            )
        amount = _number(row.get("amount"))
        if amount < 0:
            budget_anomalies.append(
                {"code": "NEGATIVE_RESERVATION_AMOUNT", "reservation_id": reservation_id, "amount": amount}
            )
        if state in {"RESERVED", "COMMITTED"}:
            key = (str(row.get("company_id", "")), str(row.get("budget_key", "")))
            reservation_amounts[key] = reservation_amounts.get(key, 0.0) + amount
        run_id = row.get("run_id")
        if state == "RESERVED" and run_id is not None and run_state_by_id.get(str(run_id)) in _TERMINAL_RUN_STATES:
            budget_anomalies.append(
                {
                    "code": "RESERVED_TERMINAL_RUN",
                    "reservation_id": reservation_id,
                    "run_id": str(run_id),
                    "run_state": run_state_by_id[str(run_id)],
                }
            )
    for idempotency_key, reservation_ids in sorted(idempotency_groups.items()):
        if len(reservation_ids) > 1:
            budget_anomalies.append(
                {
                    "code": "DUPLICATE_RESERVATION_IDEMPOTENCY_KEY",
                    "idempotency_key": idempotency_key,
                    "reservation_ids": sorted(reservation_ids),
                }
            )
    reservation_sum_mismatches: list[dict[str, Any]] = []
    for row in budget_rows:
        key = (str(row.get("company_id", "")), str(row.get("budget_key", "")))
        observed = round(reservation_amounts.get(key, 0.0), 8)
        reported = round(_number(row.get("used")), 8)
        if abs(observed - reported) > 1e-8:
            mismatch = {
                "code": "BUDGET_RESERVATION_SUM_MISMATCH",
                "company_id": key[0],
                "budget_key": key[1],
                "reservation_sum": observed,
                "budget_used": reported,
            }
            reservation_sum_mismatches.append(mismatch)
            budget_anomalies.append(mismatch)
    budget_anomalies.sort(key=lambda item: (str(item.get("code", "")), str(item.get("reservation_id", "")), str(item.get("budget_key", ""))))
    usage_totals = _usage_totals(usage_rows)
    project_states = _counts(row.get("state", "UNKNOWN") for row in project_rows)
    findings: list[dict[str, Any]] = []
    if usage_unavailable:
        findings.append(
            {
                "code": "PROJECT_USAGE_UNAVAILABLE",
                "severity": "warning",
                "project_ids": sorted(usage_unavailable),
            }
        )
    if overages:
        findings.append(
            {
                "code": "BUDGET_OVERAGE",
                "severity": "warning",
                "budgets": overages,
            }
        )
    findings.extend(budget_anomalies)
    if model_catalogue and _integer(model_catalogue.get("profile_pending_model_count")) > 0:
        findings.append(
            {
                "code": "MODEL_PROFILE_COVERAGE_PENDING",
                "severity": "info",
                "model_count": _integer(model_catalogue.get("profile_pending_model_count")),
            }
        )
    findings.sort(key=lambda item: str(item.get("code", "")))

    report = {
        "schema_version": EXECUTIVE_RECONCILIATION_SCHEMA,
        "status": "reconciled_with_findings" if findings else "reconciled",
        "coverage": {
            "project_count": len(project_rows),
            "project_usage_count": len(usage_rows) - len(usage_unavailable),
            "worker_run_count": len(run_rows),
            "budget_count": len(budget_rows),
            "budget_reservation_count": sum(reservation_states.values()),
        },
        "projects": {
            "by_state": project_states,
            "active_count": sum(count for state, count in project_states.items() if state not in _TERMINAL_PROJECT_STATES),
            "terminal_count": sum(count for state, count in project_states.items() if state in _TERMINAL_PROJECT_STATES),
            "usage": usage_totals,
        },
        "delivery": {
            "runs_by_state": _counts(run_states),
            "active_run_count": sum(state not in _TERMINAL_RUN_STATES for state in run_states),
            "terminal_run_count": len(terminal_runs),
            "successful_run_count": successful_runs,
            "failed_run_count": failed_runs,
            "success_rate": round(successful_runs / len(terminal_runs), 6) if terminal_runs else None,
        },
        "budgets": {
            **budget_totals,
            "reservation_states": reservation_states,
            "overages": overages,
            "reservation_reconciliation": {
                "active_amount_usd": round(sum(reservation_amounts.values()), 8),
                "idempotency_key_count": len(idempotency_groups),
                "anomaly_count": len(budget_anomalies),
                "anomalies": budget_anomalies,
                "sum_mismatches": reservation_sum_mismatches,
            },
        },
        "models": dict(model_catalogue or {}),
        "findings": findings,
        "sources": {
            "projects": "durable projects",
            "project_usage": "durable project_usage_events",
            "worker_runs": "durable worker_runs",
            "budgets": "durable company_budgets and budget_reservations",
            "models": "runtime model registry and persisted Model Profiles",
        },
    }
    report["views"] = build_executive_views(report)
    return report
