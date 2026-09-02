"""Tests for the guarded self-improvement lifecycle contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from mas_core.workflow import (
    GateName,
    ImprovementArtifact,
    ImprovementArtifactBundle,
    ImprovementArtifactKind,
    ImprovementArtifactReadback,
    ImprovementOpportunity,
    ImprovementOutcomeKind,
    ImprovementRisk,
    SelfImprovementAuthorityError,
    SelfImprovementLifecycle,
    SelfImprovementTransitionError,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_self_improvement_lifecycle.py"


def _opportunity() -> ImprovementOpportunity:
    return ImprovementOpportunity(
        opportunity_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        title="Bounded improvement",
        description="Improve a governed path.",
        owner="cto",
        risk=ImprovementRisk.LOW,
        budget_usd="1.25",
        evidence_policy="software_delivery",
        source="test",
        created_by="agent-1",
        created_by_kind="agent",
        licence_metadata={"license": "AGPL-3.0", "commercial_use": "unknown"},
    )


def _bound_lifecycle() -> SelfImprovementLifecycle:
    lifecycle = SelfImprovementLifecycle.create(_opportunity(), active_version="v1")
    lifecycle.bind_project(
        UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), actor="orchestrator", actor_kind="system"
    )
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
            actor=f"{gate.value}-worker",
            actor_kind="agent",
            evidence_refs=(f"evidence/{gate.value}",),
        )
    return lifecycle


def _promoted_lifecycle() -> SelfImprovementLifecycle:
    lifecycle = _bound_lifecycle()
    lifecycle.start_shadow(candidate_version="v2", actor="cto", actor_kind="human")
    lifecycle.record_observation(stage="shadow", sample_count=5, regression_fraction=0.0)
    lifecycle.start_canary(actor="cto", actor_kind="human")
    lifecycle.record_observation(stage="canary", sample_count=3, regression_fraction=0.0)
    lifecycle.request_promotion(actor="cto", actor_kind="human")
    lifecycle.record_gate(
        GateName.HUMAN_APPROVAL,
        passed=True,
        actor="operator",
        actor_kind="human",
        evidence_refs=("evidence/approval",),
    )
    lifecycle.approve_promotion(actor="operator", actor_kind="human")
    return lifecycle


def _artifact_bundle() -> ImprovementArtifactBundle:
    return ImprovementArtifactBundle(
        bundle_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        candidate_version="v2",
        generated_by="orchestrator",
        generated_by_kind="system",
        artifacts=tuple(
            ImprovementArtifact(
                artifact_id=UUID(f"{index:08d}-0000-4000-8000-000000000000"),
                kind=kind,
                uri=f"artifact://improvement/v2/{kind.value}",
                sha256=(format(index, "x") * 64)[:64],
                size_bytes=index * 10,
                candidate_version="v2",
                source_revision="commit-v2",
                target_version="v2" if kind in {ImprovementArtifactKind.CHANGE, ImprovementArtifactKind.PROVENANCE} else None,
                metadata={"producer": "fixture"},
            )
            for index, kind in enumerate(ImprovementArtifactKind, start=1)
        ),
        metadata={"retention": "project-policy"},
    )


def test_canonical_project_request_carries_owner_risk_budget_and_evidence() -> None:
    lifecycle = SelfImprovementLifecycle.create(_opportunity())
    request = lifecycle.canonical_project_request(
        project_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    )
    config = request["config"]["self_improvement"]
    assert request["name"] == "Improvement: Bounded improvement"
    assert config["owner"] == "cto"
    assert config["risk"] == "low"
    assert config["budget_usd"] == "1.25"
    assert config["evidence_policy"] == "software_delivery"
    assert config["licence_metadata"]["license"] == "AGPL-3.0"


def test_lifecycle_snapshot_round_trips_full_opportunity_and_links() -> None:
    lifecycle = _bound_lifecycle()
    lifecycle.link_reference("issue", "issue-123")
    lifecycle.link_reference("worker_run", "run-456")

    restored = SelfImprovementLifecycle.from_dict(lifecycle.as_dict())

    assert restored.opportunity.description == "Improve a governed path."
    assert restored.opportunity.created_by == "agent-1"
    assert restored.integration_refs == {
        "issue": ("issue-123",),
        "worker_run": ("run-456",),
    }
    assert restored.revision == lifecycle.revision == 1


def test_lifecycle_reference_kind_is_explicit_and_deduplicated() -> None:
    lifecycle = _bound_lifecycle()
    lifecycle.link_reference("artifact", " artifact-1 ")
    lifecycle.link_reference("artifact", "artifact-1")
    assert lifecycle.integration_refs["artifact"] == ("artifact-1",)
    with pytest.raises(SelfImprovementTransitionError, match="unknown self-improvement reference"):
        lifecycle.link_reference("license", "metadata/license")


def test_technical_gates_are_independent_and_license_is_not_a_gate() -> None:
    lifecycle = SelfImprovementLifecycle.create(_opportunity(), active_version="v1")
    lifecycle.bind_project(
        UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), actor="orchestrator", actor_kind="system"
    )
    with pytest.raises(SelfImprovementTransitionError, match="technical gates"):
        lifecycle.start_shadow(candidate_version="v2", actor="cto", actor_kind="human")
    with pytest.raises(SelfImprovementTransitionError, match="unknown self-improvement gate"):
        lifecycle.record_gate(
            "license_review",
            passed=True,
            actor="agent",
            actor_kind="agent",
            evidence_refs=("metadata/license",),
        )
    lifecycle.record_gate(
        GateName.CODING,
        passed=False,
        actor="coding-worker",
        actor_kind="agent",
        detail="failed test fixture",
    )
    assert lifecycle.gates[GateName.TESTING].status.value == "pending"
    assert lifecycle.opportunity.licence_metadata["license"] == "AGPL-3.0"


def test_promotion_requires_human_approval_and_rollback_restores_exact_version() -> None:
    lifecycle = _bound_lifecycle()
    lifecycle.start_shadow(candidate_version="v2", actor="cto", actor_kind="human")
    lifecycle.record_observation(stage="shadow", sample_count=5, regression_fraction=0.0)
    lifecycle.start_canary(actor="cto", actor_kind="human")
    lifecycle.record_observation(stage="canary", sample_count=3, regression_fraction=0.0)
    lifecycle.request_promotion(actor="cto", actor_kind="human")
    with pytest.raises(SelfImprovementAuthorityError, match="cannot approve"):
        lifecycle.approve_promotion(actor="agent", actor_kind="agent")
    lifecycle.record_gate(
        GateName.HUMAN_APPROVAL,
        passed=True,
        actor="operator",
        actor_kind="human",
        evidence_refs=("evidence/approval",),
    )
    lifecycle.approve_promotion(actor="operator", actor_kind="human")
    assert lifecycle.active_version == "v2"
    lifecycle.rollback(actor="operator", actor_kind="human", reason="test rollback")
    assert lifecycle.status.value == "rolled_back"
    assert lifecycle.active_version == "v1"
    assert lifecycle.prior_version == "v1"


def test_bad_canary_observation_blocks_promotion() -> None:
    lifecycle = _bound_lifecycle()
    lifecycle.start_shadow(candidate_version="v2", actor="cto", actor_kind="human")
    lifecycle.record_observation(stage="shadow", sample_count=5, regression_fraction=0.25)
    with pytest.raises(SelfImprovementTransitionError, match="regression"):
        lifecycle.start_canary(actor="cto", actor_kind="human")


def test_outcome_persists_cost_incidents_rollback_and_kpi_learning_idempotently() -> None:
    lifecycle = _promoted_lifecycle()
    lifecycle.rollback(actor="operator", actor_kind="human", reason="fixture rollback")
    outcome_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    record = lifecycle.record_outcome(
        outcome_id=outcome_id,
        outcome=ImprovementOutcomeKind.ROLLED_BACK,
        cost_usd="8.75",
        incident_count=1,
        rollback_performed=True,
        kpi_learning={"recovery_minutes": 4.5, "regression_fraction": 0.01},
        evidence_refs=("evidence/outcome",),
        actor="operator",
        actor_kind="human",
        detail="rollback restored the prior worker version",
    )
    retry = lifecycle.record_outcome(
        outcome_id=outcome_id,
        outcome=ImprovementOutcomeKind.ROLLED_BACK,
        cost_usd="8.75",
        incident_count=1,
        rollback_performed=True,
        kpi_learning={"recovery_minutes": 4.5, "regression_fraction": 0.01},
        evidence_refs=("evidence/outcome",),
        actor="operator",
        actor_kind="human",
        detail="rollback restored the prior worker version",
    )

    assert retry == record
    assert len(lifecycle.outcomes) == 1
    assert str(record.cost_usd) == "8.75"
    assert record.incident_count == 1
    assert record.rollback_performed is True
    assert record.kpi_learning["recovery_minutes"] == 4.5
    restored = SelfImprovementLifecycle.from_dict(lifecycle.as_dict())
    assert restored.outcomes[0].outcome == ImprovementOutcomeKind.ROLLED_BACK
    assert str(restored.outcomes[0].cost_usd) == "8.75"

    with pytest.raises(SelfImprovementTransitionError, match="different evidence"):
        lifecycle.record_outcome(
            outcome_id=outcome_id,
            outcome=ImprovementOutcomeKind.ROLLED_BACK,
            cost_usd="9.00",
            incident_count=1,
            rollback_performed=True,
            actor="operator",
            actor_kind="human",
        )


def test_outcome_requires_terminal_state_and_bounded_learning() -> None:
    lifecycle = _bound_lifecycle()
    with pytest.raises(SelfImprovementTransitionError, match="promoted, rolled-back, or rejected"):
        lifecycle.record_outcome(
            outcome=ImprovementOutcomeKind.FAILURE,
            cost_usd="1.00",
            incident_count=0,
            actor="operator",
            actor_kind="human",
        )


def test_artifact_bundle_requires_five_immutable_kinds_and_links_canonically() -> None:
    lifecycle = _bound_lifecycle()
    bundle = _artifact_bundle()
    recorded = lifecycle.record_artifact_bundle(bundle, actor="orchestrator", actor_kind="system")

    assert recorded == bundle
    assert len(recorded.artifacts) == 5
    assert len(recorded.content_hash) == 64
    assert all(artifact.immutable is True for artifact in recorded.artifacts)
    assert set(lifecycle.integration_refs["artifact"]) == {
        str(artifact.artifact_id) for artifact in bundle.artifacts
    }
    restored = SelfImprovementLifecycle.from_dict(lifecycle.as_dict())
    assert restored.artifact_bundle is not None
    assert restored.artifact_bundle.content_hash == bundle.content_hash

    retry = lifecycle.record_artifact_bundle(bundle, actor="orchestrator", actor_kind="system")
    assert retry == recorded
    with pytest.raises(SelfImprovementTransitionError, match="different evidence"):
        lifecycle.record_artifact_bundle(
            bundle.model_copy(update={"metadata": {"retention": "changed"}}),
            actor="orchestrator",
            actor_kind="system",
        )

    with pytest.raises(ValueError, match="missing required kinds"):
        ImprovementArtifactBundle(
            candidate_version="v2",
            generated_by="orchestrator",
            generated_by_kind="system",
            artifacts=bundle.artifacts[:4],
        )
    with pytest.raises(ValueError, match="manifest_sha256"):
        ImprovementArtifactBundle.model_validate(
            {**bundle.model_dump(mode="json"), "manifest_sha256": "0" * 64}
        )

    lifecycle = _promoted_lifecycle()
    with pytest.raises(ValueError, match="finite"):
        lifecycle.record_outcome(
            outcome=ImprovementOutcomeKind.SUCCESS,
            cost_usd="1.00",
            incident_count=0,
            kpi_learning={"bad": float("nan")},
            actor="operator",
            actor_kind="human",
        )
    with pytest.raises(SelfImprovementTransitionError, match="rolled_back classification"):
        lifecycle.record_outcome(
            outcome=ImprovementOutcomeKind.FAILURE,
            cost_usd="1.00",
            incident_count=1,
            rollback_performed=True,
            actor="operator",
            actor_kind="human",
        )


def test_worker_artifacts_generate_manifest_and_provider_readback_is_verified() -> None:
    lifecycle = _bound_lifecycle()
    records = []
    payloads: dict[ImprovementArtifactKind, bytes] = {}
    for index, kind in enumerate(ImprovementArtifactKind, start=1):
        payload = f"{kind.value}-payload".encode()
        payloads[kind] = payload
        records.append(
            {
                "artifact_id": f"canonical-row-{index}",
                "kind": "file",
                "uri": f"s3://aiat/project/v2/{kind.value}",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "metadata": {
                    "self_improvement_kind": kind.value,
                    "candidate_version": "v2",
                    "source_revision": "worker-commit-v2",
                    "canonical_artifact_id": str(100 + index),
                    "worker_run_id": "run-v2",
                },
            }
        )

    bundle = ImprovementArtifactBundle.from_worker_artifacts(
        records,
        generated_by="worker-runtime",
        generated_by_kind="agent",
        bundle_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
    )
    lifecycle.record_artifact_bundle(bundle, actor="orchestrator", actor_kind="system")
    for artifact in bundle.artifacts:
        lifecycle.record_artifact_readback_bytes(
            artifact_id=artifact.artifact_id,
            data=payloads[artifact.kind],
            source="in-memory-object-store",
            actor="orchestrator",
            actor_kind="system",
            canonical_artifact_id=artifact.canonical_artifact_id,
        )

    assert lifecycle.artifact_readback_complete is True
    assert len(lifecycle.artifact_readbacks) == 5
    assert len(lifecycle.integration_refs["artifact_readback"]) == 5
    restored = SelfImprovementLifecycle.from_dict(lifecycle.as_dict())
    assert restored.artifact_readback_complete is True
    assert all(isinstance(item, ImprovementArtifactReadback) for item in restored.artifact_readbacks)
    with pytest.raises(SelfImprovementTransitionError, match="checksum mismatch"):
        lifecycle.record_artifact_readback_bytes(
            artifact_id=bundle.artifacts[0].artifact_id,
            data=b"tampered",
            source="in-memory-object-store",
            actor="orchestrator",
            actor_kind="system",
        )
def test_lifecycle_fixture_runner_passes_and_live_mode_blocks() -> None:
    fixture = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert fixture.returncode == 0
    report = json.loads(fixture.stdout)
    assert report["schema_version"] == "aiat.self-improvement-check.v1"
    assert report["status"] == "pass"
    assert report["rollback_exact"] is True

    live = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert live.returncode == 2
    assert json.loads(live.stdout)["status"] == "blocked"
