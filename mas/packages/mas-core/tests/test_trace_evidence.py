from __future__ import annotations

from mas_core.observability.native_spans import build_native_trace_span
from mas_core.observability.trace_evidence import (
    TRACE_EVIDENCE_SCHEMA,
    build_trace_evidence,
    trace_retention_from_manifest,
)


def test_trace_evidence_is_bounded_and_secret_safe() -> None:
    report = build_trace_evidence(
        trace_id="trace-123",
        generated_at="2026-08-10T00:00:00+00:00",
        api_rows=[
            {
                "id": "api-1",
                "method": "GET",
                "route": "/health",
                "status_code": 200,
                "outcome": "success",
                "duration_ms": 2,
                "occurred_at": "2026-08-10T00:00:00+00:00",
            }
        ],
        task_rows=[
            {
                "task_id": "task-1",
                "agent_id": "tester",
                "team_id": "dept_qa",
                "status": "SUCCEEDED",
                "input": {"project_id": "project-1", "secret": "never-return"},
                "output": {"result": "never-return"},
                "created_at": "2026-08-10T00:00:00+00:00",
            }
        ],
        usage_rows=[
            {
                "id": "usage-1",
                "project_id": "project-1",
                "event_type": "tool",
                "tool_name": "clock.now",
                "status": "success",
                "span_id": "span-1",
                "duration_ms": "2.5",
                "cost_usd": "0.01",
                "details": {"token": "never-return"},
                "occurred_at": "2026-08-10T00:00:01+00:00",
            }
        ],
        transition_rows=[
            {
                "id": "transition-1",
                "run_id": "run-1",
                "from_state": "QUEUED",
                "to_state": "RUNNING",
                "metadata": {"secret": "never-return"},
                "created_at": "2026-08-10T00:00:02+00:00",
            }
        ],
        limit=2,
    )

    assert report.schema_version == TRACE_EVIDENCE_SCHEMA
    assert report.status == "observed"
    assert report.item_count == 2
    assert report.project_ids == ["project-1"]
    assert report.source_counts["worker_run_transitions"] == 1
    assert report.source_counts["api_requests"] == 1
    api_report = build_trace_evidence(
        trace_id="trace-123",
        api_rows=[
            {
                "id": "api-1",
                "method": "GET",
                "route": "/health",
                "status_code": 200,
                "outcome": "success",
                "duration_ms": 2,
                "occurred_at": "2026-08-10T00:00:00+00:00",
            }
        ],
    )
    api_item = next(item for item in api_report.items if item.source == "api_requests")
    assert api_item.route == "/health"
    assert api_item.status_code == 200
    payload = report.model_dump(mode="json")
    assert "never-return" not in str(payload)
    assert report.notices[0]["code"] == "PARTIAL_TRACE_SOURCES"


def test_trace_retention_projects_company_manifest_metadata() -> None:
    policy = trace_retention_from_manifest(
        {
            "manifest_json": {
                "retention": {
                    "trace_days": 42,
                    "trace_sample_rate": 0.25,
                    "terminal_mode": "delete",
                }
            }
        }
    )

    assert policy.retention_days == 42
    assert policy.sample_rate == 0.25
    assert policy.terminal_mode == "delete"
    assert policy.source == "company_manifest"


def test_trace_evidence_joins_run_correlated_usage_artifacts_and_pm_metadata() -> None:
    report = build_trace_evidence(
        trace_id="trace-123",
        worker_usage_rows=[
            {
                "id": "worker-usage-1",
                "run_id": "run-1",
                "provider_id": "omniroute",
                "exact_model_id": "model-1",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "duration_ms": 20,
                "cost_usd": 0.01,
                "resource_json": {"secret": "never-return"},
                "created_at": "2026-08-10T00:00:03+00:00",
            }
        ],
        artifact_rows=[
            {
                "id": "worker-artifact-1",
                "run_id": "run-1",
                "artifact_id": 42,
                "kind": "report",
                "sha256": "a" * 64,
                "size_bytes": 128,
                "metadata": {"secret": "never-return"},
                "created_at": "2026-08-10T00:00:04+00:00",
            }
        ],
        integration_rows=[
            {
                "id": "pm-event-1",
                "connection_id": "connection-1",
                "event_type": "issue.updated",
                "status": "PROCESSED",
                "payload": {"secret": "never-return"},
                "received_at": "2026-08-10T00:00:05+00:00",
            }
        ],
        integration_evidence_rows=[
            {
                "id": "integration-evidence-1",
                "connection_id": "connection-1",
                "project_id": "project-1",
                "evidence_type": "pull_request.updated",
                "span_id": "span-integration-1",
                "created_at": "2026-08-10T00:00:06+00:00",
                "payload": {"secret": "never-return"},
            }
        ],
    )

    assert report.source_counts["worker_usage_records"] == 1
    assert report.source_counts["worker_artifacts"] == 1
    assert report.source_counts["pm_inbox_events"] == 1
    assert report.source_counts["integration_evidence"] == 1
    usage = next(item for item in report.items if item.source == "worker_usage_records")
    assert usage.total_tokens == 15
    assert usage.exact_model_id == "model-1"
    artifact = next(item for item in report.items if item.source == "worker_artifacts")
    assert artifact.artifact_id == "42"
    assert artifact.size_bytes == 128
    integration = next(item for item in report.items if item.source == "integration_evidence")
    assert integration.event_type == "pull_request.updated"
    assert integration.project_id == "project-1"
    assert integration.span_id == "span-integration-1"
    assert "never-return" not in report.model_dump_json()


def test_trace_retention_falls_back_for_invalid_optional_metadata() -> None:
    policy = trace_retention_from_manifest(
        {"manifest_json": {"retention": {"trace_sample_rate": "not-a-rate"}}}
    )
    assert policy.source == "default"
    assert policy.retention_days == 3650


def test_native_span_normalizer_is_bounded_and_filters_payload_keys() -> None:
    span = build_native_trace_span(
        trace_id="trace-123",
        span_id="span-123",
        source_kind="model",
        operation="omniroute.chat",
        service="llm_gateway",
        status="success",
        started_at="not-a-datetime",  # type: ignore[arg-type]
        duration_ms=12.5,
        attributes={
            "model": "fixture-model",
            "prompt_tokens": 10,
            "authorization": "must-drop",
            "request_body": "must-drop",
            "nested": {"secret": "must-drop"},
        },
    )
    assert span["trace_id"] == "trace-123"
    assert span["span_id"] == "span-123"
    assert span["duration_ms"] == 12.5
    assert span["attributes"] == {"model": "fixture-model", "prompt_tokens": 10}


def test_native_span_projection_is_explicit_for_transport_and_mail_gaps() -> None:
    report = build_trace_evidence(
        trace_id="trace-123",
        native_span_rows=[
            {
                "id": "native-1",
                "trace_id": "trace-123",
                "span_id": "span-123",
                "source_kind": "transport",
                "operation": "/workers",
                "service": "orchestrator_api",
                "status": "success",
                "sampled": True,
                "started_at": "2026-08-10T00:00:00+00:00",
                "duration_ms": 5,
                "attributes_json": {"request_body": "never-return"},
            }
        ],
    )
    assert report.source_counts["native_spans"] == 1
    item = next(item for item in report.items if item.source == "native_spans")
    assert item.operation == "/workers"
    assert item.service == "orchestrator_api"
    assert item.sampled is True
    assert "never-return" not in report.model_dump_json()
    assert "mail-edge" in report.notices[0]["message"]
