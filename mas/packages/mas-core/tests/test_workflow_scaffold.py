from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mas_core.workflow import (
    InvalidTransitionError,
    ProjectState,
    WatchdogConfig,
    WorkflowController,
    WorkflowEvent,
    resolve_transition,
    should_watchdog_fire,
)
from mas_core.workflow.transitions import RESTORE_LAST_SAFE_STATE, RESTORE_PRIOR_STATE


def test_transition_table_matches_happy_path_edge_examples() -> None:
    assert (
        resolve_transition(ProjectState.INIT, WorkflowEvent.PROJECT_CREATED)
        == ProjectState.FEASIBILITY_CHECK
    )
    assert (
        resolve_transition(ProjectState.HUMAN_APPROVAL, WorkflowEvent.HUMAN_EDITS)
        == ProjectState.CDR_CREATION
    )
    assert (
        resolve_transition(ProjectState.FAILED, WorkflowEvent.RETRY)
        == RESTORE_LAST_SAFE_STATE
    )
    assert (
        resolve_transition(ProjectState.SECURITY_BLOCKED, WorkflowEvent.CEO_OVERRIDE)
        == RESTORE_PRIOR_STATE
    )


def test_wildcard_failure_event_is_allowed_from_any_state() -> None:
    assert (
        resolve_transition(ProjectState.PDR_CREATION, WorkflowEvent.WATCHDOG_TIMEOUT)
        == ProjectState.FAILED
    )


def test_controller_rejects_invalid_transition() -> None:
    controller = WorkflowController()

    try:
        controller.next_state(ProjectState.INIT, WorkflowEvent.KPI_SAVED)
    except InvalidTransitionError:
        return

    raise AssertionError("Expected InvalidTransitionError")


@pytest.mark.asyncio
async def test_security_override_restores_persisted_blocked_from_state() -> None:
    project_id = uuid4()
    storage = AsyncMock()
    storage.get_project.return_value = {
        "id": project_id,
        "state": "SECURITY_BLOCKED",
        "failed_from_state": "FEASIBILITY_CHECK",
    }
    storage.transition_project.return_value = {
        "id": project_id,
        "state": "FEASIBILITY_CHECK",
    }
    controller = WorkflowController(storage=storage)

    result = await controller.transition(
        project_id=str(project_id),
        current_state=ProjectState.SECURITY_BLOCKED,
        event=WorkflowEvent.CEO_OVERRIDE,
        actor_id="ceo",
    )

    assert result.next_state == ProjectState.FEASIBILITY_CHECK
    assert result.context["failed_from_state"] == "FEASIBILITY_CHECK"
    assert storage.transition_project.await_args.kwargs["new_state"] == "FEASIBILITY_CHECK"


def test_watchdog_uses_boot_grace_and_downtime_aware_elapsed() -> None:
    now = datetime(2026, 2, 27, 12, 0, tzinfo=UTC)
    boot_at = now - timedelta(seconds=120)
    stale_project = now - timedelta(hours=2)
    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=300)

    assert (
        should_watchdog_fire(
            now=now,
            project_updated_at=stale_project,
            boot_at=boot_at,
            config=config,
        )
        is False
    )

    post_grace_now = now + timedelta(seconds=240)
    assert (
        should_watchdog_fire(
            now=post_grace_now,
            project_updated_at=stale_project,
            boot_at=boot_at,
            config=config,
        )
        is False
    )

    timeout_now = boot_at + timedelta(seconds=3600 + 301)
    assert (
        should_watchdog_fire(
            now=timeout_now,
            project_updated_at=stale_project,
            boot_at=boot_at,
            config=config,
        )
        is True
    )
