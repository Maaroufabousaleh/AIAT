"""Export/reconcile the runtime model registry and governed profile layer.

This is a deterministic declaration check. It does not approve a model,
create a profile, or turn a registry entry into an executable route. Persisted
profiles remain the authority for governed dispatch; registry-only models are
reported as ``profile_pending``. Licence/restriction metadata is outside this
operational check and cannot fail it.

With ``--live`` the same report is fetched from a running orchestrator API.
Missing configuration, authentication, an unavailable API, or a malformed
response is reported as ``blocked`` and exits with status 2. Pass
``--require-approved`` when the live check must also block on an empty or
pending persisted profile layer; the default live mode is a readiness report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from mas_core.llm_gateway import MODEL_REGISTRY, build_model_profile_catalogue


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a summary")
    parser.add_argument(
        "--live",
        action="store_true",
        help="fetch the catalogue from a running orchestrator API instead of local declarations",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("AIAT_ORCHESTRATOR_URL", os.environ.get("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL (or AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("AIAT_API_KEY", os.environ.get("MAS_API_KEY", "")),
        help="optional bearer key (or AIAT_API_KEY/MAS_API_KEY); never included in the report",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="live request timeout in seconds",
    )
    parser.add_argument(
        "--require-approved",
        action="store_true",
        help="return blocked when the report has no approved profile coverage",
    )
    return parser


def reconcile() -> dict[str, Any]:
    """Return the deterministic checked-in registry catalogue report."""
    report = build_model_profile_catalogue((), MODEL_REGISTRY)
    report["status"] = "pass" if not report["findings"] else "pass_with_profile_findings"
    report["profile_coverage"] = (
        "complete"
        if (
            report["profile_version_count"] > 0
            and report["covered_profile_version_count"] == report["profile_version_count"]
            and report["profile_pending_model_count"] == 0
        )
        else "pending_persisted_profile_bindings"
    )
    return report


def _valid_live_catalogue_shape(payload: dict[str, Any]) -> bool:
    """Require the bounded fields used by the live status calculation."""
    required_counts = (
        "registry_model_count",
        "profile_count",
        "profile_version_count",
        "covered_profile_version_count",
        "profile_pending_model_count",
    )
    if not isinstance(payload.get("findings"), list) or not isinstance(payload.get("entries"), list):
        return False
    for field in required_counts:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    return True


def _live_report(*, url: str, api_key: str, timeout: float) -> dict[str, Any]:
    """Fetch the API-owned catalogue without leaking credentials."""
    if not url.strip():
        return {
            "schema_version": "aiat.model-profile-catalogue.v1",
            "mode": "live",
            "status": "blocked",
            "reason": "missing live configuration: orchestrator URL",
        }
    endpoint = f"{url.rstrip('/')}/model-profiles/catalogue"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key.strip() else {}
    try:
        response = httpx.get(endpoint, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "schema_version": "aiat.model-profile-catalogue.v1",
            "mode": "live",
            "status": "blocked",
            "reason": f"orchestrator catalogue unavailable: {type(exc).__name__}",
        }
    if not isinstance(payload, dict) or payload.get("schema_version") != "aiat.model-profile-catalogue.v1":
        return {
            "schema_version": "aiat.model-profile-catalogue.v1",
            "mode": "live",
            "status": "blocked",
            "reason": "orchestrator returned an invalid model-profile catalogue",
        }
    if not _valid_live_catalogue_shape(payload):
        return {
            "schema_version": "aiat.model-profile-catalogue.v1",
            "mode": "live",
            "status": "blocked",
            "reason": "orchestrator returned malformed model-profile catalogue fields",
        }
    normalized = {**payload, "mode": "live"}
    findings = normalized.get("findings")
    normalized["status"] = (
        "pass_with_profile_findings"
        if isinstance(findings, list) and findings
        else "pass_with_profile_pending"
        if normalized["profile_pending_model_count"] > 0
        else "pass"
    )
    normalized["profile_coverage"] = (
        "complete"
        if normalized["profile_version_count"] > 0
        and normalized["covered_profile_version_count"] == normalized["profile_version_count"]
        and normalized["profile_pending_model_count"] == 0
        else "pending_persisted_profile_bindings"
    )
    return normalized


def _apply_approval_requirement(report: dict[str, Any], *, require_approved: bool) -> dict[str, Any]:
    if not require_approved or report.get("status") == "blocked":
        return report
    approved = sum(
        1
        for entry in report.get("entries", [])
        if isinstance(entry, dict) and entry.get("profile_state") == "approved_profile_present"
    )
    if approved <= 0:
        return {
            **report,
            "status": "blocked",
            "reason": "no approved persisted model-profile coverage",
        }
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = (
        _live_report(url=args.url, api_key=args.api_key, timeout=args.timeout)
        if args.live
        else reconcile()
    )
    report = _apply_approval_requirement(report, require_approved=args.require_approved)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        if report.get("status") == "blocked":
            print(f"model profile catalogue: blocked ({report.get('reason', 'unknown reason')})")
            return 2
        print(
            "model profile catalogue: "
            f"{report['registry_model_count']} registry models, "
            f"{report['profile_version_count']} persisted profile versions, "
            f"coverage={report['profile_coverage']}"
        )
        if report["findings"]:
            print(f"profile findings: {len(report['findings'])}")
    return 2 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
