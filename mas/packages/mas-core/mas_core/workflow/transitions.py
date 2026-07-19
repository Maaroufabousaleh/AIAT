from typing import Literal, TypeAlias

from mas_core.workflow.events import WorkflowEvent
from mas_core.workflow.states import ProjectState

RESTORE_PRIOR_STATE = "_RESTORE_PRIOR_STATE"
RESTORE_LAST_SAFE_STATE = "_RESTORE_LAST_SAFE_STATE"

TransitionTarget: TypeAlias = ProjectState | Literal["_RESTORE_PRIOR_STATE", "_RESTORE_LAST_SAFE_STATE"]

TRANSITIONS: dict[ProjectState, dict[WorkflowEvent, TransitionTarget]] = {
    ProjectState.INIT: {
        WorkflowEvent.PROJECT_CREATED: ProjectState.FEASIBILITY_CHECK,
    },
    ProjectState.FEASIBILITY_CHECK: {
        WorkflowEvent.ALL_REVIEWS_IN: ProjectState.FEASIBILITY_REPORT,
        WorkflowEvent.CSO_VETO: ProjectState.SECURITY_BLOCKED,
    },
    ProjectState.FEASIBILITY_REPORT: {
        WorkflowEvent.HUMAN_APPROVED: ProjectState.PDR_CREATION,
        WorkflowEvent.HUMAN_REJECTED: ProjectState.ARCHIVED,
    },
    ProjectState.PDR_CREATION: {
        WorkflowEvent.PDR_SUBMITTED: ProjectState.PDR_REVIEW,
    },
    ProjectState.PDR_REVIEW: {
        WorkflowEvent.ALL_REVIEWS_IN: ProjectState.CDR_CREATION,
        WorkflowEvent.PDR_REVISION_REQUESTED: ProjectState.PDR_CREATION,
        WorkflowEvent.CSO_VETO: ProjectState.SECURITY_BLOCKED,
    },
    ProjectState.SECURITY_BLOCKED: {
        WorkflowEvent.BLOCKER_RESOLVED: RESTORE_PRIOR_STATE,
        WorkflowEvent.CEO_OVERRIDE: RESTORE_PRIOR_STATE,
    },
    ProjectState.CDR_CREATION: {
        WorkflowEvent.CDR_SUBMITTED: ProjectState.CDR_REVIEW,
    },
    ProjectState.CDR_REVIEW: {
        WorkflowEvent.CDR_PRESENTED: ProjectState.HUMAN_APPROVAL,
        WorkflowEvent.CDR_REVISION_REQUESTED: ProjectState.CDR_CREATION,
        WorkflowEvent.CSO_VETO: ProjectState.SECURITY_BLOCKED,
    },
    ProjectState.HUMAN_APPROVAL: {
        WorkflowEvent.HUMAN_APPROVED: ProjectState.RR_CREATION,
        WorkflowEvent.HUMAN_EDITS: ProjectState.CDR_CREATION,
        WorkflowEvent.HUMAN_CANCELLED: ProjectState.ARCHIVED,
    },
    ProjectState.RR_CREATION: {
        WorkflowEvent.RR_SUBMITTED: ProjectState.SPRINT_PLANNING,
    },
    ProjectState.SPRINT_PLANNING: {
        WorkflowEvent.SPRINTS_CREATED: ProjectState.INFRA_PROVISIONING,
    },
    ProjectState.INFRA_PROVISIONING: {
        WorkflowEvent.INFRA_READY: ProjectState.IN_PROGRESS,
        WorkflowEvent.INFRA_FAILED: ProjectState.FAILED,
    },
    ProjectState.IN_PROGRESS: {
        WorkflowEvent.ALL_SPRINTS_DONE: ProjectState.RETROSPECTIVE,
    },
    ProjectState.RETROSPECTIVE: {
        WorkflowEvent.RETROSPECTIVE_DONE: ProjectState.KPI_PERSISTENCE,
    },
    ProjectState.KPI_PERSISTENCE: {
        WorkflowEvent.KPI_SAVED: ProjectState.COMPLETED,
    },
    ProjectState.COMPLETED: {
        WorkflowEvent.ARCHIVE_REQUESTED: ProjectState.ARCHIVED,
    },
    ProjectState.FAILED: {
        WorkflowEvent.RETRY: RESTORE_LAST_SAFE_STATE,
        WorkflowEvent.ARCHIVE_REQUESTED: ProjectState.ARCHIVED,
    },
    ProjectState.ARCHIVED: {},
}

WILDCARD_TRANSITIONS: dict[WorkflowEvent, TransitionTarget] = {
    WorkflowEvent.WATCHDOG_TIMEOUT: ProjectState.FAILED,
    WorkflowEvent.REVIEW_CIRCUIT_OPEN: ProjectState.FAILED,
    WorkflowEvent.UNRECOVERABLE_ERROR: ProjectState.FAILED,
}


def resolve_transition(current_state: ProjectState, event: WorkflowEvent) -> TransitionTarget | None:
    """Return next state token for a valid transition, else ``None``."""

    state_map = TRANSITIONS.get(current_state, {})
    if event in state_map:
        return state_map[event]
    return WILDCARD_TRANSITIONS.get(event)


def is_terminal_state(state: ProjectState) -> bool:
    """Terminal project states."""

    # FAILED is terminal for automatic resume.  It has an explicit manual
    # RETRY transition, so startup/system resume must not silently re-run it.
    return state in {ProjectState.COMPLETED, ProjectState.ARCHIVED, ProjectState.FAILED}
