"""
Tests for the watchdog background loop and schedule-aware logic.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mas_core.workflow import (
    InvalidTransitionError,
    WatchdogConfig,
    WorkflowEvent,
    should_watchdog_fire,
    watchdog_elapsed_seconds,
)
from mas_core.workflow.states import ProjectState


# ── should_watchdog_fire ─────────────────────────────────────────────────────


def test_fire_when_elapsed_exceeds_timeout():
    """Project updated 2 hours ago → should fire."""
    now = datetime.now(tz=UTC)
    updated = now - timedelta(hours=2)
    boot = now - timedelta(hours=3)
    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=300)
    assert should_watchdog_fire(now=now, project_updated_at=updated, boot_at=boot, config=config)


def test_no_fire_within_grace_period():
    """Boot happened 60 s ago → grace period active → no fire."""
    now = datetime.now(tz=UTC)
    updated = now - timedelta(hours=2)
    boot = now - timedelta(seconds=60)
    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=300)
    assert not should_watchdog_fire(
        now=now, project_updated_at=updated, boot_at=boot, config=config
    )


def test_no_fire_when_recent():
    """Project updated 10 min ago → no fire."""
    now = datetime.now(tz=UTC)
    updated = now - timedelta(minutes=10)
    boot = now - timedelta(hours=1)
    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=300)
    assert not should_watchdog_fire(
        now=now, project_updated_at=updated, boot_at=boot, config=config
    )


def test_fire_after_grace_expired():
    """Boot 70 min ago (grace expired), project updated 2 hours ago → fire."""
    now = datetime.now(tz=UTC)
    updated = now - timedelta(hours=2)
    boot = now - timedelta(minutes=70)
    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=300)
    assert should_watchdog_fire(now=now, project_updated_at=updated, boot_at=boot, config=config)


def test_watchdog_elapsed_uses_max_updated_boot():
    """Elapsed should be computed from max(updated_at, boot_at)."""
    now = datetime.now(tz=UTC)
    updated = now - timedelta(hours=2)
    boot = now - timedelta(hours=1)
    elapsed = watchdog_elapsed_seconds(now=now, project_updated_at=updated, boot_at=boot)
    # Should measure from boot_at (1 hour ago), not updated_at (2 hours ago)
    assert elapsed == pytest.approx(3600, abs=1)


def test_watchdog_elapsed_without_boot():
    """When boot_at is None, elapsed is from updated_at."""
    now = datetime.now(tz=UTC)
    updated = now - timedelta(hours=2)
    elapsed = watchdog_elapsed_seconds(now=now, project_updated_at=updated, boot_at=None)
    assert elapsed == pytest.approx(7200, abs=1)


def test_watchdog_elapsed_zero_when_updated_after_boot():
    """If updated_at > boot_at, elapsed should be from updated_at."""
    now = datetime.now(tz=UTC)
    updated = now - timedelta(minutes=5)
    boot = now - timedelta(hours=1)
    elapsed = watchdog_elapsed_seconds(now=now, project_updated_at=updated, boot_at=boot)
    assert elapsed == pytest.approx(300, abs=1)


# ── watchdog_loop ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_watchdog_fires_for_stuck_project():
    """Watchdog should transition a stuck project to FAILED."""
    storage = MagicMock()
    storage.get_config = AsyncMock(return_value="RUNNING")
    storage.list_projects = AsyncMock(
        return_value=[
            {
                "id": "proj-1",
                "state": "IN_PROGRESS",
                "updated_at": datetime.now(tz=UTC) - timedelta(hours=2),
            }
        ]
    )

    controller = MagicMock()
    controller.transition = AsyncMock()

    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=60)
    boot_at = datetime.now(tz=UTC) - timedelta(hours=3)
    stop_event = asyncio.Event()

    from orchestrator_api.main import watchdog_loop

    with patch("orchestrator_api.main.WATCHDOG_INTERVAL_S", 0):
        await watchdog_loop(storage, controller, config, boot_at, stop_event, max_iterations=1)

    # Verify at least one transition was attempted for the stuck project
    controller.transition.assert_called()
    call_kwargs = controller.transition.call_args.kwargs
    assert call_kwargs["event"] == WorkflowEvent.WATCHDOG_TIMEOUT
    assert call_kwargs["actor_id"] == "watchdog"


@pytest.mark.anyio
async def test_watchdog_skips_recent_project():
    """Watchdog should not fire for a recently updated project."""
    storage = MagicMock()
    storage.get_config = AsyncMock(return_value="RUNNING")
    storage.list_projects = AsyncMock(
        return_value=[
            {
                "id": "proj-1",
                "state": "IN_PROGRESS",
                "updated_at": datetime.now(tz=UTC) - timedelta(minutes=5),
            }
        ]
    )

    controller = MagicMock()
    controller.transition = AsyncMock()

    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=60)
    boot_at = datetime.now(tz=UTC) - timedelta(hours=1)
    stop_event = asyncio.Event()

    from orchestrator_api.main import watchdog_loop

    with patch("orchestrator_api.main.WATCHDOG_INTERVAL_S", 0):
        await watchdog_loop(storage, controller, config, boot_at, stop_event, max_iterations=1)

    controller.transition.assert_not_called()


@pytest.mark.anyio
async def test_watchdog_skips_terminal_projects():
    """Watchdog should skip COMPLETED and ARCHIVED projects (terminal states)."""
    storage = MagicMock()
    storage.get_config = AsyncMock(return_value="RUNNING")
    storage.list_projects = AsyncMock(
        return_value=[
            {
                "id": "proj-1",
                "state": "COMPLETED",
                "updated_at": datetime.now(tz=UTC) - timedelta(hours=2),
            },
            {
                "id": "proj-2",
                "state": "ARCHIVED",
                "updated_at": datetime.now(tz=UTC) - timedelta(hours=2),
            },
        ]
    )

    controller = MagicMock()
    controller.transition = AsyncMock()

    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=60)
    boot_at = datetime.now(tz=UTC) - timedelta(hours=3)
    stop_event = asyncio.Event()

    from orchestrator_api.main import watchdog_loop

    with patch("orchestrator_api.main.WATCHDOG_INTERVAL_S", 0):
        await watchdog_loop(storage, controller, config, boot_at, stop_event, max_iterations=1)

    controller.transition.assert_not_called()


@pytest.mark.anyio
async def test_watchdog_skips_when_not_running():
    """Watchdog should skip checks when system_state != RUNNING."""
    storage = MagicMock()
    storage.get_config = AsyncMock(return_value="SHUTTING_DOWN")
    storage.list_projects = AsyncMock(
        return_value=[
            {
                "id": "proj-1",
                "state": "IN_PROGRESS",
                "updated_at": datetime.now(tz=UTC) - timedelta(hours=2),
            }
        ]
    )

    controller = MagicMock()
    controller.transition = AsyncMock()

    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=60)
    boot_at = datetime.now(tz=UTC) - timedelta(hours=3)
    stop_event = asyncio.Event()

    from orchestrator_api.main import watchdog_loop

    with patch("orchestrator_api.main.WATCHDOG_INTERVAL_S", 0):
        await watchdog_loop(storage, controller, config, boot_at, stop_event, max_iterations=1)

    controller.transition.assert_not_called()


@pytest.mark.anyio
async def test_watchdog_handles_invalid_transition():
    """Watchdog should gracefully handle InvalidTransitionError."""
    storage = MagicMock()
    storage.get_config = AsyncMock(return_value="RUNNING")
    storage.list_projects = AsyncMock(
        return_value=[
            {
                "id": "proj-1",
                "state": "IN_PROGRESS",
                "updated_at": datetime.now(tz=UTC) - timedelta(hours=2),
            }
        ]
    )

    controller = MagicMock()
    controller.transition = AsyncMock(
        side_effect=InvalidTransitionError(ProjectState.IN_PROGRESS, WorkflowEvent.WATCHDOG_TIMEOUT)
    )

    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=60)
    boot_at = datetime.now(tz=UTC) - timedelta(hours=3)
    stop_event = asyncio.Event()

    from orchestrator_api.main import watchdog_loop

    # Should not raise
    with patch("orchestrator_api.main.WATCHDOG_INTERVAL_S", 0):
        await watchdog_loop(storage, controller, config, boot_at, stop_event, max_iterations=1)
