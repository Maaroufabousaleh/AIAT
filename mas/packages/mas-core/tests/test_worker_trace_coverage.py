"""Worker trace source-coverage contract and release evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mas_core.observability.mail_edge import normalize_provider_webhook
from mas_core.observability.trace_evidence import build_trace_evidence
from mas_core.observability.worker_trace_coverage import (
    WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
    evaluate_worker_mail_edge_coverage,
    evaluate_worker_trace_coverage,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _evidence(*, include_worker: bool = True):
    native_rows = [
        {"id": "model-span", "source_kind": "model", "operation": "model.call", "service": "gateway"},
    ]
    if include_worker:
        native_rows.append(
            {"id": "worker-span", "source_kind": "worker", "operation": "worker.execute", "service": "runner"}
        )
    return build_trace_evidence(
        trace_id="coverage-trace",
        worker_usage_rows=[{"id": "usage", "run_id": "run", "prompt_tokens": 1, "completion_tokens": 1}],
        artifact_rows=[{"id": "artifact", "run_id": "run", "artifact_id": 1, "size_bytes": 1}],
        native_span_rows=native_rows,
    )


def test_native_source_coverage_is_explicit_and_worker_predicate_is_fail_closed() -> None:
    observed = _evidence()
    assert observed.coverage["native_model_spans"] == "observed"
    assert observed.coverage["native_worker_spans"] == "observed"
    assert evaluate_worker_trace_coverage(observed)["status"] == "pass"

    missing = evaluate_worker_trace_coverage(_evidence(include_worker=False))
    assert missing["status"] == "fail"
    assert missing["missing_required_sources"] == ["native_worker_spans"]
    assert missing["licence_metadata_is_gate"] is False


def test_worker_trace_coverage_fixture_reports_model_worker_and_optional_integration_sources() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_worker_trace_coverage.py", "--json", "--require-integration"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.worker-trace-coverage-check.v1"
    assert report["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False
    assert report["required_sources"]["native_model_spans"] == "observed"
    assert report["required_sources"]["native_worker_spans"] == "observed"
    assert report["required_sources"]["native_integration_spans"] == "observed"
    assert report["mail_edge_status"] == "not_checked"


def test_worker_trace_coverage_live_dispatch_requires_explicit_confirmation() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_worker_trace_coverage.py",
            "--live",
            "--dispatch",
            "--json",
            "--url",
            "http://localhost:8000",
            "--api-key",
            "test-only-not-secret",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert report["licence_metadata_is_gate"] is False
    assert "confirm-dispatch" in report["reason"]


def test_worker_mail_edge_coverage_joins_independent_payload_free_contracts() -> None:
    trace_id = "worker-mail-trace"
    worker_id = "00000000-0000-4000-a000-000000000904"
    evidence = build_trace_evidence(
        trace_id=trace_id,
        worker_usage_rows=[{"id": "usage", "run_id": "run", "prompt_tokens": 1}],
        artifact_rows=[{"id": "artifact", "run_id": "run", "artifact_id": 1}],
        integration_evidence_rows=[{"id": "integration", "connection_id": "conn"}],
        native_span_rows=[
            {"id": "model", "source_kind": "model", "operation": "model.call"},
            {"id": "worker", "source_kind": "worker", "operation": "worker.execute"},
            {"id": "integration-span", "source_kind": "integration", "operation": "pm.update"},
        ],
    )
    delivered = normalize_provider_webhook(
        "resend",
        {"id": "event-delivered", "type": "email.delivered", "data": {"email_id": "message"}},
        signature_verified=True,
        worker_id=worker_id,
        trace_id=trace_id,
    )
    bounced = normalize_provider_webhook(
        "resend",
        {"id": "event-bounced", "type": "email.bounced", "data": {"email_id": "message-2"}},
        signature_verified=True,
        worker_id=worker_id,
        trace_id=trace_id,
    )
    report = evaluate_worker_mail_edge_coverage(
        evidence,
        [delivered, bounced],
        trace_id=trace_id,
        worker_id=worker_id,
        require_integration=True,
    )
    assert report["schema_version"] == WORKER_MAIL_EDGE_COVERAGE_SCHEMA
    assert report["status"] == "pass"
    assert report["worker_trace"]["status"] == "pass"
    assert report["mail_edge"]["status"] == "pass"
    assert report["licence_metadata_is_gate"] is False


def test_worker_mail_edge_coverage_does_not_hide_missing_scope_or_provider_signals() -> None:
    evidence = _evidence()
    report = evaluate_worker_mail_edge_coverage(evidence, [], require_mail_edge=True)
    assert report["status"] == "fail"
    assert "mail_edge_trace_scope" in report["missing_required_signals"]
    assert "mail_edge_worker_scope" in report["missing_required_signals"]
