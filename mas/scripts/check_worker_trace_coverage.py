"""Check model-backed Worker Run trace source coverage.

Fixture mode joins the real bounded trace projection with model-usage,
artifact, and native model/worker spans.  Live read mode inspects a selected
trace without mutation.  Live dispatch mode is intentionally opt-in: it
requires a selected active model-backed worker, project, and
``--confirm-dispatch`` before sending one bounded deterministic task.

The report contains only schema/count/status metadata.  It never prints API
keys, task input/output, provider payloads, or response bodies.  Integration,
mail-edge, and retention enforcement remain separate gates.  Licence or
restriction metadata is not an activation or execution predicate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import httpx

from mas_core.observability.trace_evidence import build_trace_evidence
from mas_core.observability.tracing import is_safe_trace_id
from mas_core.observability.worker_trace_coverage import (
    WORKER_TRACE_COVERAGE_SCHEMA,
    evaluate_worker_trace_coverage,
)

CHECK_SCHEMA = "aiat.worker-trace-coverage-check.v1"
TRACE_SCHEMA = "aiat.trace-evidence.v1"
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"})


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="inspect a configured deployment")
    parser.add_argument(
        "--dispatch",
        action="store_true",
        help="dispatch one bounded deterministic task before reading its trace",
    )
    parser.add_argument(
        "--confirm-dispatch",
        action="store_true",
        help="explicitly authorize the bounded live dispatch mutation",
    )
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
        help="selected active model-backed worker UUID for --dispatch",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("AIAT_LIVE_PROJECT_ID", ""),
        help="selected project UUID for --dispatch",
    )
    parser.add_argument(
        "--model-profile-id",
        default=os.getenv("AIAT_LIVE_MODEL_PROFILE_ID", ""),
        help="optional approved model-profile ID to request",
    )
    parser.add_argument(
        "--trace-id",
        default=os.getenv("AIAT_LIVE_WORKER_TRACE_ID", ""),
        help="existing trace ID, or the trace ID to assign to --dispatch",
    )
    parser.add_argument("--dispatch-mode", choices=("queued", "inline"), default="queued")
    parser.add_argument("--wait-seconds", type=float, default=45.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--require-integration",
        action="store_true",
        help="also require native integration and durable integration evidence",
    )
    parser.add_argument("--limit", type=int, default=300)
    return parser


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "licence_metadata_is_gate": False,
        "reason": reason,
        "url_configured": url_configured,
        "scope": "selected model-backed worker trace read; dispatch requires explicit confirmation",
        "mail_edge_status": "not_checked",
        "live_retention_status": "not_checked",
    }


def _safe_uuid(value: str, label: str) -> tuple[str | None, str | None]:
    try:
        parsed = UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None, f"{label} must be a UUID"
    return str(parsed), None


def _coverage_report(
    *,
    payload: Mapping[str, Any],
    trace_id: str,
    run_state: str | None,
    dispatched: bool,
    require_integration: bool,
) -> dict[str, Any]:
    if payload.get("schema_version") != TRACE_SCHEMA:
        return _blocked("orchestrator returned an invalid trace evidence model", url_configured=True)
    source_counts = payload.get("source_counts")
    coverage = payload.get("coverage")
    retention = payload.get("retention")
    if not isinstance(source_counts, Mapping) or not isinstance(coverage, Mapping) or not isinstance(retention, Mapping):
        return _blocked("trace evidence omitted bounded source coverage or retention", url_configured=True)
    evaluated = evaluate_worker_trace_coverage(payload, require_integration=require_integration)
    run_ok = not dispatched or run_state == "SUCCEEDED"
    status = "pass" if evaluated["status"] == "pass" and run_ok else "fail"
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "live",
        "status": status,
        "licence_metadata_is_gate": False,
        "trace_status": str(payload.get("status") or "unknown"),
        "trace_id_used": trace_id,
        "run_state": run_state,
        "dispatched": dispatched,
        "run_succeeded": run_ok,
        "item_count": int(payload.get("item_count") or 0),
        "source_counts": {
            str(key): _count(value) for key, value in source_counts.items()
        },
        "coverage": {str(key): str(value) for key, value in coverage.items()},
        "required_sources": dict(evaluated["required_sources"]),
        "optional_sources": dict(evaluated["optional_sources"]),
        "missing_required_sources": list(evaluated["missing_required_sources"]),
        "require_integration": bool(require_integration),
        "notice_codes": sorted(
            str(item.get("code"))
            for item in payload.get("notices") or []
            if isinstance(item, Mapping) and item.get("code")
        ),
        "retention": {
            "retention_days": retention.get("retention_days"),
            "sample_rate": retention.get("sample_rate"),
            "terminal_mode": retention.get("terminal_mode"),
            "source": retention.get("source"),
        },
        "mail_edge_status": "not_checked",
        "live_retention_status": "reported_not_enforced",
        "scope": "bounded model-backed worker source coverage; no raw payloads returned",
    }


def _fixture(require_integration: bool) -> dict[str, Any]:
    evidence = build_trace_evidence(
        trace_id="fixture-worker-trace-001",
        worker_usage_rows=[
            {
                "id": "fixture-worker-usage-001",
                "run_id": "fixture-run-001",
                "provider_id": "omniroute",
                "exact_model_id": "fixture-model",
                "prompt_tokens": 8,
                "completion_tokens": 12,
                "cost_usd": 0.01,
                "created_at": "2026-08-11T00:00:01+00:00",
            }
        ],
        artifact_rows=[
            {
                "id": "fixture-artifact-001",
                "run_id": "fixture-run-001",
                "artifact_id": 1,
                "kind": "report",
                "sha256": "a" * 64,
                "size_bytes": 64,
                "created_at": "2026-08-11T00:00:02+00:00",
            }
        ],
        integration_evidence_rows=[
            {
                "id": "fixture-integration-001",
                "connection_id": "fixture-connection",
                "evidence_type": "issue.updated",
                "created_at": "2026-08-11T00:00:03+00:00",
            }
        ],
        native_span_rows=[
            {
                "id": "fixture-model-span",
                "source_kind": "model",
                "operation": "omniroute.chat",
                "service": "llm_gateway",
                "status": "success",
                "started_at": "2026-08-11T00:00:01+00:00",
            },
            {
                "id": "fixture-worker-span",
                "source_kind": "worker",
                "operation": "worker.execute",
                "service": "team_runner",
                "status": "success",
                "started_at": "2026-08-11T00:00:01+00:00",
            },
            {
                "id": "fixture-integration-span",
                "source_kind": "integration",
                "operation": "pm.issue.updated",
                "service": "pm_gateway",
                "status": "success",
                "started_at": "2026-08-11T00:00:03+00:00",
            },
        ],
        generated_at="2026-08-11T00:00:04+00:00",
    )
    evaluated = evaluate_worker_trace_coverage(evidence, require_integration=require_integration)
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "fixture",
        "status": evaluated["status"],
        "licence_metadata_is_gate": False,
        "trace_status": evidence.status,
        "item_count": evidence.item_count,
        "source_counts": evidence.source_counts,
        "coverage": evidence.coverage,
        "required_sources": evaluated["required_sources"],
        "optional_sources": evaluated["optional_sources"],
        "missing_required_sources": evaluated["missing_required_sources"],
        "require_integration": bool(require_integration),
        "mail_edge_status": "not_checked",
        "live_retention_status": "not_checked",
        "scope": "deterministic fixture; no database, worker, provider, or deployment state changed",
    }


def _preflight_worker(client: httpx.Client, *, base: str, headers: Mapping[str, str], worker_id: str) -> Mapping[str, Any] | None:
    try:
        response = client.get(f"{base}/capabilities/workers", headers=dict(headers))
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        workers = response.json()
    except ValueError:
        return None
    if not isinstance(workers, list):
        return None
    for item in workers:
        if isinstance(item, Mapping) and str(item.get("id")) == worker_id:
            return {str(key): value for key, value in item.items()}
    return None


def _live(args: argparse.Namespace) -> dict[str, Any]:
    url = str(args.url or "").strip()
    api_key = str(args.api_key or "").strip()
    if not url:
        return _blocked("missing live configuration: orchestrator URL")
    if not api_key:
        return _blocked("missing live configuration: operator API key", url_configured=True)
    if args.dispatch and not args.confirm_dispatch:
        return _blocked("live dispatch requires --confirm-dispatch", url_configured=True)
    if not args.dispatch and not str(args.trace_id or "").strip():
        return _blocked("read mode requires --trace-id", url_configured=True)
    if args.wait_seconds <= 0 or args.wait_seconds > 300:
        return _blocked("wait-seconds must be between 0 and 300", url_configured=True)
    trace_id = str(args.trace_id or "").strip() or f"aiat-live-worker-{uuid4().hex}"
    if not is_safe_trace_id(trace_id):
        return _blocked("trace ID is not a bounded safe identifier", url_configured=True)
    headers = {"X-API-Key": api_key}
    base = url.rstrip("/")
    run_id: str | None = None
    run_state: str | None = None
    try:
        with httpx.Client(timeout=args.timeout) as client:
            if args.dispatch:
                worker_id, worker_error = _safe_uuid(args.worker_id, "worker-id")
                project_id, project_error = _safe_uuid(args.project_id, "project-id")
                if worker_error or project_error:
                    return _blocked(worker_error or project_error or "invalid dispatch selection", url_configured=True)
                worker = _preflight_worker(client, base=base, headers=headers, worker_id=worker_id or "")
                if worker is None:
                    return _blocked("selected worker is unavailable", url_configured=True)
                if str(worker.get("status") or "") not in {"ACTIVE", "DRAINING"}:
                    return _blocked("selected worker is not active", url_configured=True)
                if str(worker.get("model_mode") or "none") == "none":
                    return _blocked("selected worker is not model-backed", url_configured=True)
                selected_profile = str(args.model_profile_id or worker.get("model_profile_id") or "").strip()
                if not selected_profile:
                    return _blocked("model-backed worker has no approved model profile selection", url_configured=True)
                dispatch_payload: dict[str, Any] = {
                    "worker_id": worker_id,
                    "project_id": project_id,
                    "idempotency_key": f"worker-trace-coverage:{trace_id}",
                    "task_type": "aiat_live_worker_trace_coverage",
                    "task_input": {"check": "aiat-worker-trace-coverage-v1"},
                    "requested_model_profile": {"profile_id": selected_profile},
                    "workspace_mode": "isolated",
                    "timeout_seconds": min(120, max(1, int(args.wait_seconds))),
                    "budget_usd": 0.10,
                    "prompt_tokens": 32,
                    "expected_output_tokens": 64,
                    "dispatch_mode": args.dispatch_mode,
                    "lease_seconds": max(30, min(300, int(args.wait_seconds))),
                }
                response = client.post(
                    f"{base}/workers/runs",
                    headers={**headers, "X-AIAT-Trace-ID": trace_id},
                    json=dispatch_payload,
                )
                if response.status_code != 202:
                    return _blocked(f"worker dispatch returned HTTP {response.status_code}", url_configured=True)
                try:
                    accepted = response.json()
                except ValueError:
                    return _blocked("worker dispatch returned invalid JSON", url_configured=True)
                if not isinstance(accepted, Mapping) or not accepted.get("run_id"):
                    return _blocked("worker dispatch omitted a run ID", url_configured=True)
                run_id = str(accepted["run_id"])
                deadline = time.monotonic() + float(args.wait_seconds)
                while time.monotonic() < deadline:
                    status_response = client.get(f"{base}/workers/runs/{run_id}", headers=headers)
                    if status_response.status_code != 200:
                        return _blocked(f"worker status returned HTTP {status_response.status_code}", url_configured=True)
                    try:
                        status_payload = status_response.json()
                    except ValueError:
                        return _blocked("worker status returned invalid JSON", url_configured=True)
                    if not isinstance(status_payload, Mapping):
                        return _blocked("worker status returned an invalid model", url_configured=True)
                    run_state = str(status_payload.get("state") or "")
                    if run_state in TERMINAL_STATES:
                        break
                    time.sleep(0.5)
                if run_state not in TERMINAL_STATES:
                    return _blocked("worker run did not reach a terminal state within the bounded wait", url_configured=True)
            evidence_response = client.get(
                f"{base}/observability/traces/{trace_id}",
                headers=headers,
                params={"limit": max(1, min(int(args.limit), 300))},
            )
            if evidence_response.status_code != 200:
                return _blocked(f"trace evidence returned HTTP {evidence_response.status_code}", url_configured=True)
            try:
                evidence_payload = evidence_response.json()
            except ValueError:
                return _blocked("trace evidence returned invalid JSON", url_configured=True)
    except (httpx.HTTPError, ValueError, TypeError, OverflowError) as exc:
        return _blocked(f"live worker trace evidence unavailable: {type(exc).__name__}", url_configured=True)
    if not isinstance(evidence_payload, Mapping):
        return _blocked("trace evidence returned an invalid model", url_configured=True)
    report = _coverage_report(
        payload=evidence_payload,
        trace_id=trace_id,
        run_state=run_state,
        dispatched=args.dispatch,
        require_integration=args.require_integration,
    )
    if run_id:
        report["run_id"] = run_id
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _live(args) if args.live else _fixture(args.require_integration)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"worker trace coverage: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
