"""Verify deterministic watchdog timeout and safe-retry semantics.

The fixture drives the real schedule-aware watchdog helpers and the pure
workflow controller without storage, workers, network calls, or state
mutation.  It proves boot grace, downtime-aware elapsed time, watchdog failure
transition, restoration to a recorded safe state, and terminal-state
exclusion.  Resource licence/restriction metadata is not a predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from mas_core.workflow import (
    WatchdogConfig,
    WorkflowController,
    WorkflowEvent,
    is_terminal_state,
    should_watchdog_fire,
)
from mas_core.workflow.states import ProjectState

SCHEMA_VERSION = "aiat.workflow-watchdog-recovery.v1"


def _check(name: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "reason": reason}


async def _controller_checks() -> tuple[dict[str, Any], dict[str, Any]]:
    controller = WorkflowController()
    watchdog_result = await controller.transition(
        project_id="fixture-project",
        current_state=ProjectState.IN_PROGRESS,
        event=WorkflowEvent.WATCHDOG_TIMEOUT,
        actor_id="watchdog",
        context={"reason": "fixture timeout"},
    )
    retry_result = await controller.transition(
        project_id="fixture-project",
        current_state=ProjectState.FAILED,
        event=WorkflowEvent.RETRY,
        actor_id="operator",
        context={"last_safe_state": ProjectState.IN_PROGRESS.value},
    )
    return (
        {
            "prior_state": str(watchdog_result.prior_state),
            "next_state": str(watchdog_result.next_state),
            "event": watchdog_result.event.value,
        },
        {
            "prior_state": str(retry_result.prior_state),
            "next_state": str(retry_result.next_state),
            "event": retry_result.event.value,
        },
    )


def build_report() -> dict[str, Any]:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    boot_at = now - timedelta(seconds=120)
    stale_project = now - timedelta(hours=2)
    config = WatchdogConfig(timeout_seconds=3600, grace_seconds_after_boot=300)

    during_grace = should_watchdog_fire(
        now=now,
        project_updated_at=stale_project,
        boot_at=boot_at,
        config=config,
    )
    after_grace_before_timeout = should_watchdog_fire(
        now=boot_at + timedelta(seconds=3599),
        project_updated_at=stale_project,
        boot_at=boot_at,
        config=config,
    )
    at_timeout = should_watchdog_fire(
        now=boot_at + timedelta(seconds=3600 + 301),
        project_updated_at=stale_project,
        boot_at=boot_at,
        config=config,
    )
    watchdog_result, retry_result = asyncio.run(_controller_checks())

    checks = [
        _check(
            "boot_grace_suppresses_timeout",
            during_grace is False,
            "stale projects are not timed out during the post-boot grace window",
        ),
        _check(
            "downtime_aware_elapsed_time",
            after_grace_before_timeout is False and at_timeout is True,
            "elapsed time starts at max(project.updated_at, boot_at) and fires at the configured boundary",
        ),
        _check(
            "watchdog_transitions_to_failed",
            watchdog_result == {
                "prior_state": ProjectState.IN_PROGRESS.value,
                "next_state": ProjectState.FAILED.value,
                "event": WorkflowEvent.WATCHDOG_TIMEOUT.value,
            },
            "watchdog timeout uses the universal workflow failure transition",
        ),
        _check(
            "retry_restores_last_safe_state",
            retry_result == {
                "prior_state": ProjectState.FAILED.value,
                "next_state": ProjectState.IN_PROGRESS.value,
                "event": WorkflowEvent.RETRY.value,
            },
            "explicit retry restores the recorded safe state rather than guessing a new stage",
        ),
        _check(
            "terminal_states_are_excluded",
            all(is_terminal_state(state) for state in (ProjectState.FAILED, ProjectState.COMPLETED, ProjectState.ARCHIVED)),
            "terminal project states are not automatically re-entered by the watchdog",
        ),
    ]
    status = "pass" if all(item["status"] == "pass" for item in checks) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "fixture",
        "status": status,
        "checks": checks,
        "controller": {"storage": False, "worker_dispatch": False, "mutation": False},
        "live": {"status": "not_checked", "reason": "native watchdog and cold-recovery proof remains an operator gate"},
        "licence_metadata": {
            "recorded": False,
            "affects_discovery_install_activation_or_execution": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the complete report as JSON")
    parser.add_argument("--live", action="store_true", help="reserved; native live evidence is not claimed")
    args = parser.parse_args(argv)
    if args.live:
        report = {
            "schema_version": SCHEMA_VERSION,
            "mode": "live",
            "status": "blocked",
            "reason": "native watchdog/cold-recovery evidence requires an explicitly selected operator window",
            "controller": {"storage": False, "worker_dispatch": False, "mutation": False},
            "licence_metadata": {
                "recorded": False,
                "affects_discovery_install_activation_or_execution": False,
            },
        }
    else:
        report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"workflow watchdog/recovery: {report['status']}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    sys.exit(main())
