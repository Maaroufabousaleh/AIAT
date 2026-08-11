"""Run a read-only preflight for one selected steward candidate.

Fixture mode exercises the same evaluator with a complete candidate snapshot.
``--live`` requires explicit worker and candidate UUIDs, reads the worker
catalogue plus the selected steward/candidate endpoints, and never generates
or certifies a candidate, changes a steward, activates a worker, or starts a
rollout.  Licence and resource-restriction metadata remain informational.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from mas_core.worker_registry.worker_steward_readiness import (
    WORKER_STEWARD_READINESS_SCHEMA,
    evaluate_worker_steward_readiness,
)

CHECK_SCHEMA = "aiat.worker-steward-readiness-check.v1"
REPO_ROOT = Path(__file__).resolve().parents[1]


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
        "--candidate-id",
        default=os.getenv("AIAT_LIVE_CANDIDATE_ID", ""),
        help="explicit selected steward candidate UUID; live mode never auto-selects",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP read timeout in seconds")
    return parser


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "readiness_schema": WORKER_STEWARD_READINESS_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "licence_metadata_is_gate": False,
        "reason": reason,
        "url_configured": url_configured,
        "no_mutation": True,
        "scope": "read-only selected steward candidate certification readiness",
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
    worker_id = "00000000-0000-4000-8000-000000000201"
    candidate_id = "00000000-0000-4000-8000-000000000202"
    report = evaluate_worker_steward_readiness(
        worker={
            "id": worker_id,
            "source_repo": "https://github.com/example/worker",
            "version_pin": "1.2.3",
        },
        steward={
            "worker_id": worker_id,
            "status": "READY",
            "provenance": {
                "canonical_source_repository": "https://github.com/example/worker",
                "exact_release": "1.2.3",
                "transport_type": "process",
                "security_scan_status": "passed",
                "license_id": "metadata-only",
            },
        },
        candidate={
            "candidate_id": candidate_id,
            "worker_id": worker_id,
            "intake_status": "CERTIFYING",
            "bundle": {
                "bundle_id": "00000000-0000-4000-8000-000000000203",
                "content_hash": "bundle-hash",
                "documentation_refs": ["00000000-0000-4000-8000-000000000204"],
                "verified_capabilities": {"capabilities": {"read_only": True}},
            },
            "adapter": {
                "adapter_id": "00000000-0000-4000-8000-000000000205",
                "version": "adapter-1.0.0",
                "content_hash": "adapter-hash",
            },
        },
        worker_id=worker_id,
        candidate_id=candidate_id,
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "readiness_schema": WORKER_STEWARD_READINESS_SCHEMA,
        "mode": "fixture",
        "status": report["status"],
        "licence_metadata_is_gate": False,
        "no_mutation": True,
        "readiness": report,
        "scope": "deterministic read-only fixture; no steward, candidate, worker, identity, or provider state changed",
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
    worker_id, worker_error = _uuid(str(args.worker_id or ""), "worker-id")
    candidate_id, candidate_error = _uuid(str(args.candidate_id or ""), "candidate-id")
    if worker_error or candidate_error:
        return _blocked(worker_error or candidate_error or "invalid selection", url_configured=True)

    headers = {"X-API-Key": api_key}
    base = url.rstrip("/")
    fetch_errors: dict[str, str] = {}
    worker: Mapping[str, Any] | None = None
    steward: Mapping[str, Any] | None = None
    candidate: Mapping[str, Any] | None = None
    try:
        with httpx.Client(timeout=float(args.timeout)) as client:
            worker_rows, worker_error = _get_json(
                client, base=base, path="/capabilities/workers", headers=headers
            )
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

            steward_payload, steward_error = _get_json(
                client,
                base=base,
                path=f"/capabilities/workers/{worker_id}/steward",
                headers=headers,
            )
            if steward_error:
                fetch_errors["steward"] = f"steward read returned {steward_error}"
            elif isinstance(steward_payload, Mapping):
                steward = steward_payload

            candidate_rows, candidate_error = _get_json(
                client,
                base=base,
                path=f"/capabilities/workers/{worker_id}/steward/candidates",
                headers=headers,
            )
            if candidate_error:
                fetch_errors["candidate"] = f"candidate read returned {candidate_error}"
            elif not isinstance(candidate_rows, list):
                fetch_errors["candidate"] = "candidate read returned an invalid collection"
            else:
                candidate = next(
                    (
                        item
                        for item in candidate_rows
                        if isinstance(item, Mapping)
                        and str(item.get("candidate_id") or item.get("id")) == candidate_id
                    ),
                    None,
                )
    except (httpx.HTTPError, ValueError, TypeError, OverflowError) as exc:
        return _blocked(f"live steward readiness read unavailable: {type(exc).__name__}", url_configured=True)

    readiness = evaluate_worker_steward_readiness(
        worker=worker,
        steward=steward,
        candidate=candidate,
        worker_id=worker_id or "",
        candidate_id=candidate_id or "",
        fetch_errors=fetch_errors,
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "readiness_schema": WORKER_STEWARD_READINESS_SCHEMA,
        "mode": "live",
        "status": readiness["status"],
        "licence_metadata_is_gate": False,
        "no_mutation": True,
        "readiness": readiness,
        "scope": "read-only selected external-worker steward certification readiness; no candidate generation, conformance run, approval, activation, rollout, identity, or provider mutation",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _live(args) if args.live else _fixture()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, default=str))
    else:
        print(f"worker steward readiness: {report['status']} — {report.get('scope', report.get('reason', ''))}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
