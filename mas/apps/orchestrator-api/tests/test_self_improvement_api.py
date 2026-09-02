"""Authenticated canonical API coverage for guarded self-improvement projects."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from conftest import PROJECT_ID, _fake_project


def _patch_storage(storage) -> None:  # noqa: ANN001
    from orchestrator_api.main import app

    app.state.storage = storage


def _make_storage():  # noqa: ANN202
    storage = MagicMock()
    storage.create_self_improvement_project = AsyncMock(
        return_value=_fake_project("INIT", name="Improvement: Reduce flaky tests")
    )
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=None)
    storage.update_self_improvement_lifecycle = AsyncMock(return_value=None)
    return storage


def _payload(**overrides):  # noqa: ANN003, ANN202
    payload = {
        "title": "Reduce flaky tests",
        "description": "Improve retry diagnostics for the test worker.",
        "owner": "service",
        "owner_kind": "system",
        "risk": "medium",
        "budget_usd": "12.50",
        "evidence_policy": "software_delivery",
        "source": "operator_goal",
        "created_by": "service",
        "created_by_kind": "system",
        "licence_metadata": {
            "license": "AGPL-3.0",
            "use_restriction": "personal-only metadata",
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.anyio
async def test_create_self_improvement_project_uses_authenticated_principal(client):
    storage = _make_storage()
    _patch_storage(storage)

    response = await client.post("/projects/self-improvement", json=_payload())

    assert response.status_code == 201
    assert response.json()["name"] == "Improvement: Reduce flaky tests"
    storage.create_self_improvement_project.assert_awaited_once()
    opportunity = storage.create_self_improvement_project.await_args.args[0]
    assert opportunity.title == "Reduce flaky tests"
    assert opportunity.licence_metadata["license"] == "AGPL-3.0"


@pytest.mark.anyio
async def test_create_self_improvement_project_rejects_forged_creator(client):
    storage = _make_storage()
    _patch_storage(storage)

    response = await client.post(
        "/projects/self-improvement",
        json=_payload(created_by="agent-pretending-to-be-service"),
    )

    assert response.status_code == 403
    storage.create_self_improvement_project.assert_not_awaited()


@pytest.mark.anyio
async def test_create_self_improvement_project_requires_storage(client):
    _patch_storage(None)

    response = await client.post("/projects/self-improvement", json=_payload())

    assert response.status_code == 503


@pytest.mark.anyio
async def test_create_self_improvement_project_validates_typed_request(client):
    _patch_storage(_make_storage())

    response = await client.post(
        "/projects/self-improvement",
        json=_payload(title=""),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_create_self_improvement_project_checks_company_scope(client):
    storage = _make_storage()
    storage.get_company = AsyncMock(return_value=None)
    _patch_storage(storage)

    response = await client.post("/projects/self-improvement", json=_payload())

    assert response.status_code == 404
    storage.create_self_improvement_project.assert_not_awaited()


def _lifecycle_snapshot():  # noqa: ANN202
    from mas_core.workflow import ImprovementOpportunity, ImprovementRisk, SelfImprovementLifecycle

    opportunity = ImprovementOpportunity(
        title="Link evidence",
        description="Attach canonical evidence references.",
        owner="service",
        owner_kind="system",
        risk=ImprovementRisk.LOW,
        budget_usd="1.00",
        evidence_policy="software_delivery",
        source="test",
        created_by="service",
        created_by_kind="system",
    )
    lifecycle = SelfImprovementLifecycle.create(opportunity)
    lifecycle.bind_project(PROJECT_ID, actor="service", actor_kind="system")
    return lifecycle.as_dict()


def _promotion_pending_snapshot():  # noqa: ANN202
    from mas_core.workflow import GateName, SelfImprovementLifecycle

    lifecycle = SelfImprovementLifecycle.from_dict(_lifecycle_snapshot())
    for gate in (
        GateName.CODING,
        GateName.TESTING,
        GateName.REVIEW,
        GateName.SECURITY,
        GateName.MIGRATION,
        GateName.ROLLBACK,
    ):
        lifecycle.record_gate(
            gate,
            passed=True,
            actor="worker",
            actor_kind="agent",
            evidence_refs=(f"evidence/{gate.value}",),
        )
    lifecycle.start_shadow(candidate_version="v2", actor="service", actor_kind="system")
    lifecycle.record_observation(stage="shadow", sample_count=2, regression_fraction=0.0)
    lifecycle.start_canary(actor="service", actor_kind="system")
    lifecycle.record_observation(stage="canary", sample_count=2, regression_fraction=0.0)
    lifecycle.request_promotion(actor="service", actor_kind="system")
    lifecycle.record_gate(
        GateName.HUMAN_APPROVAL,
        passed=True,
        actor="operator",
        actor_kind="human",
        evidence_refs=("evidence/approval",),
    )
    return lifecycle.as_dict()


def _rolled_back_snapshot():  # noqa: ANN202
    from mas_core.workflow import SelfImprovementLifecycle

    lifecycle = SelfImprovementLifecycle.from_dict(_promotion_pending_snapshot())
    lifecycle.approve_promotion(actor="operator", actor_kind="human")
    lifecycle.rollback(actor="operator", actor_kind="human", reason="restore prior version")
    return lifecycle.as_dict()


def _artifact_bundle_payload():  # noqa: ANN202
    from mas_core.workflow import (
        ImprovementArtifact,
        ImprovementArtifactBundle,
        ImprovementArtifactKind,
    )

    bundle = ImprovementArtifactBundle(
        bundle_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        candidate_version="candidate-v2",
        generated_by="service",
        generated_by_kind="system",
        artifacts=tuple(
            ImprovementArtifact(
                artifact_id=UUID(f"{index + 20:08d}-0000-4000-8000-000000000000"),
                kind=kind,
                uri=f"artifact://candidate-v2/{kind.value}",
                sha256=(format(index + 2, "x") * 64)[:64],
                size_bytes=index + 1,
                candidate_version="candidate-v2",
                source_revision="commit-candidate-v2",
            )
            for index, kind in enumerate(ImprovementArtifactKind)
        ),
    )
    return bundle.model_dump(mode="json")


@pytest.mark.anyio
async def test_get_self_improvement_lifecycle_reads_canonical_project_snapshot(client):
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=_lifecycle_snapshot())
    _patch_storage(storage)

    response = await client.get(f"/projects/{PROJECT_ID}/self-improvement")

    assert response.status_code == 200
    assert response.json()["status"] == "project_bound"
    assert response.json()["revision"] == 1


@pytest.mark.anyio
async def test_link_self_improvement_reference_uses_revisioned_writer(client):
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=_lifecycle_snapshot())
    storage.update_self_improvement_lifecycle = AsyncMock(
        return_value=_fake_project("INIT", project_id=PROJECT_ID)
    )
    _patch_storage(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/references",
        json={"kind": "worker_run", "reference": "run-123"},
    )

    assert response.status_code == 200
    lifecycle = storage.update_self_improvement_lifecycle.await_args.args[1]
    assert lifecycle.integration_refs["worker_run"] == ("run-123",)
    assert lifecycle.revision == 1
    assert response.json()["lifecycle"]["integration_refs"]["worker_run"] == ["run-123"]


@pytest.mark.anyio
async def test_link_self_improvement_reference_returns_conflict_on_stale_snapshot(client):
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=_lifecycle_snapshot())
    storage.update_self_improvement_lifecycle = AsyncMock(return_value=None)
    _patch_storage(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/references",
        json={"kind": "artifact", "reference": "artifact-123"},
    )

    assert response.status_code == 409


@pytest.mark.anyio
async def test_self_improvement_action_persists_technical_gate(client):
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=_lifecycle_snapshot())
    storage.update_self_improvement_lifecycle = AsyncMock(
        return_value=_fake_project("INIT", project_id=PROJECT_ID)
    )
    _patch_storage(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/actions",
        json={
            "action": "record_gate",
            "gate": "coding",
            "passed": True,
            "evidence_refs": ["worker-run:run-1"],
        },
    )

    assert response.status_code == 200
    lifecycle = storage.update_self_improvement_lifecycle.await_args.args[1]
    assert lifecycle.gates["coding"].status.value == "passed"
    assert lifecycle.gates["coding"].evidence_refs == ("worker-run:run-1",)


@pytest.mark.anyio
async def test_self_improvement_action_cannot_approve_as_service(client):
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(
        return_value=_promotion_pending_snapshot()
    )
    _patch_storage(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/actions",
        json={"action": "approve_promotion"},
    )

    assert response.status_code == 403
    storage.update_self_improvement_lifecycle.assert_not_awaited()


@pytest.mark.anyio
async def test_self_improvement_action_requires_action_fields(client):
    storage = _make_storage()
    _patch_storage(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/actions",
        json={"action": "record_observation"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_self_improvement_action_persists_terminal_outcome_learning(client):
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=_rolled_back_snapshot())
    storage.update_self_improvement_lifecycle = AsyncMock(
        return_value=_fake_project("INIT", project_id=PROJECT_ID)
    )
    _patch_storage(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/actions",
        json={
            "action": "record_outcome",
            "outcome": "rolled_back",
            "cost_usd": "8.75",
            "incident_count": 1,
            "rollback_performed": True,
            "kpi_learning": {"recovery_minutes": 4.5},
            "evidence_refs": ["evidence/outcome"],
            "detail": "prior version restored",
        },
    )

    assert response.status_code == 200
    lifecycle = storage.update_self_improvement_lifecycle.await_args.args[1]
    assert len(lifecycle.outcomes) == 1
    assert lifecycle.outcomes[0].outcome.value == "rolled_back"
    assert str(lifecycle.outcomes[0].cost_usd) == "8.75"
    assert response.json()["lifecycle"]["outcomes"][0]["kpi_learning"] == {
        "recovery_minutes": 4.5
    }


@pytest.mark.anyio
async def test_self_improvement_action_persists_immutable_artifact_bundle(client):
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=_lifecycle_snapshot())
    storage.update_self_improvement_lifecycle = AsyncMock(
        return_value=_fake_project("INIT", project_id=PROJECT_ID)
    )
    _patch_storage(storage)

    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/actions",
        json={"action": "record_artifacts", "artifact_bundle": _artifact_bundle_payload()},
    )

    assert response.status_code == 200
    lifecycle = storage.update_self_improvement_lifecycle.await_args.args[1]
    assert lifecycle.artifact_bundle is not None
    assert len(lifecycle.artifact_bundle.artifacts) == 5
    assert len(lifecycle.integration_refs["artifact"]) == 5
    assert response.json()["lifecycle"]["artifact_bundle"]["schema_version"] == (
        "aiat.self-improvement-artifacts.v1"
    )


@pytest.mark.anyio
async def test_self_improvement_action_persists_provider_artifact_readback(client):
    from mas_core.workflow import ImprovementArtifactBundle, SelfImprovementLifecycle

    lifecycle = SelfImprovementLifecycle.from_dict(_lifecycle_snapshot())
    bundle = ImprovementArtifactBundle.model_validate(_artifact_bundle_payload())
    lifecycle.record_artifact_bundle(bundle, actor="service", actor_kind="system")
    storage = _make_storage()
    storage.get_self_improvement_lifecycle = AsyncMock(return_value=lifecycle.as_dict())
    storage.update_self_improvement_lifecycle = AsyncMock(
        return_value=_fake_project("INIT", project_id=PROJECT_ID)
    )
    _patch_storage(storage)

    first = bundle.artifacts[0]
    response = await client.post(
        f"/projects/{PROJECT_ID}/self-improvement/actions",
        json={
            "action": "record_artifact_readback",
            "artifact_id": str(first.artifact_id),
            "actual_sha256": first.sha256,
            "actual_size_bytes": first.size_bytes,
            "readback_source": "s3-compatible-fixture",
        },
    )

    assert response.status_code == 200
    persisted = storage.update_self_improvement_lifecycle.await_args.args[1]
    assert len(persisted.artifact_readbacks) == 1
    assert persisted.artifact_readbacks[0].verified is True
    assert response.json()["lifecycle"]["artifact_readbacks"][0]["source"] == (
        "s3-compatible-fixture"
    )
