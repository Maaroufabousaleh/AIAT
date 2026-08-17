"""Evaluate the evidence required for one model-backed Worker Run.

This module only evaluates the already bounded :class:`TraceEvidence` read
model.  It does not select a worker, authorize a run, or treat provenance or
licence metadata as an execution predicate.  Integration, audit, and mail
edge sources remain separately visible so a worker-run pass cannot be
mistaken for complete distributed coverage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from .mail_edge import MailEdgeObservation, evaluate_mail_edge_coverage
from .trace_evidence import TraceEvidence

WORKER_TRACE_COVERAGE_SCHEMA = "aiat.worker-trace-coverage.v1"
WORKER_MAIL_EDGE_COVERAGE_SCHEMA = "aiat.worker-mail-edge-coverage.v1"
WORKER_TRACE_REQUIRED_SOURCES = (
    "worker_usage_records",
    "worker_artifacts",
    "native_model_spans",
    "native_worker_spans",
)
WORKER_TRACE_OPTIONAL_SOURCES = (
    "native_audit_spans",
    "native_integration_spans",
    "integration_evidence",
    "native_mail_spans",
)


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _observed(evidence: TraceEvidence | Mapping[str, Any], source: str) -> bool:
    """Return whether one source has at least one bounded observed row."""

    if isinstance(evidence, TraceEvidence):
        coverage: Mapping[str, Any] = evidence.coverage
        source_counts: Mapping[str, Any] = evidence.source_counts
    else:
        raw_coverage = evidence.get("coverage")
        raw_counts = evidence.get("source_counts")
        coverage = cast("Mapping[str, Any]", raw_coverage) if isinstance(raw_coverage, Mapping) else {}
        source_counts = cast("Mapping[str, Any]", raw_counts) if isinstance(raw_counts, Mapping) else {}
    if source in coverage and str(coverage.get(source) or "") == "observed":
        return True
    if source in source_counts:
        return _count(source_counts.get(source)) > 0
    return False


def evaluate_worker_trace_coverage(
    evidence: TraceEvidence | Mapping[str, Any],
    *,
    require_integration: bool = False,
) -> dict[str, Any]:
    """Evaluate model-backed worker source coverage without exposing payloads.

    The default predicate is deliberately limited to model usage, worker
    artifact, and native model/worker spans.  ``require_integration`` adds
    native integration and durable integration evidence for a run that is
    explicitly expected to exercise a provider/integration adapter.
    """

    if isinstance(evidence, TraceEvidence):
        coverage: Mapping[str, Any] = evidence.coverage
        source_counts: Mapping[str, Any] = evidence.source_counts
        trace_status: str = evidence.status
    elif isinstance(evidence, Mapping):
        raw_coverage = evidence.get("coverage")
        raw_counts = evidence.get("source_counts")
        coverage = cast("Mapping[str, Any]", raw_coverage) if isinstance(raw_coverage, Mapping) else {}
        source_counts = cast("Mapping[str, Any]", raw_counts) if isinstance(raw_counts, Mapping) else {}
        trace_status = str(evidence.get("status") or "unknown")
    else:
        raise TypeError("evidence must be TraceEvidence or a mapping")

    required = list(WORKER_TRACE_REQUIRED_SOURCES)
    if require_integration:
        required.extend(("native_integration_spans", "integration_evidence"))
    required_status = {
        source: "observed" if (
            str(coverage.get(source) or "") == "observed"
            or _count(source_counts.get(source)) > 0
        ) else "empty"
        for source in required
    }
    optional_status = {
        source: "observed" if _observed(evidence, source) else "empty"
        for source in WORKER_TRACE_OPTIONAL_SOURCES
        if source not in required
    }
    missing = [source for source, status in required_status.items() if status != "observed"]
    return {
        "schema_version": WORKER_TRACE_COVERAGE_SCHEMA,
        "status": "pass" if not missing else "fail",
        "licence_metadata_is_gate": False,
        "trace_status": trace_status,
        "require_integration": bool(require_integration),
        "required_sources": required_status,
        "optional_sources": optional_status,
        "missing_required_sources": missing,
        "worker_run_scope": (
            "model-backed worker source coverage; integration/mail-edge and live retention are separate gates"
        ),
    }


def evaluate_worker_mail_edge_coverage(
    evidence: TraceEvidence | Mapping[str, Any],
    observations: Iterable[MailEdgeObservation | Mapping[str, Any]],
    *,
    trace_id: str | None = None,
    worker_id: str | None = None,
    require_integration: bool = False,
    require_mail_edge: bool = True,
) -> dict[str, Any]:
    """Join worker trace sources with payload-free mail-edge observations.

    The worker and mail-edge evaluators remain independent authorities.  This
    function only provides an explicit cross-surface evidence result so an
    operator cannot mistake a worker trace pass for provider/mail coverage.
    It never selects, activates, dispatches, or authorizes a worker.
    """

    worker_report = evaluate_worker_trace_coverage(
        evidence,
        require_integration=require_integration,
    )
    mail_report = evaluate_mail_edge_coverage(
        observations,
        trace_id=trace_id,
        worker_id=worker_id,
    )
    missing: list[str] = []
    if worker_report["status"] != "pass":
        missing.append("worker_trace_sources")
    if require_mail_edge:
        if not trace_id:
            missing.append("mail_edge_trace_scope")
        if not worker_id:
            missing.append("mail_edge_worker_scope")
        if mail_report["status"] != "pass":
            missing.extend(
                f"mail_edge:{name}"
                for name in mail_report.get("missing", [])
                if isinstance(name, str)
            )
    return {
        "schema_version": WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
        "status": "pass" if not missing else "fail",
        "licence_metadata_is_gate": False,
        "require_integration": bool(require_integration),
        "require_mail_edge": bool(require_mail_edge),
        "trace_id": trace_id,
        "worker_id": worker_id,
        "worker_trace": worker_report,
        "mail_edge": mail_report,
        "missing_required_signals": sorted(set(missing)),
        "scope": (
            "cross-surface worker trace and payload-free mail-edge evidence; "
            "live worker execution and external provider delivery remain separate"
        ),
    }


__all__ = [
    "WORKER_MAIL_EDGE_COVERAGE_SCHEMA",
    "WORKER_TRACE_COVERAGE_SCHEMA",
    "WORKER_TRACE_REQUIRED_SOURCES",
    "WORKER_TRACE_OPTIONAL_SOURCES",
    "evaluate_worker_trace_coverage",
    "evaluate_worker_mail_edge_coverage",
]
