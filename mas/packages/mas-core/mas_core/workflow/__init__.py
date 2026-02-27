"""Deterministic workflow scaffold from org architecture section 11.2."""

from mas_core.workflow.controller import (
    InvalidTransitionError,
    WorkflowController,
    WorkflowTransitionResult,
)
from mas_core.workflow.events import WorkflowEvent
from mas_core.workflow.states import ProjectState
from mas_core.workflow.transitions import (
    RESTORE_LAST_SAFE_STATE,
    RESTORE_PRIOR_STATE,
    TRANSITIONS,
    TransitionTarget,
    is_terminal_state,
    resolve_transition,
)
from mas_core.workflow.watchdog import (
    WatchdogConfig,
    should_watchdog_fire,
    watchdog_elapsed_seconds,
)

__all__ = [
    "InvalidTransitionError",
    "ProjectState",
    "RESTORE_LAST_SAFE_STATE",
    "RESTORE_PRIOR_STATE",
    "TRANSITIONS",
    "TransitionTarget",
    "WatchdogConfig",
    "WorkflowController",
    "WorkflowEvent",
    "WorkflowTransitionResult",
    "is_terminal_state",
    "resolve_transition",
    "should_watchdog_fire",
    "watchdog_elapsed_seconds",
]
