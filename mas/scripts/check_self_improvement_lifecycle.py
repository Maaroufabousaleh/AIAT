"""Run the deterministic guarded self-improvement lifecycle fixture.

The fixture creates a canonical project request with owner, risk, budget, and
evidence policy; records independent technical gates; completes shadow/canary
and human-approved promotion; then performs an exact rollback to the prior
immutable version, converts five worker-produced records into the immutable
artifact bundle, verifies provider-style checksum/size read-back for every
artifact, and records bounded outcome/cost/incident/KPI learning.
``--live`` is intentionally fail-closed until a durable project/control-plane
integration is supplied.  Licence metadata is included only in the report and
never affects a transition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from uuid import UUID

from mas_core.workflow import (
    GateName,
    ImprovementArtifactBundle,
    ImprovementArtifactKind,
    ImprovementOpportunity,
    ImprovementOutcomeKind,
    ImprovementRisk,
    SelfImprovementLifecycle,
)

CHECK_SCHEMA = "aiat.self-improvement-check.v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--live", action="store_true", help="require the durable project/control-plane integration"
    )
    return parser


def _fixture() -> dict[str, object]:
    project_id = UUID("11111111-1111-4111-8111-111111111111")
    opportunity = ImprovementOpportunity(
        opportunity_id=UUID("22222222-2222-4222-8222-222222222222"),
        title="Improve governed worker recovery telemetry",
        description="Add bounded recovery evidence to the worker operator surface.",
        owner="cto",
        owner_kind="human",
        risk=ImprovementRisk.MEDIUM,
        budget_usd="12.50",
        evidence_policy="software_delivery",
        source="operator_goal",
        created_by="cto",
        created_by_kind="human",
        licence_metadata={"source": "internal-and-oss", "restriction_notice": "metadata-only"},
    )
    lifecycle = SelfImprovementLifecycle.create(opportunity, active_version="worker-runtime-v1")
    project_request = lifecycle.canonical_project_request(project_id=project_id)
    lifecycle.bind_project(project_id, actor="orchestrator", actor_kind="system")
    worker_artifacts: list[dict[str, object]] = []
    artifact_payloads: dict[str, bytes] = {}
    for index, kind in enumerate(ImprovementArtifactKind, start=1):
        payload = f"{kind.value}:worker-runtime-v2".encode()
        artifact_payloads[kind.value] = payload
        worker_artifacts.append(
            {
                "artifact_id": f"worker-artifact-row-{index}",
                "kind": "file",
                "uri": f"artifact://worker-runtime-v2/{kind.value}",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "metadata": {
                    "self_improvement_kind": kind.value,
                    "candidate_version": "worker-runtime-v2",
                    "source_revision": "worker-runtime-v1-to-v2",
                    "canonical_artifact_id": f"artifact-row-{index}",
                    "producer": "deterministic-worker-fixture",
                },
            }
        )
    artifact_bundle = ImprovementArtifactBundle.from_worker_artifacts(
        worker_artifacts,
        bundle_id=UUID("44444444-4444-4444-8444-444444444444"),
        generated_by="worker-runtime",
        generated_by_kind="agent",
        candidate_version="worker-runtime-v2",
        source_revision="worker-runtime-v1-to-v2",
        metadata={"retention": "project-policy"},
    )
    lifecycle.record_artifact_bundle(artifact_bundle, actor="orchestrator", actor_kind="system")
    for artifact in artifact_bundle.artifacts:
        lifecycle.record_artifact_readback_bytes(
            artifact_id=artifact.artifact_id,
            data=artifact_payloads[artifact.kind.value],
            source="deterministic-object-store",
            actor="orchestrator",
            actor_kind="system",
            canonical_artifact_id=artifact.canonical_artifact_id,
        )

    gate_evidence = {
        GateName.CODING: "evidence/coding-v2",
        GateName.TESTING: "evidence/tests-v2",
        GateName.REVIEW: "evidence/review-v2",
        GateName.SECURITY: "evidence/security-v2",
        GateName.MIGRATION: "evidence/migration-v2",
        GateName.ROLLBACK: "evidence/rollback-plan-v2",
    }
    for gate, evidence in gate_evidence.items():
        lifecycle.record_gate(
            gate,
            passed=True,
            actor=gate.value + "-worker",
            actor_kind="agent",
            evidence_refs=(evidence,),
        )
    lifecycle.start_shadow(candidate_version="worker-runtime-v2", actor="cto", actor_kind="human")
    lifecycle.record_observation(stage="shadow", sample_count=5, regression_fraction=0.02)
    lifecycle.start_canary(actor="cto", actor_kind="human")
    lifecycle.record_observation(stage="canary", sample_count=3, regression_fraction=0.01)
    lifecycle.request_promotion(actor="cto", actor_kind="human")
    lifecycle.record_gate(
        GateName.HUMAN_APPROVAL,
        passed=True,
        actor="operator",
        actor_kind="human",
        evidence_refs=("evidence/operator-approval-v2",),
    )
    lifecycle.approve_promotion(actor="operator", actor_kind="human")
    promoted_version = lifecycle.active_version
    lifecycle.rollback(actor="operator", actor_kind="human", reason="fixture rollback exercise")
    outcome = lifecycle.record_outcome(
        outcome_id=UUID("33333333-3333-4333-8333-333333333333"),
        outcome=ImprovementOutcomeKind.ROLLED_BACK,
        cost_usd="8.75",
        incident_count=1,
        rollback_performed=True,
        kpi_learning={"recovery_minutes": 4.5, "regression_fraction": 0.01},
        evidence_refs=("evidence/outcome-v2",),
        actor="operator",
        actor_kind="human",
        detail="fixture rollback restored the prior immutable version",
    )
    lifecycle.assert_invariants()
    report = lifecycle.as_dict()
    report.update(
        {
            "schema_version": CHECK_SCHEMA,
            "status": "pass",
            "project_request": {
                **project_request,
                "project_id": str(project_request["project_id"]),
                "company_id": str(project_request["company_id"])
                if project_request["company_id"]
                else None,
            },
            "promoted_version_before_rollback": promoted_version,
            "rollback_exact": lifecycle.active_version == "worker-runtime-v1",
            "artifact_bundle_recorded": lifecycle.artifact_bundle is not None,
            "artifact_bundle_complete": (
                lifecycle.artifact_bundle is not None
                and len(lifecycle.artifact_bundle.artifacts) == len(ImprovementArtifactKind)
            ),
            "artifact_bundle_sha256": (
                lifecycle.artifact_bundle.content_hash if lifecycle.artifact_bundle is not None else None
            ),
            "artifact_kinds": sorted(
                artifact.kind.value for artifact in lifecycle.artifact_bundle.artifacts
            )
            if lifecycle.artifact_bundle is not None
            else [],
            "artifact_readback_complete": lifecycle.artifact_readback_complete,
            "artifact_readback_count": len(lifecycle.artifact_readbacks),
            "outcome_persisted": len(lifecycle.outcomes) == 1,
            "outcome_cost_usd": str(outcome.cost_usd),
            "outcome_incident_count": outcome.incident_count,
            "outcome_rollback_performed": outcome.rollback_performed,
            "kpi_learning_persisted": dict(outcome.kpi_learning),
            "scope": "deterministic lifecycle fixture; no durable project or worker deployment was mutated",
        }
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.live:
        report: dict[str, object] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "durable project/control-plane integration is not configured",
            "scope": "no live project, worker, or deployment state was changed",
        }
        exit_code = 2
    else:
        report = {"mode": "fixture", **_fixture()}
        exit_code = (
            0
            if report.get("status") == "pass"
            and report.get("rollback_exact")
            and report.get("artifact_bundle_recorded")
            and report.get("artifact_bundle_complete")
            and report.get("artifact_readback_complete")
            and report.get("outcome_persisted")
            else 1
        )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, default=str))
    else:
        print(
            f"self-improvement lifecycle: {report['status']} — {report.get('reason', report.get('scope', ''))}"
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
