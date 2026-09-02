"""Verify the API-owned executive reconciliation evidence surface.

Static mode is declaration-only. ``--live`` fetches the read-only
``/executive/reconciliation`` endpoint and reports bounded coverage/finding
counts without echoing raw projects, usage, reservations, IDs, or credentials.
Use ``--require-clean`` when a release gate must reject any reconciliation
finding. Missing configuration, authentication, unavailable API, or malformed
responses are blocked with exit code 2. Licence/restriction metadata is not an
input to this check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

EXECUTIVE_SCHEMA = "aiat.executive-reconciliation.v1"
CHECK_SCHEMA = "aiat.executive-reconciliation-check.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="fetch the orchestrator reconciliation endpoint")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL (or AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", os.getenv("MAS_API_KEY", ""))),
        help="optional operator bearer key (AIAT_OPERATOR_API_KEY/AIAT_API_KEY/MAS_API_KEY); never included in the report",
    )
    parser.add_argument("--company-id", help="optional company scope UUID")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="fail when the canonical report contains any finding",
    )
    return parser


def _static_report() -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "static",
        "status": "pass",
        "reason": "live mode was not requested",
        "coverage": None,
        "finding_count": None,
        "canonical_status": "not_checked",
        "scope": "declaration only; live executive reconciliation not checked",
    }


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "coverage": None,
        "finding_count": None,
        "canonical_status": "not_checked",
        "scope": "API-owned executive reconciliation summary only",
    }


def inspect_live(
    *,
    url: str,
    api_key: str,
    company_id: str | None,
    timeout: float,
    require_clean: bool = False,
) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    endpoint = f"{url.rstrip('/')}/executive/reconciliation"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key.strip() else {}
    params = {"company_id": company_id} if company_id else None
    try:
        response = httpx.get(endpoint, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked(f"executive reconciliation unavailable: {type(exc).__name__}", url_configured=True)
    if not isinstance(payload, dict) or payload.get("schema_version") != EXECUTIVE_SCHEMA:
        return _blocked("orchestrator returned an invalid executive reconciliation", url_configured=True)
    coverage = payload.get("coverage")
    findings = payload.get("findings")
    if not isinstance(coverage, dict) or not isinstance(findings, list):
        return _blocked("executive reconciliation omitted coverage or findings", url_configured=True)
    safe_coverage = {
        key: coverage.get(key)
        for key in (
            "project_count",
            "project_usage_count",
            "worker_run_count",
            "budget_count",
            "budget_reservation_count",
        )
        if isinstance(coverage.get(key), int)
    }
    finding_count = len(findings)
    canonical_status = str(payload.get("status", "unknown"))
    status = "pass_with_findings" if finding_count else "pass"
    reason = "canonical reconciliation has no findings" if not finding_count else "canonical reconciliation has findings"
    if require_clean and finding_count:
        status = "fail"
        reason = "canonical reconciliation findings violate --require-clean"
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": status,
        "reason": reason,
        "url_configured": True,
        "company_scoped": bool(company_id),
        "coverage": safe_coverage,
        "finding_count": finding_count,
        "canonical_status": canonical_status,
        "scope": "API-owned executive reconciliation summary only",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = (
        inspect_live(
            url=args.url,
            api_key=args.api_key,
            company_id=args.company_id,
            timeout=args.timeout,
            require_clean=args.require_clean,
        )
        if args.live
        else _static_report()
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"executive reconciliation: {report['status']} — {report.get('reason', 'no reason')}")
    return 2 if report["status"] == "blocked" else (1 if report["status"] == "fail" else 0)


if __name__ == "__main__":
    sys.exit(main())
