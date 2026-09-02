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
    InvalidTransitionError,
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


async def _controller_checks() -> dict[str, Any]:
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
    cancellation_result = await controller.transition(
        project_id="fixture-project",
        current_state=ProjectState.HUMAN_APPROVAL,
        event=WorkflowEvent.HUMAN_CANCELLED,
        actor_id="operator",
        context={"reason": "fixture cancellation"},
    )
    escalation_result = await controller.transition(
        project_id="fixture-project",
        current_state=ProjectState.PDR_REVIEW,
        event=WorkflowEvent.REVIEW_CIRCUIT_OPEN,
        actor_id="review-circuit",
        context={"reason": "fixture review timeout escalation"},
    )
    timeout_result = await controller.transition(
        project_id="fixture-project",
        current_state=ProjectState.CDR_CREATION,
        event=WorkflowEvent.WATCHDOG_TIMEOUT,
        actor_id="watchdog",
        context={"reason": "fixture node timeout"},
    )
    try:
        controller.next_state(ProjectState.INIT, WorkflowEvent.KPI_SAVED)
    except InvalidTransitionError:
        invalid_transition_blocked = True
    else:
        invalid_transition_blocked = False

    return {
        "watchdog": {
            "prior_state": str(watchdog_result.prior_state),
            "next_state": str(watchdog_result.next_state),
            "event": watchdog_result.event.value,
        },
        "retry": {
            "prior_state": str(retry_result.prior_state),
            "next_state": str(retry_result.next_state),
            "event": retry_result.event.value,
        },
        "cancellation": {
            "prior_state": str(cancellation_result.prior_state),
            "next_state": str(cancellation_result.next_state),
            "event": cancellation_result.event.value,
        },
        "escalation": {
            "prior_state": str(escalation_result.prior_state),
            "next_state": str(escalation_result.next_state),
            "event": escalation_result.event.value,
        },
        "timeout": {
            "prior_state": str(timeout_result.prior_state),
            "next_state": str(timeout_result.next_state),
            "event": timeout_result.event.value,
        },
        "invalid_transition_blocked": invalid_transition_blocked,
    }


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
    controller_results = asyncio.run(_controller_checks())
    watchdog_result = controller_results["watchdog"]
    retry_result = controller_results["retry"]

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
        _check(
            "explicit_human_cancellation",
            controller_results["cancellation"]
            == {
                "prior_state": ProjectState.HUMAN_APPROVAL.value,
                "next_state": ProjectState.ARCHIVED.value,
                "event": WorkflowEvent.HUMAN_CANCELLED.value,
            },
            "human cancellation reaches the terminal archived state through the controller",
        ),
        _check(
            "review_circuit_escalation",
            controller_results["escalation"]
            == {
                "prior_state": ProjectState.PDR_REVIEW.value,
                "next_state": ProjectState.FAILED.value,
                "event": WorkflowEvent.REVIEW_CIRCUIT_OPEN.value,
            },
            "review-circuit escalation uses the universal failure transition",
        ),
        _check(
            "node_timeout_failure",
            controller_results["timeout"]
            == {
                "prior_state": ProjectState.CDR_CREATION.value,
                "next_state": ProjectState.FAILED.value,
                "event": WorkflowEvent.WATCHDOG_TIMEOUT.value,
            },
            "node timeout reaches failure without guessing a recovery state",
        ),
        _check(
            "invalid_transition_fails_closed",
            controller_results["invalid_transition_blocked"] is True,
            "events not present in the transition table are rejected",
        ),
    ]
    status = "pass" if all(item["status"] == "pass" for item in checks) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "fixture",
        "status": status,
        "checks": checks,
        "transition_cases": controller_results,
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
