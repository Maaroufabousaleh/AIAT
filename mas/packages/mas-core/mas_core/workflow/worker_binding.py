"""Flow-task bindings to authoritative universal Worker Runs.

The flow API may dispatch a run synchronously or return before a queued run
has finished.  This module keeps that distinction deterministic: only a
terminal Worker Run can settle a governed task; every known non-terminal state
keeps the task active and records the run ID in flow context.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

WORKER_RUN_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"})
WORKER_RUN_NONTERMINAL_STATES = frozenset(
    {
        "CREATED",
        "QUEUED",
        "CLAIMED",
        "VALIDATING",
        "READY",
        "DISPATCHING",
        "RUNNING",
        "PAUSING",
        "PAUSED",
        "RESUMING",
    }
)

WorkerRunStateClass = Literal["succeeded", "failed", "pending", "unknown"]


def normalize_worker_run_state(state: Any) -> str:
    """Return the canonical upper-case state used by persisted run records."""

    return str(state or "").strip().upper()


def classify_worker_run_state(state: Any) -> WorkerRunStateClass:
    """Classify a dispatch result without treating asynchronous progress as failure."""

    normalized = normalize_worker_run_state(state)
    if normalized == "SUCCEEDED":
        return "succeeded"
    if normalized in WORKER_RUN_TERMINAL_STATES:
        return "failed"
    if normalized in WORKER_RUN_NONTERMINAL_STATES:
        return "pending"
    return "unknown"


def bind_pending_worker_run(
    context: Mapping[str, Any],
    *,
    node_id: str,
    run_id: str,
    state: Any,
    dispatch_mode: str | None = None,
) -> dict[str, Any]:
    """Return a copy of ``context`` with one active node/run binding recorded."""

    normalized_state = normalize_worker_run_state(state)
    if classify_worker_run_state(normalized_state) != "pending":
        raise ValueError("only a known non-terminal Worker Run can remain active")
    normalized_run_id = str(run_id).strip()
    if not normalized_run_id:
        raise ValueError("pending Worker Run binding requires a run ID")

    result = deepcopy(dict(context))
    active_worker_runs = dict(result.get("active_worker_runs") or {})
    binding: dict[str, Any] = {"run_id": normalized_run_id, "state": normalized_state}
    if dispatch_mode is not None:
        binding["dispatch_mode"] = str(dispatch_mode)
    active_worker_runs[str(node_id)] = binding
    result["active_worker_runs"] = active_worker_runs
    return result


def clear_worker_run_binding(
    context: Mapping[str, Any],
    *,
    node_id: str,
) -> dict[str, Any]:
    """Return a copy of ``context`` with one settled node/run binding removed."""

    result = deepcopy(dict(context))
    active_worker_runs = dict(result.get("active_worker_runs") or {})
    active_worker_runs.pop(str(node_id), None)
    if active_worker_runs:
        result["active_worker_runs"] = active_worker_runs
    else:
        result.pop("active_worker_runs", None)
    return result
