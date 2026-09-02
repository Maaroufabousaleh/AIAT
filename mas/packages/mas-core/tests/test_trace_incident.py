from __future__ import annotations

from mas_core.observability.trace_evidence import build_trace_evidence
from mas_core.observability.trace_incident import build_trace_incident


def test_trace_incident_classifies_http_and_worker_failures_without_payloads() -> None:
    evidence = build_trace_evidence(
        trace_id="incident-trace-1",
        api_rows=[
            {
                "id": "api-503",
                "method": "POST",
                "route": "/workers",
                "status_code": 503,
                "outcome": "error",
                "occurred_at": "2026-08-17T00:00:00+00:00",
                "body": "secret body must not return",
            }
        ],
        transition_rows=[
            {
                "id": "transition-1",
                "run_id": "run-1",
                "to_state": "FAILED",
                "created_at": "2026-08-17T00:00:01+00:00",
                "metadata": {"token": "drop-me"},
            }
        ],
        native_span_rows=[
            {
                "id": "span-1",
                "source_kind": "worker",
                "operation": "worker.run",
                "service": "worker-runtime",
                "status": "failed",
                "started_at": "2026-08-17T00:00:02+00:00",
            }
        ],
    )

    incident = build_trace_incident(evidence)

    assert incident.schema_version == "aiat.trace-incident.v1"
    assert incident.status == "attention"
    assert incident.severity == "critical"
    assert incident.finding_count == 3
    assert incident.affected_sources == ["api_requests", "native_spans", "worker_run_transitions"]
    assert {finding.id for finding in incident.findings} == {"api-503", "transition-1", "span-1"}
    assert "TRACE_FAILURE_FINDINGS" in incident.notice_codes
    assert "secret body must not return" not in incident.model_dump_json()
    assert "drop-me" not in incident.model_dump_json()


def test_trace_incident_discloses_partial_coverage_without_marking_clear_trace_failed() -> None:
    evidence = build_trace_evidence(
        trace_id="incident-trace-2",
        api_rows=[
            {
                "id": "api-200",
                "method": "GET",
                "route": "/health",
                "status_code": 200,
                "outcome": "success",
                "occurred_at": "2026-08-17T00:00:00+00:00",
            }
        ],
    )

    incident = build_trace_incident(evidence)

    assert incident.status == "clear"
    assert incident.severity == "info"
    assert incident.coverage_status == "partial"
    assert incident.finding_count == 0
    assert "TRACE_COVERAGE_PARTIAL" in incident.notice_codes


def test_trace_incident_preserves_not_found_and_empty_coverage_state() -> None:
    evidence = build_trace_evidence(trace_id="incident-trace-missing")

    incident = build_trace_incident(evidence)

    assert incident.status == "not_found"
    assert incident.severity == "info"
    assert incident.coverage_status == "empty"
    assert incident.notice_codes[:2] == ["TRACE_NOT_FOUND", "TRACE_COVERAGE_EMPTY"]
    assert incident.notice_codes[-1] == "TRACE_NO_FAILURE_FINDINGS"
