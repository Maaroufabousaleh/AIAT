"""MAS observability: Prometheus metrics, structured logging, trace-id propagation."""

from mas_core.observability.native_spans import (
    NATIVE_TRACE_SPAN_SCHEMA,
    NATIVE_TRACE_SPAN_SOURCE_KINDS,
    NativeTraceSpan,
    build_native_trace_span,
)
from mas_core.observability.trace_evidence import (
    TRACE_EVIDENCE_SCHEMA,
    TRACE_RETENTION_SCHEMA,
    TraceEvidence,
    TraceEvidenceItem,
    TraceRetentionPolicy,
    build_trace_evidence,
    trace_retention_from_manifest,
)
from mas_core.observability.logging import configure_logging
from mas_core.observability.metrics import (
    MAS_AGENT_CORRECTION_FACTOR,
    MAS_BUDGET_EXHAUSTED_TOTAL,
    MAS_DLQ_DEPTH,
    MAS_INFRA_LEAD_TIME,
    MAS_LLM_CALLS_TOTAL,
    MAS_MESSAGES_TOTAL,
    MAS_PROJECT_STATE,
    MAS_REVIEW_CIRCUIT_OPEN,
    MAS_TOOL_CALLS_TOTAL,
    MAS_TOOL_CIRCUIT_STATE,
    set_tool_circuit_state,
)
from mas_core.observability.tracing import bind_trace_id, clear_trace_context, new_trace_id

__all__ = [
    # Prometheus metrics
    "MAS_MESSAGES_TOTAL",
    "MAS_TOOL_CALLS_TOTAL",
    "MAS_LLM_CALLS_TOTAL",
    "MAS_BUDGET_EXHAUSTED_TOTAL",
    "MAS_DLQ_DEPTH",
    "MAS_PROJECT_STATE",
    "MAS_REVIEW_CIRCUIT_OPEN",
    "MAS_INFRA_LEAD_TIME",
    "MAS_AGENT_CORRECTION_FACTOR",
    "MAS_TOOL_CIRCUIT_STATE",
    "set_tool_circuit_state",
    # Logging
    "configure_logging",
    # Tracing
    "bind_trace_id",
    "clear_trace_context",
    "new_trace_id",
]
