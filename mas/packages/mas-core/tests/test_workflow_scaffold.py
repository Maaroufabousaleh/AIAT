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


def test_evidence_policy_checks_required_artifact_kinds() -> None:
    from mas_core.workflow import EvidencePolicy, evaluate_project_evidence

    result = evaluate_project_evidence(
        project_id="project-1",
        policy=EvidencePolicy(
            policy_id="release",
            version="1.0",
            requires_artifacts=True,
            required_artifact_kinds=("test-report", "coverage"),
            requires_approvals_closed=False,
            requires_audit=False,
        ),
        project={"id": "project-1"},
        artifacts=[{"id": "artifact-1", "kind": "test-report"}],
    )

    check = next(item for item in result.checks if item.name == "required_artifact_kinds")
    assert check.passed is False
    assert "coverage" in (check.reason or "")


def test_evidence_package_groups_sources_without_turning_notices_into_gates() -> None:
    from mas_core.workflow import (
        EvidencePolicy,
        build_evidence_package,
        evaluate_project_evidence,
    )

    policy = EvidencePolicy(
        policy_id="release",
        version="1.0",
        requires_artifacts=True,
        required_artifact_kinds=("security-scan", "deployment"),
        requires_repository=True,
        requires_approvals_closed=False,
        requires_audit=False,
    )
    artifacts = [
        {
            "id": "artifact-security",
            "kind": "security-scan",
            "sha256": "abc",
            "size_bytes": 12,
            "path": "reports/security.json",
            "metadata": {"license": "restricted-for-commercial-use"},
        },
        {"id": "artifact-deploy", "kind": "deployment", "path": "deploy.json"},
    ]
    completeness = evaluate_project_evidence(
        project_id="project-1",
        policy=policy,
        project={"id": "project-1"},
        artifacts=artifacts,
        repository={"id": "repo-1", "initialized": True, "adapter_health": "ok"},
    )

    package = build_evidence_package(
        completeness=completeness,
        policy=policy,
        artifacts=artifacts,
        repository={"id": "repo-1", "initialized": True, "adapter_health": "ok"},
        generated_at="2026-08-10T00:00:00+00:00",
    )

    assert package.schema_version == "aiat.project-evidence-package.v1"
    assert package.status == "complete"
    assert {item.category for item in package.items} >= {"security", "deployment", "repository"}
    security = next(item for item in package.categories if item.category == "security")
    deployment = next(item for item in package.categories if item.category == "deployment")
    assert security.status == "present"
    assert security.required is True
    assert deployment.required is True
    assert package.notices == [
        {
            "artifact_id": "artifact-security",
            "field": "license",
            "value": "restricted-for-commercial-use",
        }
    ]


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
    assert (
        resolve_transition(ProjectState.CDR_REVIEW, WorkflowEvent.CDR_REVISION_REQUESTED)
        == ProjectState.CDR_CREATION
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
