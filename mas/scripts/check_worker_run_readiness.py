"""Run a read-only preflight for one selected model-backed Worker Run.

Fixture mode exercises the same evaluator with a complete bounded control
plane snapshot.  ``--live`` requires an explicit worker and project UUID and
reads the worker, project, company, assignment, budget, model-profile, and
health surfaces from the orchestrator.  It never selects a row automatically,
activates a worker, provisions identity, reserves a budget, dispatches a run,
or returns task/provider payloads.

Licence and resource-restriction metadata remain informational and are not a
readiness or execution gate.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from mas_core.worker_registry.worker_run_readiness import (
    WORKER_RUN_READINESS_SCHEMA,
    evaluate_worker_run_readiness,
)

CHECK_SCHEMA = "aiat.worker-run-readiness-check.v1"
DEFAULT_BUDGET_USD = Decimal("0.10")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    parser.add_argument("--live", action="store_true", help="read one configured deployment")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", "")),
        help="operator API key; never included in the report",
    )
    parser.add_argument(
        "--worker-id",
        default=os.getenv("AIAT_LIVE_WORKER_ID", ""),
        help="explicit selected worker UUID; live mode never auto-selects",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("AIAT_LIVE_PROJECT_ID", ""),
        help="explicit selected project UUID; live mode never auto-selects",
    )
    parser.add_argument(
        "--model-profile-id",
        default=os.getenv("AIAT_LIVE_MODEL_PROFILE_ID", ""),
        help="optional selected model profile; it must match the worker binding",
    )
    parser.add_argument(
        "--budget-usd",
        type=Decimal,
        default=DEFAULT_BUDGET_USD,
        help="bounded cost allowance to preflight (default: 0.10; no reservation is made)",
    )
    parser.add_argument(
        "--require-sandbox",
        action="store_true",
        help="require a hardened gVisor or Firecracker worker profile",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP read timeout in seconds")
    return parser


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "readiness_schema": WORKER_RUN_READINESS_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "licence_metadata_is_gate": False,
        "reason": reason,
        "url_configured": url_configured,
        "no_mutation": True,
        "scope": "read-only selected model-backed worker-run readiness",
    }


def _uuid(value: str, label: str) -> tuple[str | None, str | None]:
    try:
        return str(UUID(str(value).strip())), None
    except (TypeError, ValueError, AttributeError):
        return None, f"{label} must be a UUID"


def _get_json(
    client: httpx.Client,
    *,
    base: str,
    path: str,
    headers: Mapping[str, str],
) -> tuple[Any | None, str | None]:
    try:
        response = client.get(f"{base}{path}", headers=dict(headers))
    except httpx.HTTPError:
        return None, "transport_error"
    if response.status_code == 404:
        return None, "not_found"
    if response.status_code != 200:
        return None, f"http_{response.status_code}"
    try:
        return response.json(), None
    except ValueError:
        return None, "invalid_json"


def _fixture() -> dict[str, Any]:
    worker_id = "00000000-0000-4000-8000-000000000101"
    project_id = "00000000-0000-4000-8000-000000000102"
    company_id = "00000000-0000-4000-8000-000000000103"
    profile_id = "fixture-model-profile"
    report = evaluate_worker_run_readiness(
        worker={
            "id": worker_id,
            "status": "ACTIVE",
            "model_mode": "aiat_gateway",
            "model_profile_id": profile_id,
            "evaluation_status": "approved",
            "version_pin": "fixture-1.0.0",
            "active_shell_version_id": "shell-1",
            "active_adapter_id": "adapter-1",
            "active_skill_bundle_id": "bundle-1",
            "sandbox_profile": "gvisor",
        },
        project={"id": project_id, "company_id": company_id, "state": "IN_PROGRESS"},
        company={"id": company_id, "status": "ACTIVE"},
        assignments=[
            {
                "worker_id": worker_id,
                "status": "ACTIVE",
                "approval_required": False,
                "model_profile_id": profile_id,
            }
        ],
        budgets=[
            {"budget_key": "max_concurrent_runs", "configured": True, "available": "1"},
            {"budget_key": "max_cost_usd", "configured": True, "available": "0.10"},
        ],
        model_profiles=[
            {
                "profile_id": profile_id,
                "status": "approved",
                "versions": [{"status": "approved", "version": "1"}],
            }
        ],
        worker_id=worker_id,
        project_id=project_id,
        required_budget_usd=DEFAULT_BUDGET_USD,
        require_sandbox=True,
        health={"health_status": "healthy"},
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "readiness_schema": WORKER_RUN_READINESS_SCHEMA,
        "mode": "fixture",
        "status": report["status"],
        "licence_metadata_is_gate": False,
        "no_mutation": True,
        "readiness": report,
        "scope": "deterministic read-only fixture; no database, identity, budget, worker, or provider state changed",
    }


def _live(args: argparse.Namespace) -> dict[str, Any]:
    url = str(args.url or "").strip()
    api_key = str(args.api_key or "").strip()
    if not url:
        return _blocked("missing live configuration: orchestrator URL")
    if not api_key:
        return _blocked("missing live configuration: operator API key", url_configured=True)
    if not math.isfinite(float(args.timeout)) or args.timeout <= 0 or args.timeout > 60:
        return _blocked("timeout must be between 0 and 60 seconds", url_configured=True)
    if args.budget_usd.is_nan() or args.budget_usd.is_infinite() or args.budget_usd < 0 or args.budget_usd > Decimal("1000"):
        return _blocked("budget-usd must be finite and between 0 and 1000", url_configured=True)
    worker_id, worker_error = _uuid(str(args.worker_id or ""), "worker-id")
    project_id, project_error = _uuid(str(args.project_id or ""), "project-id")
    if worker_error or project_error:
        return _blocked(worker_error or project_error or "invalid selection", url_configured=True)
    headers = {"X-API-Key": api_key}
    base = url.rstrip("/")
    fetch_errors: dict[str, str] = {}
    try:
        with httpx.Client(timeout=float(args.timeout)) as client:
            worker_rows, worker_error = _get_json(
                client, base=base, path="/capabilities/workers", headers=headers
            )
            worker = None
            if worker_error:
                fetch_errors["worker"] = f"worker read returned {worker_error}"
            elif not isinstance(worker_rows, list):
                fetch_errors["worker"] = "worker read returned an invalid collection"
            else:
                worker = next(
                    (
                        item
                        for item in worker_rows
                        if isinstance(item, Mapping) and str(item.get("id")) == worker_id
                    ),
                    None,
                )

            project, project_error = _get_json(
                client, base=base, path=f"/projects/{project_id}", headers=headers
            )
            if project_error:
                fetch_errors["project"] = f"project read returned {project_error}"
            if not isinstance(project, Mapping):
                project = None

            company = None
            assignments: Any = []
            budgets: Any = []
            if project is not None and project.get("company_id"):
                company_id = str(project["company_id"])
                company_payload, company_error = _get_json(
                    client, base=base, path=f"/companies/{company_id}", headers=headers
                )
                if company_error:
                    fetch_errors["company"] = f"company read returned {company_error}"
                # ``GET /companies/{id}`` returns the canonical read model
                # envelope; accept only its bounded company projection here.
                company = (
                    company_payload.get("company")
                    if isinstance(company_payload, Mapping)
                    and isinstance(company_payload.get("company"), Mapping)
                    else company_payload
                )
                assignments, assignment_error = _get_json(
                    client,
                    base=base,
                    path=f"/companies/{company_id}/assignments",
                    headers=headers,
                )
                if assignment_error:
                    fetch_errors["assignments"] = f"assignment read returned {assignment_error}"
                budgets, budget_error = _get_json(
                    client,
                    base=base,
                    path=f"/companies/{company_id}/budgets",
                    headers=headers,
                )
                if budget_error:
                    fetch_errors["budgets"] = f"budget read returned {budget_error}"
            else:
                fetch_errors["company"] = "project company ID is missing"
                fetch_errors["assignments"] = "project company ID is missing"
                fetch_errors["budgets"] = "project company ID is missing"

            model_profiles, profile_error = _get_json(
                client, base=base, path="/model-profiles", headers=headers
            )
            if profile_error:
                fetch_errors["model_profiles"] = f"model-profile read returned {profile_error}"

            health, health_error = _get_json(
                client,
                base=base,
                path=f"/capabilities/workers/{worker_id}/health",
                headers=headers,
            )
            if health_error:
                fetch_errors["worker_health"] = f"worker health read returned {health_error}"
                health = None
            elif not isinstance(health, Mapping):
                fetch_errors["worker_health"] = "worker health read returned an invalid payload"
                health = None
    except (httpx.HTTPError, ValueError, TypeError, OverflowError) as exc:
        return _blocked(f"live readiness read unavailable: {type(exc).__name__}", url_configured=True)

    if not isinstance(company, Mapping):
        company = None
    if not isinstance(assignments, list):
        assignments = []
    if not isinstance(budgets, list):
        budgets = []
    if not isinstance(model_profiles, list):
        model_profiles = []

    readiness = evaluate_worker_run_readiness(
        worker=worker if isinstance(worker, Mapping) else None,
        project=project,
        company=company,
        assignments=assignments,
        budgets=budgets,
        model_profiles=model_profiles,
        worker_id=worker_id or "",
        project_id=project_id or "",
        requested_model_profile_id=str(args.model_profile_id or "").strip() or None,
        required_budget_usd=args.budget_usd,
        require_sandbox=bool(args.require_sandbox),
        health=health,
        fetch_errors=fetch_errors,
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "readiness_schema": WORKER_RUN_READINESS_SCHEMA,
        "mode": "live",
        "status": readiness["status"],
        "licence_metadata_is_gate": False,
        "no_mutation": True,
        "readiness": readiness,
        "scope": "read-only selected model-backed worker-run readiness; no activation, identity provisioning, budget reservation, dispatch, or payload access",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _live(args) if args.live else _fixture()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, default=str))
    else:
        print(f"worker run readiness: {report['status']} — {report.get('scope', report.get('reason', ''))}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
