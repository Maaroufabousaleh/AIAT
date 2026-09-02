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

from threading import Lock
from typing import TYPE_CHECKING

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

from mas_core.workflow.states import ProjectState

if TYPE_CHECKING:
    from collections.abc import Iterable

# Cardinality budgets are intentionally conservative for the bounded platform
# metrics.  Per-project drill-down belongs in logs/traces/audit queries rather
# than labels, so project-state is allowed only a finite workflow-state set.
METRIC_SERIES_BUDGET = 2_000
METRIC_FAMILY_SERIES_BUDGETS = {
    "mas_project_state": 32,
    "mas_review_circuit_open": 4,
    "mas_infra_lead_time": 20,
}

# Every AIAT-owned label is classified here so cardinality review does not
# depend on which counters happened to receive a sample during a scrape.  The
# values are deliberately about the source of the bound, not a licence or
# resource decision: protocol enums and control-plane catalogues are bounded
# by their contracts, while histogram buckets are bounded by declaration.
METRIC_LABEL_POLICIES: dict[str, dict[str, dict[str, str]]] = {
    "mas_messages": {
        "direction": {"classification": "bounded", "basis": "message-direction-enum"},
        "team": {"classification": "bounded", "basis": "known-team-catalogue"},
        "msg_type": {"classification": "bounded", "basis": "message-type-enum"},
    },
    "mas_tool_calls": {
        "tool_name": {"classification": "bounded", "basis": "registered-tool-catalogue"},
        "status": {"classification": "bounded", "basis": "tool-outcome-enum"},
    },
    "mas_llm_calls": {
        "model": {"classification": "bounded", "basis": "approved-model-profile-catalogue"},
        "agent_id": {"classification": "bounded", "basis": "active-worker-registry"},
    },
    "mas_budget_exhausted": {
        "agent_id": {"classification": "bounded", "basis": "active-worker-registry"},
        "budget_type": {"classification": "bounded", "basis": "budget-type-enum"},
    },
    "mas_dlq_depth": {
        "stream": {"classification": "bounded", "basis": "known-stream-catalogue"},
    },
    "mas_project_state": {
        "state": {"classification": "bounded", "basis": "project-state-enum"},
    },
    "mas_review_circuit_open": {},
    "mas_infra_lead_time": {
        "le": {"classification": "bounded", "basis": "declared-histogram-buckets"},
    },
    "mas_agent_correction_factor": {
        "agent_id": {"classification": "bounded", "basis": "active-worker-registry"},
    },
    "mas_tool_circuit_state": {
        "tool_name": {"classification": "bounded", "basis": "registered-tool-catalogue"},
    },
}

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
    "Current workflow state aggregate (1 = at least one project is active in that state). Per-project detail is in audit/log records.",
    ["state"],
)

# The project-state metric is an aggregate presence gauge, not a per-project
# gauge. Keep a bounded in-process count so moving one project does not clear a
# state that still contains other projects. Startup reconciliation replaces
# this cache from Postgres after every process restart.
PROJECT_STATE_LABELS = tuple(state.value for state in ProjectState)
_project_state_counts = dict.fromkeys(PROJECT_STATE_LABELS, 0)
_project_state_lock = Lock()


def _project_state_label(state: object) -> str:
    return str(getattr(state, "value", state))


def _publish_project_state_presence() -> None:
    for state, count in _project_state_counts.items():
        MAS_PROJECT_STATE.labels(state=state).set(1 if count > 0 else 0)


def reconcile_project_state_metrics(states: Iterable[object]) -> dict[str, int]:
    """Replace aggregate project-state counts from authoritative state rows.

    Unknown persisted states are ignored for metrics and remain an application
    validation concern. The returned map is bounded to ``ProjectState``
    labels, which keeps the scrape contract stable even with many projects.
    """

    counts = dict.fromkeys(PROJECT_STATE_LABELS, 0)
    for state in states:
        label = _project_state_label(state)
        if label in counts:
            counts[label] += 1
    with _project_state_lock:
        _project_state_counts.clear()
        _project_state_counts.update(counts)
        _publish_project_state_presence()
        return dict(_project_state_counts)


def observe_project_state(state: object) -> None:
    """Add one newly-created project to the aggregate state cache."""

    label = _project_state_label(state)
    if label not in _project_state_counts:
        return
    with _project_state_lock:
        _project_state_counts[label] += 1
        _publish_project_state_presence()


def record_project_state_transition(prior_state: object, next_state: object) -> None:
    """Apply one committed project transition to the bounded aggregate cache."""

    prior = _project_state_label(prior_state)
    next_value = _project_state_label(next_state)
    with _project_state_lock:
        if prior != next_value and prior in _project_state_counts:
            _project_state_counts[prior] = max(0, _project_state_counts[prior] - 1)
        if next_value in _project_state_counts and prior != next_value:
            _project_state_counts[next_value] += 1
        _publish_project_state_presence()

# ── 7. Review circuit-breaker activations ─────────────────────────────────
MAS_REVIEW_CIRCUIT_OPEN = Gauge(
    "mas_review_circuit_open",
    "Whether the review circuit-breaker is open (1/0); project drill-down is in audit records.",
)

# ── 8. Infrastructure lead time ───────────────────────────────────────────
MAS_INFRA_LEAD_TIME = Histogram(
    "mas_infra_lead_time",
    "Infra provisioning duration in seconds; project drill-down is in audit records.",
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

AIAT_METRIC_COLLECTORS = {
    "mas_messages": MAS_MESSAGES_TOTAL,
    "mas_tool_calls": MAS_TOOL_CALLS_TOTAL,
    "mas_llm_calls": MAS_LLM_CALLS_TOTAL,
    "mas_budget_exhausted": MAS_BUDGET_EXHAUSTED_TOTAL,
    "mas_dlq_depth": MAS_DLQ_DEPTH,
    "mas_project_state": MAS_PROJECT_STATE,
    "mas_review_circuit_open": MAS_REVIEW_CIRCUIT_OPEN,
    "mas_infra_lead_time": MAS_INFRA_LEAD_TIME,
    "mas_agent_correction_factor": MAS_AGENT_CORRECTION_FACTOR,
    "mas_tool_circuit_state": MAS_TOOL_CIRCUIT_STATE,
}

# ── Helpers ────────────────────────────────────────────────────────────────

_CIRCUIT_STATE_MAP = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}


def set_tool_circuit_state(tool_name: str, state: str) -> None:
    """Convenience: set the tool circuit-breaker gauge from a state string."""
    MAS_TOOL_CIRCUIT_STATE.labels(tool_name=tool_name).set(_CIRCUIT_STATE_MAP.get(state, -1))


def metric_series_budget_status(
    registry: CollectorRegistry = REGISTRY,
) -> dict[str, object]:
    """Return current custom metric series counts and budget violations.

    The helper is safe to call from a scrape path or a certification test. It
    counts only AIAT-owned metric families (``mas_*``); process/runtime and
    third-party collector families are outside this platform budget.
    """
    family_counts: dict[str, int] = {}
    for family in registry.collect():
        if not family.name.startswith("mas_"):
            continue
        family_counts[family.name] = len(family.samples)
    total = sum(family_counts.values())
    violations = []
    if total > METRIC_SERIES_BUDGET:
        violations.append(
            f"total custom series {total} exceeds budget {METRIC_SERIES_BUDGET}"
        )
    for name, budget in METRIC_FAMILY_SERIES_BUDGETS.items():
        count = family_counts.get(name, 0)
        if count > budget:
            violations.append(f"{name} has {count} series; budget is {budget}")
    return {
        "total": total,
        "family_counts": family_counts,
        "budget": METRIC_SERIES_BUDGET,
        "family_budgets": dict(METRIC_FAMILY_SERIES_BUDGETS),
        "violations": violations,
        "passed": not violations,
    }


def metric_label_inventory(
    registry: CollectorRegistry = REGISTRY,
) -> dict[str, tuple[str, ...]]:
    """Return labels for AIAT metric families for static cardinality review."""

    inventory: dict[str, tuple[str, ...]] = {}
    for family in registry.collect():
        if family.name.startswith("mas_"):
            labels: set[str] = set()
            for sample in family.samples:
                labels.update(sample.labels)
            inventory[family.name] = tuple(sorted(labels))
    return inventory


def metric_declared_label_inventory() -> dict[str, tuple[str, ...]]:
    """Return declared labels, including the generated histogram ``le`` label."""

    inventory: dict[str, tuple[str, ...]] = {}
    for family, collector in AIAT_METRIC_COLLECTORS.items():
        labels = set(getattr(collector, "_labelnames", ()) or ())
        if isinstance(collector, Histogram):
            labels.add("le")
        inventory[family] = tuple(sorted(str(label) for label in labels))
    return inventory


def metric_label_policy_inventory() -> dict[str, dict[str, dict[str, str]]]:
    """Return the declared classification for every AIAT metric label.

    This is a copy so callers can include the inventory in evidence reports
    without mutating the module-level contract.
    """

    return {
        family: {label: dict(policy) for label, policy in labels.items()}
        for family, labels in METRIC_LABEL_POLICIES.items()
    }


def assert_metric_series_budget(registry: CollectorRegistry = REGISTRY) -> dict[str, object]:
    """Fail a certification/check path when bounded metric budgets are exceeded."""
    status = metric_series_budget_status(registry)
    if not status["passed"]:
        raise RuntimeError("; ".join(str(item) for item in status["violations"]))
    return status
