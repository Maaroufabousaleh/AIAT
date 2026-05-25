"""Prometheus metrics for the MAS platform.

Defines the 10 custom metrics required by Phase 12 (Step 48).
All metrics are module-level singletons so any service can import and
increment them.  Each FastAPI service should expose ``/metrics`` via
``prometheus_client.make_asgi_app`` or ``prometheus-fastapi-instrumentator``.

Usage::

    from mas_core.observability.metrics import MAS_MESSAGES_TOTAL
    MAS_MESSAGES_TOTAL.labels(direction="inbound", team="exec_ceo", msg_type="DIRECTIVE").inc()
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── 1. Message throughput ──────────────────────────────────────────────────
MAS_MESSAGES_TOTAL = Counter(
    "mas_messages_total",
    "Total messages published/consumed across Redis Streams.",
    ["direction", "team", "msg_type"],
)

# Phase 3 custom counters — expected by dashboards and integration tests.
# These are registered immediately so they appear in /metrics even when
# no message has been published yet.
MESSAGES_PUBLISHED_TOTAL = Counter(
    "messages_published_total",
    "Total messages successfully published to team streams.",
    ["team"],
)

MESSAGES_DLQ_TOTAL = Counter(
    "messages_dlq_total",
    "Total messages moved to the dead-letter queue.",
    ["team"],
)

# ── 2. Tool execution ─────────────────────────────────────────────────────
MAS_TOOL_CALLS_TOTAL = Counter(
    "mas_tool_calls_total",
    "Total tool invocations by name and outcome.",
    ["tool_name", "status"],
)

# Backward-compatible tool-service counters used by existing dashboards/tests.
TOOL_INVOCATIONS_TOTAL = Counter(
    "tool_invocations_total",
    "Total tool invocations by name and status.",
    ["tool_name", "status"],
)

TOOL_ERRORS_TOTAL = Counter(
    "tool_errors_total",
    "Total failed tool invocations by name and error code.",
    ["tool_name", "error_code"],
)

# ── 3. LLM calls ──────────────────────────────────────────────────────────
MAS_LLM_CALLS_TOTAL = Counter(
    "mas_llm_calls_total",
    "Total LLM inference calls by model and agent.",
    ["model", "agent_id"],
)

# ── 4. Budget exhaustion ──────────────────────────────────────────────────
MAS_BUDGET_EXHAUSTED_TOTAL = Counter(
    "mas_budget_exhausted_total",
    "Number of times an agent's budget was exhausted.",
    ["agent_id", "budget_type"],
)

# ── 5. Dead-letter queue depth ────────────────────────────────────────────
MAS_DLQ_DEPTH = Gauge(
    "mas_dlq_depth",
    "Current dead-letter count in Postgres per stream.",
    ["stream"],
)

# ── 6. Project state ──────────────────────────────────────────────────────
MAS_PROJECT_STATE = Gauge(
    "mas_project_state",
    "Current workflow state per project (1 = active in that state).",
    ["project_id", "state"],
)

# ── 7. Review circuit-breaker activations ─────────────────────────────────
MAS_REVIEW_CIRCUIT_OPEN = Gauge(
    "mas_review_circuit_open",
    "Whether the review circuit-breaker is open for a project (1/0).",
    ["project_id"],
)

# ── 8. Infrastructure lead time ───────────────────────────────────────────
MAS_INFRA_LEAD_TIME = Histogram(
    "mas_infra_lead_time",
    "Infra provisioning duration in seconds per project.",
    ["project_id"],
    buckets=(10, 30, 60, 120, 300, 600, 1800, 3600),
)

# ── 9. Agent estimation correction factor ─────────────────────────────────
MAS_AGENT_CORRECTION_FACTOR = Gauge(
    "mas_agent_correction_factor",
    "Per-agent estimation drift (ratio of actual/estimated).",
    ["agent_id"],
)

# ── 10. Tool circuit-breaker state ────────────────────────────────────────
MAS_TOOL_CIRCUIT_STATE = Gauge(
    "mas_tool_circuit_state",
    "Current circuit-breaker state per tool (0=CLOSED, 1=HALF_OPEN, 2=OPEN).",
    ["tool_name"],
)

# ── Helpers ────────────────────────────────────────────────────────────────

_CIRCUIT_STATE_MAP = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}


def set_tool_circuit_state(tool_name: str, state: str) -> None:
    """Convenience: set the tool circuit-breaker gauge from a state string."""
    MAS_TOOL_CIRCUIT_STATE.labels(tool_name=tool_name).set(_CIRCUIT_STATE_MAP.get(state, -1))
