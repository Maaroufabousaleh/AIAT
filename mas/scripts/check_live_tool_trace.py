"""Run one bounded tool call and read its operator trace projection.

The probe uses the pure ``time_now`` tool and an existing project selected by
an operator-authenticated project listing.  It verifies that tool usage
accounting and the tool-service native span are both visible through the
canonical trace endpoint.  No tool payload, project ID, credential, or
response body is emitted.  Missing local configuration is ``blocked`` (exit
code 2); a configured but incomplete read-back is a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from mas_core.observability.tracing import is_safe_span_id, is_safe_trace_id

CHECK_SCHEMA = "aiat.live-tool-trace-check.v1"
TRACE_SCHEMA = "aiat.trace-evidence.v1"
DEFAULT_TOOL = "time_now"
_LOCAL_ORCHESTRATOR_HOSTS = {"127.0.0.1", "localhost", "::1"}
_COMPOSE_TOOL_SERVICE_HOSTS = {"tool-service", "mas-tool-service", "tool_service"}


def _resolve_tool_service_url(*, orchestrator_url: str, tool_service_url: str) -> tuple[str, str]:
    """Resolve the Compose-only service alias for a host-side local probe.

    The development ``.env`` intentionally uses ``tool-service:8002`` so
    containers can address one another.  The release checker usually runs on
    the host, where that DNS name is unavailable while the published port is
    reachable at loopback.  Rewrite only that narrow local shape; remote
    deployments and arbitrary configured URLs are left untouched.
    """

    service = urlsplit(tool_service_url)
    orchestrator = urlsplit(orchestrator_url)
    if (
        orchestrator.hostname in _LOCAL_ORCHESTRATOR_HOSTS
        and service.hostname in _COMPOSE_TOOL_SERVICE_HOSTS
    ):
        port = service.port or 8002
        return (
            urlunsplit((service.scheme or "http", f"127.0.0.1:{port}", service.path, service.query, service.fragment)),
            "local-compose-host-fallback",
        )
    return tool_service_url, "configured"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="exercise the configured deployment")
    parser.add_argument(
        "--orchestrator-url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
    )
    parser.add_argument(
        "--tool-service-url",
        default=os.getenv("AIAT_TOOL_SERVICE_URL", os.getenv("TOOL_SERVICE_URL", "")),
    )
    parser.add_argument(
        "--operator-api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", "")),
    )
    parser.add_argument("--tool-secret", default=os.getenv("TOOL_SECRET", ""))
    parser.add_argument(
        "--trace-id",
        default=os.getenv("AIAT_LIVE_TOOL_TRACE_ID", "aiat-live-tool-trace-check"),
    )
    parser.add_argument(
        "--span-id",
        default=os.getenv("AIAT_LIVE_TOOL_PARENT_SPAN_ID", "aiat-tool-parent-check"),
    )
    parser.add_argument("--tool-name", default=DEFAULT_TOOL)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def _blocked(reason: str, *, orchestrator_configured: bool = False, tool_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "orchestrator_configured": orchestrator_configured,
        "tool_service_configured": tool_configured,
        "scope": "one bounded time_now call plus operator trace read-back; only telemetry rows may be created",
    }


def _project_id(payload: Any) -> str | None:
    rows = payload if isinstance(payload, list) else (
        payload.get("projects") or payload.get("items") or payload.get("data")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            return str(row["id"]).strip()
    return None


def _live(
    *,
    orchestrator_url: str,
    tool_service_url: str,
    operator_api_key: str,
    tool_secret: str,
    trace_id: str,
    span_id: str,
    tool_name: str,
    timeout: float,
) -> dict[str, Any]:
    orchestrator_url = orchestrator_url.strip().rstrip("/")
    tool_service_url = tool_service_url.strip().rstrip("/")
    if not orchestrator_url:
        return _blocked("missing live configuration: orchestrator URL")
    if not tool_service_url:
        return _blocked("missing live configuration: tool-service URL", orchestrator_configured=True)
    if not operator_api_key.strip():
        return _blocked(
            "missing live configuration: operator API key",
            orchestrator_configured=True,
            tool_configured=True,
        )
    if not tool_secret.strip():
        return _blocked(
            "missing live configuration: tool secret",
            orchestrator_configured=True,
            tool_configured=True,
        )
    if not is_safe_trace_id(trace_id):
        return _blocked("trace ID is not a bounded safe identifier", orchestrator_configured=True, tool_configured=True)
    if not is_safe_span_id(span_id):
        return _blocked("span ID is not a bounded safe identifier", orchestrator_configured=True, tool_configured=True)
    if tool_name != DEFAULT_TOOL:
        return _blocked("live probe only permits the pure time_now tool", orchestrator_configured=True, tool_configured=True)

    tool_service_url, tool_service_endpoint_mode = _resolve_tool_service_url(
        orchestrator_url=orchestrator_url,
        tool_service_url=tool_service_url,
    )

    headers = {"X-API-Key": operator_api_key}
    try:
        with httpx.Client(timeout=max(1.0, min(float(timeout), 60.0))) as client:
            projects_response = client.get(f"{orchestrator_url}/projects", params={"limit": 1}, headers=headers)
            if projects_response.status_code != 200:
                return _blocked(
                    f"project listing returned HTTP {projects_response.status_code}",
                    orchestrator_configured=True,
                    tool_configured=True,
                )
            project_id = _project_id(projects_response.json())
            if not project_id:
                return _blocked(
                    "operator project listing contained no usable project",
                    orchestrator_configured=True,
                    tool_configured=True,
                )
            tool_response = client.post(
                f"{tool_service_url}/tools/{tool_name}/run",
                headers={"Authorization": f"Bearer {tool_secret}"},
                json={
                    "agent_id": "operator-trace-probe",
                    "sender_role": "orchestrator",
                    "sender_team": "exec",
                    "project_id": project_id,
                    "kwargs": {},
                    "trace_id": trace_id,
                    "span_id": span_id,
                },
            )
            tool_payload = tool_response.json() if tool_response.headers.get("content-type", "").startswith("application/json") else {}
            if tool_response.status_code != 200:
                return {
                    "schema_version": CHECK_SCHEMA,
                    "mode": "live",
                    "status": "fail",
                    "tool_http_status": tool_response.status_code,
                    "tool_success": False,
                    "trace_id_used": trace_id,
                    "tool_service_endpoint_mode": tool_service_endpoint_mode,
                    "scope": "one bounded time_now call; no response body emitted",
                }
            if not isinstance(tool_payload, dict) or tool_payload.get("success") is not True:
                return {
                    "schema_version": CHECK_SCHEMA,
                    "mode": "live",
                    "status": "fail",
                    "tool_http_status": tool_response.status_code,
                    "tool_success": False,
                    "error_code": str(tool_payload.get("error_code") or "unknown") if isinstance(tool_payload, dict) else "invalid_response",
                    "trace_id_used": trace_id,
                    "tool_service_endpoint_mode": tool_service_endpoint_mode,
                    "scope": "one bounded time_now call; no response body emitted",
                }
            evidence_response = client.get(
                f"{orchestrator_url}/observability/traces/{trace_id}",
                headers=headers,
                params={"limit": 100},
            )
            if evidence_response.status_code != 200:
                return {
                    "schema_version": CHECK_SCHEMA,
                    "mode": "live",
                    "status": "fail",
                    "tool_http_status": tool_response.status_code,
                    "tool_success": True,
                    "evidence_http_status": evidence_response.status_code,
                    "trace_id_used": trace_id,
                    "tool_service_endpoint_mode": tool_service_endpoint_mode,
                    "scope": "operator trace read-back only; no response body emitted",
                }
            evidence = evidence_response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return _blocked(
            "live tool trace boundary unavailable",
            orchestrator_configured=True,
            tool_configured=True,
        )

    if not isinstance(evidence, dict) or evidence.get("schema_version") != TRACE_SCHEMA:
        return {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "fail",
            "tool_http_status": 200,
            "tool_success": True,
            "evidence_status": "invalid",
            "trace_id_used": trace_id,
            "scope": "operator trace read-back only; no response body emitted",
        }
    items = evidence.get("items")
    source_counts = evidence.get("source_counts")
    coverage = evidence.get("coverage")
    if not isinstance(items, list) or not isinstance(source_counts, dict) or not isinstance(coverage, dict):
        return {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "fail",
            "tool_http_status": 200,
            "tool_success": True,
            "evidence_status": "incomplete",
            "trace_id_used": trace_id,
            "scope": "operator trace read-back only; no response body emitted",
        }
    native_tool_spans = [
        row for row in items
        if isinstance(row, dict)
        and row.get("kind") == "native_span"
        and row.get("operation") == tool_name
        and row.get("service") == "tool_service"
    ]
    usage_events = [
        row for row in items
        if isinstance(row, dict)
        and row.get("kind") == "usage"
        and row.get("event_type") == "tool"
        and row.get("tool_name") == tool_name
    ]
    result = {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "pass" if evidence.get("status") == "observed" and native_tool_spans and usage_events else "fail",
        "tool_http_status": 200,
        "tool_success": True,
        "evidence_status": str(evidence.get("status") or "unknown"),
        "trace_id_used": trace_id,
        "item_count": int(evidence.get("item_count") or 0),
        "source_counts": {str(key): int(value) for key, value in source_counts.items()},
        "coverage": {str(key): str(value) for key, value in coverage.items()},
        "native_tool_span_count": len(native_tool_spans),
        "tool_usage_event_count": len(usage_events),
        "tool_service_endpoint_mode": tool_service_endpoint_mode,
        "native_operations": sorted({str(row.get("operation")) for row in native_tool_spans}),
        "native_services": sorted({str(row.get("service")) for row in native_tool_spans}),
        "scope": "one bounded time_now call plus operator trace read-back; only telemetry rows may be created",
    }
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = (
        _live(
            orchestrator_url=args.orchestrator_url,
            tool_service_url=args.tool_service_url,
            operator_api_key=args.operator_api_key,
            tool_secret=args.tool_secret,
            trace_id=args.trace_id,
            span_id=args.span_id,
            tool_name=args.tool_name,
            timeout=args.timeout,
        )
        if args.live
        else {
            "schema_version": CHECK_SCHEMA,
            "mode": "fixture",
            "status": "pass",
            "live_required": True,
            "scope": "deterministic contract fixture; no network or database state changed",
        }
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"live tool trace: {report['status']} — {report.get('reason', report.get('scope', ''))}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    sys.exit(main())
