"""Certify durable local Postgres self-improvement lifecycle evidence.

The checker creates one reserved improvement project through the canonical
``AgentStorage.create_self_improvement_project`` writer, advances the real
typed lifecycle through technical gates, shadow, canary, human approval,
promotion, artifact read-back, and exact rollback, and reopens each snapshot
through the revisioned CAS writer.  It also proves that a stale lifecycle
revision cannot overwrite a newer snapshot.

This is local control-plane evidence only.  It does not dispatch a worker,
call a model/provider, execute a migration, or claim external artifact
durability.  Raw lifecycle payloads and licence details are never emitted;
licence metadata remains informational.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.company_manifest import DEFAULT_COMPANY_ID  # noqa: E402
from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.workflow import (  # noqa: E402
    GateName,
    ImprovementArtifactBundle,
    ImprovementArtifactKind,
    ImprovementOpportunity,
    ImprovementOutcomeKind,
    ImprovementRisk,
    SelfImprovementLifecycle,
)

CHECK_SCHEMA = "aiat.self-improvement-postgres-evidence-certification.v1"
EXPECTED_MIGRATION = "0037_worker_host_registry"
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000951")
OPPORTUNITY_ID = UUID("00000000-0000-4000-a000-000000000952")
BUNDLE_ID = UUID("00000000-0000-4000-a000-000000000953")
OUTCOME_ID = UUID("00000000-0000-4000-a000-000000000954")
ACTOR = "aiat-self-improvement-certification"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_SELF_IMPROVEMENT_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help=(
            "Postgres DSN; defaults to AIAT_SELF_IMPROVEMENT_EVIDENCE_DSN/"
            "PGBOUNCER_DSN/POSTGRES_DSN"
        ),
    )
    return parser


def _normalize_dsn(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value or "${" in value or "}" in value:
        return None
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    return value if value.startswith("postgresql+asyncpg://") else None


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-self-improvement",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "licence_metadata_is_gate": False,
    }


async def _migration_version(storage: AgentStorage) -> str | None:
    async with storage.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _cleanup(storage: AgentStorage) -> int:
    """Delete only the reserved project; dependent rows cascade by schema."""

    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text("DELETE FROM projects WHERE id = :project_id"),
            {"project_id": PROJECT_ID},
        )
    return int(result.rowcount or 0)


async def _counts(storage: AgentStorage) -> dict[str, int | str | None]:
    async with storage.engine.connect() as connection:
        project = await connection.execute(
            sa.text(
                """SELECT state, revision,
                          (config -> 'self_improvement' -> 'lifecycle' ->> 'revision') AS lifecycle_revision
                     FROM projects WHERE id = :project_id"""
            ),
            {"project_id": PROJECT_ID},
        )
        project_row = project.mappings().first()
        history = await connection.scalar(
            sa.text("SELECT count(*) FROM project_state_history WHERE project_id = :project_id"),
            {"project_id": PROJECT_ID},
        )
    return {
        "project_present": 1 if project_row else 0,
        "project_state": str(project_row["state"]) if project_row else None,
        "project_revision": int(project_row["revision"]) if project_row else None,
        "lifecycle_revision": (
            int(project_row["lifecycle_revision"])
            if project_row and project_row["lifecycle_revision"] is not None
            else None
        ),
        "history_rows": int(history or 0),
    }


def _artifact_inputs() -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    worker_artifacts: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    for index, kind in enumerate(ImprovementArtifactKind, start=1):
        payload = f"{kind.value}:aiat-self-improvement-postgres-v1".encode()
        payloads[kind.value] = payload
        worker_artifacts.append(
            {
                "artifact_id": f"self-improvement-worker-artifact-{index}",
                "uri": f"artifact://aiat/self-improvement-postgres-v1/{kind.value}",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "metadata": {
                    "self_improvement_kind": kind.value,
                    "candidate_version": "self-improvement-postgres-v1",
                    "source_revision": "self-improvement-postgres-baseline",
                    "canonical_artifact_id": f"self-improvement-artifact-row-{index}",
                    "producer": "local-certification-fixture",
                },
            }
        )
    return worker_artifacts, payloads


async def _run(dsn: str) -> dict[str, Any]:
    storage = AgentStorage(dsn)
    await storage.connect()
    migration_version = await _migration_version(storage)
    if migration_version != EXPECTED_MIGRATION:
        await storage.close()
        return {
            **_blocked("database is not at the native trace migration head"),
            "migration_version": migration_version,
        }

    cleanup_before = await _cleanup(storage)
    opportunity = ImprovementOpportunity(
        opportunity_id=OPPORTUNITY_ID,
        title="Certify durable governed self-improvement lifecycle",
        description="Exercise the canonical project and lifecycle writers locally.",
        owner="operator",
        owner_kind="human",
        risk=ImprovementRisk.MEDIUM,
        budget_usd="7.25",
        evidence_policy="software_delivery",
        source="operator_certification",
        created_by="operator",
        created_by_kind="human",
        company_id=DEFAULT_COMPANY_ID,
        licence_metadata={"source": "local-fixture", "restriction_notice": "metadata-only"},
    )
    mutation_performed = False
    cas_conflict = False
    try:
        await storage.create_self_improvement_project(opportunity, project_id=PROJECT_ID)
        mutation_performed = True
        lifecycle_payload = await storage.get_self_improvement_lifecycle(PROJECT_ID)
        if lifecycle_payload is None:
            raise RuntimeError("created project has no lifecycle snapshot")
        lifecycle = SelfImprovementLifecycle.from_dict(lifecycle_payload)

        async def persist(next_lifecycle: SelfImprovementLifecycle) -> SelfImprovementLifecycle:
            updated = await storage.update_self_improvement_lifecycle(
                PROJECT_ID, next_lifecycle, actor=ACTOR
            )
            if updated is None:
                raise RuntimeError("lifecycle CAS update unexpectedly lost its project revision")
            refreshed = await storage.get_self_improvement_lifecycle(PROJECT_ID)
            if refreshed is None:
                raise RuntimeError("lifecycle disappeared after persistence")
            return SelfImprovementLifecycle.from_dict(refreshed)

        # The canonical project writer starts a new improvement without an
        # active version.  Record the existing immutable baseline through the
        # same revisioned writer before exercising promotion/rollback.
        lifecycle.active_version = "self-improvement-postgres-baseline"
        lifecycle = await persist(lifecycle)
        stale_snapshot = SelfImprovementLifecycle.from_dict(lifecycle.as_dict())

        # A stale copy must not overwrite the first legitimate gate update.
        lifecycle.record_gate(
            GateName.CODING,
            passed=True,
            actor="coding-worker",
            actor_kind="agent",
            evidence_refs=("evidence/local-coding",),
        )
        lifecycle = await persist(lifecycle)
        cas_result = await storage.update_self_improvement_lifecycle(
            PROJECT_ID, stale_snapshot, actor=ACTOR
        )
        cas_conflict = cas_result is None

        for gate in (
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
                evidence_refs=(f"evidence/local-{gate.value}",),
            )
            lifecycle = await persist(lifecycle)

        lifecycle.start_shadow(
            candidate_version="self-improvement-postgres-v1",
            actor="cto",
            actor_kind="human",
        )
        lifecycle.record_observation(stage="shadow", sample_count=4, regression_fraction=0.0)
        lifecycle = await persist(lifecycle)

        worker_artifacts, payloads = _artifact_inputs()
        bundle = ImprovementArtifactBundle.from_worker_artifacts(
            worker_artifacts,
            bundle_id=BUNDLE_ID,
            generated_by="local-certification-worker",
            generated_by_kind="agent",
            candidate_version="self-improvement-postgres-v1",
            source_revision="self-improvement-postgres-baseline",
            metadata={"scope": "local-postgres"},
        )
        lifecycle.record_artifact_bundle(bundle, actor=ACTOR, actor_kind="system")
        lifecycle = await persist(lifecycle)
        for artifact in bundle.artifacts:
            lifecycle.record_artifact_readback_bytes(
                artifact_id=artifact.artifact_id,
                data=payloads[artifact.kind.value],
                source="local-object-store-fixture",
                actor=ACTOR,
                actor_kind="system",
                canonical_artifact_id=artifact.canonical_artifact_id,
            )
            lifecycle = await persist(lifecycle)

        lifecycle.start_canary(actor="cto", actor_kind="human")
        lifecycle.record_observation(stage="canary", sample_count=3, regression_fraction=0.0)
        lifecycle = await persist(lifecycle)
        lifecycle.request_promotion(actor="cto", actor_kind="human")
        lifecycle = await persist(lifecycle)
        lifecycle.record_gate(
            GateName.HUMAN_APPROVAL,
            passed=True,
            actor="operator",
            actor_kind="human",
            evidence_refs=("evidence/local-human-approval",),
        )
        lifecycle = await persist(lifecycle)
        lifecycle.approve_promotion(actor="operator", actor_kind="human")
        lifecycle = await persist(lifecycle)
        promoted_version = lifecycle.active_version
        lifecycle.rollback(
            actor="operator",
            actor_kind="human",
            reason="local exact rollback certification",
        )
        lifecycle = await persist(lifecycle)
        outcome = lifecycle.record_outcome(
            outcome_id=OUTCOME_ID,
            outcome=ImprovementOutcomeKind.ROLLED_BACK,
            cost_usd="4.50",
            incident_count=0,
            rollback_performed=True,
            kpi_learning={"rollback_minutes": 1.5},
            evidence_refs=("evidence/local-outcome",),
            actor="operator",
            actor_kind="human",
            detail="local exact rollback certification",
        )
        lifecycle = await persist(lifecycle)
        lifecycle.assert_invariants()
        counts = await _counts(storage)
        report = {
            "schema_version": CHECK_SCHEMA,
            "mode": "local-postgres-self-improvement",
            "status": "pass",
            "migration_version": migration_version,
            "project_id_present": lifecycle.project_id == PROJECT_ID,
            "reserved_rows_removed_before": cleanup_before,
            "cas_conflict_rejected": cas_conflict,
            "technical_gate_count": sum(
                1 for gate in lifecycle.gates.values() if gate.status.value == "passed"
            )
            - 1,
            "human_approval_recorded": lifecycle.gates[GateName.HUMAN_APPROVAL].status.value
            == "passed",
            "promoted_version_before_rollback": promoted_version,
            "rollback_exact": lifecycle.active_version == "self-improvement-postgres-baseline",
            "artifact_bundle_recorded": lifecycle.artifact_bundle is not None,
            "artifact_bundle_complete": (
                lifecycle.artifact_bundle is not None
                and len(lifecycle.artifact_bundle.artifacts) == len(ImprovementArtifactKind)
            ),
            "artifact_readback_complete": lifecycle.artifact_readback_complete,
            "artifact_readback_count": len(lifecycle.artifact_readbacks),
            "outcome_persisted": len(lifecycle.outcomes) == 1,
            "outcome_cost_usd": str(outcome.cost_usd),
            "outcome_incident_count": outcome.incident_count,
            "final_status": lifecycle.status.value,
            "final_revision": lifecycle.revision,
            "history_transition_count": len(lifecycle.history),
            "counts": counts,
            "mutation_performed": mutation_performed,
            "local_database_access_performed": True,
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "worker_dispatch_performed": False,
            "payload_free_report": True,
            "licence_metadata_is_gate": False,
            "scope": "reserved local Postgres project/lifecycle only; no worker/provider/deployment mutation",
        }
        report["status"] = "pass" if all(
            (
                report["project_id_present"],
                report["cas_conflict_rejected"],
                report["technical_gate_count"] == 6,
                report["human_approval_recorded"],
                report["rollback_exact"],
                report["artifact_bundle_complete"],
                report["artifact_readback_complete"],
                report["outcome_persisted"],
                counts["project_present"] == 1,
            )
        ) else "fail"
        return report
    finally:
        cleanup_after = await _cleanup(storage)
        await storage.close()
        if "report" in locals():
            report["cleanup"] = {
                "before_rows": cleanup_before,
                "after_rows": cleanup_after,
                "remaining_project_rows": (await _remaining_project_count(dsn)),
            }


async def _remaining_project_count(dsn: str) -> int:
    """Reopen only long enough to verify the reserved project is gone."""

    storage = AgentStorage(dsn)
    await storage.connect()
    try:
        async with storage.engine.connect() as connection:
            value = await connection.scalar(
                sa.text("SELECT count(*) FROM projects WHERE id = :project_id"),
                {"project_id": PROJECT_ID},
            )
        return int(value or 0)
    finally:
        await storage.close()


async def _async_main(raw_dsn: str | None) -> tuple[dict[str, Any], int]:
    dsn = _normalize_dsn(raw_dsn)
    if dsn is None:
        return _blocked("Postgres DSN is not configured"), 2
    try:
        report = await _run(dsn)
    except Exception as exc:  # noqa: BLE001 - checker must emit a safe blocked report
        return {
            **_blocked("local self-improvement certification could not complete"),
            "error_type": type(exc).__name__,
        }, 2
    if report.get("status") == "pass":
        return report, 0
    return report, 2 if report.get("status") == "blocked" else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = asyncio.run(_async_main(args.dsn))
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, default=str))
    else:
        print(f"self-improvement Postgres evidence: {report['status']} — {report.get('scope', report.get('reason', ''))}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
