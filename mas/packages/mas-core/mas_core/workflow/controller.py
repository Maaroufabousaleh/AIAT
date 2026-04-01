"""Deterministic workflow controller with optional DB persistence and event publishing.

Phase 0 provides pure in-memory validation (no side-effects).
Phase 4b adds:
  - Atomic state + history persistence via AgentStorage (SELECT ... FOR UPDATE)
  - Event publishing via a callback (router SYSTEM_EVENT publish)

When ``storage`` is None the controller works in pure-validation mode,
returning a result dataclass with no side effects. This preserves backward
compatibility with all existing tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from mas_core.workflow.events import WorkflowEvent
from mas_core.workflow.states import ProjectState
from mas_core.workflow.transitions import (
    RESTORE_LAST_SAFE_STATE,
    RESTORE_PRIOR_STATE,
    TransitionTarget,
    resolve_transition,
)

logger = logging.getLogger(__name__)

# Type alias for async event publisher callbacks.
# Signature: async def publish(project_id, from_state, to_state, event, actor_id, context) -> None
EventPublisher = Callable[
    [str, str, str, str, str, dict[str, Any]],
    Coroutine[Any, Any, None],
]


class InvalidTransitionError(ValueError):
    """Raised when ``(state, event)`` is not present in the transition table."""

    def __init__(self, state: ProjectState, event: WorkflowEvent) -> None:
        super().__init__(f"Invalid transition: state={state} event={event}")
        self.state = state
        self.event = event


@dataclass(slots=True, frozen=True)
class WorkflowTransitionResult:
    """Transition result — returned by both pure and persistent modes."""

    project_id: str
    prior_state: ProjectState
    event: WorkflowEvent
    next_state: TransitionTarget
    actor_id: str
    context: dict[str, Any]


class WorkflowController:
    """Deterministic transition validator and executor from section 11.2.

    Parameters
    ----------
    storage : AgentStorage | None
        When provided, ``transition()`` atomically persists state changes
        using ``SELECT ... FOR UPDATE`` and appends a history row.
    event_publisher : EventPublisher | None
        When provided, ``transition()`` calls this after persistence to
        publish a SYSTEM_EVENT notification (typically via the message router).
    """

    def __init__(
        self,
        storage: Any | None = None,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self._storage = storage
        self._event_publisher = event_publisher

    @property
    def storage(self) -> Any | None:
        return self._storage

    def can_transition(self, state: ProjectState, event: WorkflowEvent) -> bool:
        return resolve_transition(state, event) is not None

    def next_state(self, state: ProjectState, event: WorkflowEvent) -> TransitionTarget:
        target = resolve_transition(state, event)
        if target is None:
            raise InvalidTransitionError(state=state, event=event)
        return target

    def _resolve_special_target(
        self,
        target: TransitionTarget,
        *,
        prior_state: ProjectState,
        context: dict[str, Any],
    ) -> ProjectState:
        """Resolve _RESTORE_PRIOR_STATE and _RESTORE_LAST_SAFE_STATE tokens
        into concrete ProjectState values."""
        if target == RESTORE_PRIOR_STATE:
            # Use the failed_from_state stored in context or fall back to INIT.
            restored = context.get("failed_from_state") or context.get("prior_state")
            if restored:
                return ProjectState(restored)
            logger.warning(
                "RESTORE_PRIOR_STATE without prior state in context; falling back to INIT"
            )
            return ProjectState.INIT

        if target == RESTORE_LAST_SAFE_STATE:
            # Determine the last safe (non-failure) state.
            safe = context.get("last_safe_state") or context.get("failed_from_state")
            if safe:
                return ProjectState(safe)
            logger.warning(
                "RESTORE_LAST_SAFE_STATE without safe state in context; falling back to INIT"
            )
            return ProjectState.INIT

        # Already a concrete ProjectState.
        return ProjectState(target)

    async def transition(
        self,
        *,
        project_id: str,
        current_state: ProjectState,
        event: WorkflowEvent,
        actor_id: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowTransitionResult:
        """Execute a workflow transition.

        Pure mode (no storage):
            - Validates the transition deterministically.
            - Returns the result with no side effects.

        Persistent mode (storage provided):
            - Validates the transition.
            - Atomically updates project state and appends history via
              AgentStorage.transition_project() (uses SELECT ... FOR UPDATE).
            - Publishes a SYSTEM_EVENT if event_publisher is set.
        """
        ctx = context or {}

        # 1. Validate and resolve the target state.
        raw_target = self.next_state(current_state, event)
        concrete_target = self._resolve_special_target(
            raw_target,
            prior_state=current_state,
            context=ctx,
        )

        # 2. Persist atomically if storage is available.
        if self._storage is not None:
            pid = UUID(project_id) if isinstance(project_id, str) else project_id

            # Build failure fields for FAILED state transitions.
            failure_reason: str | None = None
            failed_from_state: str | None = None
            if concrete_target == ProjectState.FAILED:
                failure_reason = ctx.get("failure_reason", event.value)
                failed_from_state = str(current_state)

            updated = await self._storage.transition_project(
                pid,
                new_state=str(concrete_target),
                event=event.value,
                triggered_by=actor_id,
                payload=ctx if ctx else None,
                failure_reason=failure_reason,
                failed_from_state=failed_from_state,
                expected_state=str(current_state),
            )

            if updated is None:
                raise ValueError(
                    f"Project {project_id} not found or state has changed "
                    f"(expected {current_state}) — retry after re-reading"
                )

            logger.info(
                "Workflow transition persisted: project=%s %s -> %s (event=%s, actor=%s)",
                project_id,
                current_state,
                concrete_target,
                event,
                actor_id,
            )

        # 3. Publish SYSTEM_EVENT if publisher is available.
        if self._event_publisher is not None:
            try:
                await self._event_publisher(
                    project_id,
                    str(current_state),
                    str(concrete_target),
                    event.value,
                    actor_id,
                    ctx,
                )
            except Exception:
                logger.exception(
                    "Failed to publish SYSTEM_EVENT for project=%s transition %s -> %s",
                    project_id,
                    current_state,
                    concrete_target,
                )
                # Don't fail the transition if event publishing fails —
                # the state is already persisted.

        return WorkflowTransitionResult(
            project_id=project_id,
            prior_state=current_state,
            event=event,
            next_state=concrete_target,
            actor_id=actor_id,
            context=ctx,
        )
