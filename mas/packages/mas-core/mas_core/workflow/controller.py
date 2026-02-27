from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mas_core.workflow.events import WorkflowEvent
from mas_core.workflow.states import ProjectState
from mas_core.workflow.transitions import TransitionTarget, resolve_transition


class InvalidTransitionError(ValueError):
    """Raised when ``(state, event)`` is not present in the transition table."""

    def __init__(self, state: ProjectState, event: WorkflowEvent) -> None:
        super().__init__(f"Invalid transition: state={state} event={event}")
        self.state = state
        self.event = event


@dataclass(slots=True, frozen=True)
class WorkflowTransitionResult:
    """Pure transition result. Database and publish side-effects come later."""

    project_id: str
    prior_state: ProjectState
    event: WorkflowEvent
    next_state: TransitionTarget
    actor_id: str
    context: dict[str, Any]


class WorkflowController:
    """Deterministic transition validator scaffold from section 11.2."""

    def can_transition(self, state: ProjectState, event: WorkflowEvent) -> bool:
        return resolve_transition(state, event) is not None

    def next_state(self, state: ProjectState, event: WorkflowEvent) -> TransitionTarget:
        target = resolve_transition(state, event)
        if target is None:
            raise InvalidTransitionError(state=state, event=event)
        return target

    async def transition(
        self,
        *,
        project_id: str,
        current_state: ProjectState,
        event: WorkflowEvent,
        actor_id: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowTransitionResult:
        """
        Phase 0 scaffold:
        - deterministic validation and transition target resolution
        - side-effect free

        Phase 4b/10 will add:
        - SELECT .. FOR UPDATE
        - atomic state + history persistence
        - router SYSTEM_EVENT publish
        """

        return WorkflowTransitionResult(
            project_id=project_id,
            prior_state=current_state,
            event=event,
            next_state=self.next_state(current_state, event),
            actor_id=actor_id,
            context=context or {},
        )
