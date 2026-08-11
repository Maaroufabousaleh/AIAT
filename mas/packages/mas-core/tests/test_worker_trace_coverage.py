"""Worker trace source-coverage contract and release evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mas_core.observability.trace_evidence import build_trace_evidence
from mas_core.observability.worker_trace_coverage import evaluate_worker_trace_coverage

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
