"""Certify durable local Postgres worker-run and trace evidence.

This probe registers one reserved fixture worker, executes the real
``WorkerRunController`` with the real ``NativeWorkerAdapter``, persists a
normalized result, and reads the worker run, usage, artifact, transitions, and
payload-free native trace evidence back through a second Postgres connection.
The fixture is deterministic and uses no external model, provider, runtime,
or network endpoint.  Its task input and result contain a private marker only
to prove that the bounded evidence report does not expose payload data.

The checker requires a Postgres DSN supplied by the caller and exits with
status 2 when the database is not configured, unavailable, or not at the
native-trace migration head.  It never falls back to in-memory storage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if CORE_ROOT.exists() and str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.observability.trace_evidence import build_trace_evidence  # noqa: E402
from mas_core.observability.worker_trace_coverage import (  # noqa: E402
    WORKER_TRACE_COVERAGE_SCHEMA,
    evaluate_worker_trace_coverage,
)
from mas_core.worker_contract.adapters import NativeWorkerAdapter  # noqa: E402
from mas_core.worker_contract.controller import WorkerRunController  # noqa: E402
from mas_core.worker_contract.models import (  # noqa: E402
    ArtifactKind,
    WorkerArtifact,
    WorkerResult,
    WorkerRunRequest,
    WorkerUsage,
)

CHECK_SCHEMA = "aiat.worker-run-postgres-evidence-certification.v1"
EXPECTED_MIGRATION = "0036_native_trace_spans"
WORKER_NAME = "aiat-cert-worker-postgres-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
TRACE_ID = "aiat-cert-worker-postgres-v1-trace"
SPAN_ID = "aiat-cert-worker-postgres-v1-span"
WORKER_SPAN_ID = "aiat-cert-worker-postgres-v1-worker-span"
IDEMPOTENCY_KEY = "aiat-cert-worker-postgres-v1-idempotency"
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000931")
RUN_ID = UUID("00000000-0000-4000-a000-000000000932")
PAYLOAD_MARKER = "aiat fixture payload must never enter the evidence report"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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


def _blocked(reason: str, *, migration_version: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-run",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }
    if migration_version is not None:
        report["migration_version"] = migration_version
    return report


async def _migration_version(storage: AgentStorage) -> str | None:
    async with storage.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _fixture_snapshot(storage: AgentStorage) -> dict[str, int]:
    """Return counts for the reserved namespace, never request/result JSON."""

    async with storage.engine.connect() as connection:
        values = {
            "workers": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_registry WHERE name LIKE :prefix"),
                {"prefix": WORKER_PREFIX},
            ),
            "runs": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM worker_runs
                       WHERE worker_id IN (
                         SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
            "artifacts": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM worker_artifacts
                       WHERE run_id IN (
                         SELECT id FROM worker_runs
                         WHERE worker_id IN (
                           SELECT id FROM worker_registry WHERE name LIKE :prefix
                         )
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
            "usage": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM worker_usage_records
                       WHERE run_id IN (
                         SELECT id FROM worker_runs
                         WHERE worker_id IN (
                           SELECT id FROM worker_registry WHERE name LIKE :prefix
                         )
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
            "spans": await connection.scalar(
                sa.text("SELECT count(*) FROM native_trace_spans WHERE trace_id = :trace_id"),
                {"trace_id": TRACE_ID},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    """Delete only rows owned by the reserved worker/trace fixture."""

    async with storage.engine.begin() as connection:
        artifact_rows = (
            await connection.execute(
                sa.text(
                    """SELECT artifact_id FROM worker_artifacts
                       WHERE run_id IN (
                         SELECT id FROM worker_runs
                         WHERE worker_id IN (
                           SELECT id FROM worker_registry WHERE name LIKE :prefix
                         )
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            )
        ).scalars().all()
        artifacts = [int(value) for value in artifact_rows if value is not None]
        deleted_spans = await connection.execute(
            sa.text("DELETE FROM native_trace_spans WHERE trace_id = :trace_id"),
            {"trace_id": TRACE_ID},
        )
        # worker_artifacts has a RESTRICT link to canonical artifacts, so its
        # links are removed before the worker run and canonical artifact rows.
        deleted_links = await connection.execute(
            sa.text(
                """DELETE FROM worker_artifacts
                   WHERE run_id IN (
                     SELECT id FROM worker_runs
                     WHERE worker_id IN (
                       SELECT id FROM worker_registry WHERE name LIKE :prefix
                     )
                   )"""
            ),
            {"prefix": WORKER_PREFIX},
        )
        deleted_runs = await connection.execute(
            sa.text(
                """DELETE FROM worker_runs
                   WHERE worker_id IN (
                     SELECT id FROM worker_registry WHERE name LIKE :prefix
                   )"""
            ),
            {"prefix": WORKER_PREFIX},
        )
        deleted_artifacts = 0
        if artifacts:
            result = await connection.execute(
                sa.text("DELETE FROM artifacts WHERE id = ANY(:ids)").bindparams(
                    sa.bindparam("ids", type_=sa.ARRAY(sa.BigInteger))
                ),
                {"ids": artifacts},
            )
            deleted_artifacts = int(result.rowcount or 0)
        deleted_workers = await connection.execute(
            sa.text("DELETE FROM worker_registry WHERE name LIKE :prefix"),
            {"prefix": WORKER_PREFIX},
        )
    return {
        "spans": int(deleted_spans.rowcount or 0),
        "worker_artifacts": int(deleted_links.rowcount or 0),
        "worker_runs": int(deleted_runs.rowcount or 0),
        "artifacts": deleted_artifacts,
        "workers": int(deleted_workers.rowcount or 0),
    }


async def _fixture_worker(request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={"result": "fixture-complete", "private_marker": PAYLOAD_MARKER},
        artifacts=[
            WorkerArtifact(
                kind=ArtifactKind.REPORT,
                name="worker-run-fixture-report.json",
                uri="fixture://aiat/worker-run-postgres-v1/report.json",
                sha256="b" * 64,
                size_bytes=128,
                mime_type="application/json",
            )
        ],
        usage=WorkerUsage(
            prompt_tokens=11,
            completion_tokens=17,
            cost_usd=0.0123,
            duration_ms=4.5,
            cpu_seconds=0.02,
            memory_bytes=4096,
            provider="fixture-gateway",
            exact_model_id="fixture-model-v1",
        ),
        completion_criteria={"criterion": "durable-evidence"},
    )


def _safe_blob(*values: Any) -> str:
    return json.dumps(values, default=str, sort_keys=True)


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    first_snapshot: dict[str, int] = {}
    run_row: dict[str, Any] | None = None
    reopened_healthy = False
    durable_run: dict[str, Any] | None = None
    durable_usage: list[dict[str, Any]] = []
    durable_artifacts: list[dict[str, Any]] = []
    durable_spans: list[dict[str, Any]] = []
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    migration_version: str | None = None
    transition_count = 0
    event_count = 0
    try:
        await storage.connect()
        async with storage.engine.connect() as connection:
            await connection.execute(sa.text("SELECT 1"))
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
                "network_access_performed": True,
            }
        await _cleanup(storage)
        first_snapshot = await _fixture_snapshot(storage)
        registered = await storage.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_REGISTRY_ID,
            adapter_type="native",
            adapter_config={"fixture": "durable-postgres-worker-run"},
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1",
            source_repo="internal-fixture",
            source_revision="fixture-postgres-v1",
            version_pin="fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="NativeWorkerAdapter",
            isolation_mode="native",
            model_mode="aiat_gateway",
        )
        canonical_worker_id = UUID(str(registered["id"]))
        request = WorkerRunRequest(
            run_id=RUN_ID,
            idempotency_key=IDEMPOTENCY_KEY,
            worker_id=str(canonical_worker_id),
            task_type="durable_worker_evidence_fixture",
            task_input={"private_marker": PAYLOAD_MARKER, "operation": "durability"},
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
            timeout_seconds=30,
        )
        controller = WorkerRunController(storage=storage)
        adapter = NativeWorkerAdapter(
            _fixture_worker,
            worker_id=str(canonical_worker_id),
            runtime_version="fixture-runtime-v1",
        )
        try:
            outcome = await controller.execute(
                request,
                adapter,
                worker_registry_id=canonical_worker_id,
            )
        finally:
            await adapter.close()
        await storage.create_native_trace_span(
            trace_id=TRACE_ID,
            span_id=WORKER_SPAN_ID,
            parent_span_id=SPAN_ID,
            source_kind="worker",
            operation="worker.execute",
            service="worker_run_controller",
            status="success" if outcome.state == "SUCCEEDED" else "failure",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            attributes={"run_state": outcome.state, "fixture": True},
        )
        run_row = await storage.get_worker_run(RUN_ID)
        transitions = await storage.list_worker_run_transitions(RUN_ID)
        events = await storage.list_worker_events(RUN_ID)
        transition_count = len(transitions)
        event_count = len(events)
        trace_usage_rows = await storage.list_worker_usage_records_by_trace(TRACE_ID)
        trace_artifact_rows = await storage.list_worker_artifacts_by_trace(TRACE_ID)
        trace_span_rows = await storage.list_native_trace_spans_by_trace(TRACE_ID)
        evidence = build_trace_evidence(
            trace_id=TRACE_ID,
            worker_usage_rows=trace_usage_rows,
            artifact_rows=trace_artifact_rows,
            native_span_rows=trace_span_rows,
        )
        coverage = evaluate_worker_trace_coverage(evidence)
        safe_projection_blob = _safe_blob(
            evidence.model_dump(mode="json"),
            trace_usage_rows,
            trace_artifact_rows,
            trace_span_rows,
        )
        payload_free = PAYLOAD_MARKER not in safe_projection_blob
        if not payload_free:
            coverage = {**coverage, "status": "fail", "missing_required_sources": ["payload_redaction"]}
        await storage.close()

        reopened = AgentStorage(normalized_dsn)
        try:
            await reopened.connect()
            reopened_healthy = True
            durable_run = await reopened.get_worker_run(RUN_ID)
            durable_usage = await reopened.list_worker_usage(RUN_ID)
            durable_artifacts = await reopened.list_worker_artifacts(RUN_ID)
            durable_spans = await reopened.list_native_trace_spans_by_trace(TRACE_ID)
            cleanup_counts = await _cleanup(reopened)
            remaining = await _fixture_snapshot(reopened)
        finally:
            with suppress(Exception):
                await reopened.close()
    except Exception as exc:
        return {
            **_blocked("local_postgres_worker_evidence_failed"),
            "failure_type": type(exc).__name__,
            "local_database_access_performed": True,
            "network_access_performed": True,
        }
    finally:
        if getattr(storage, "_engine", None) is not None:
            with suppress(Exception):
                await _cleanup(storage)
            with suppress(Exception):
                await storage.close()

    run_state = str((run_row or {}).get("state") or "unknown")
    trace_usage_rows = durable_usage
    trace_artifact_rows = durable_artifacts
    evidence = build_trace_evidence(
        trace_id=TRACE_ID,
        worker_usage_rows=trace_usage_rows,
        artifact_rows=trace_artifact_rows,
        native_span_rows=durable_spans,
    )
    coverage = evaluate_worker_trace_coverage(evidence)
    payload_free = PAYLOAD_MARKER not in _safe_blob(evidence.model_dump(mode="json"), durable_spans)
    passed = (
        migration_version == EXPECTED_MIGRATION
        and run_state == "SUCCEEDED"
        and str((run_row or {}).get("id")) == str(RUN_ID)
        and transition_count >= 5
        and event_count >= 2
        and len(durable_usage) == 1
        and len(durable_artifacts) == 1
        and len(durable_spans) >= 3
        and coverage["status"] == "pass"
        and payload_free
        and reopened_healthy
        and remaining == {"workers": 0, "runs": 0, "artifacts": 0, "usage": 0, "spans": 0}
        and sum(cleanup_counts.values()) >= 5
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-run",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "worker_name": WORKER_NAME,
        "run_state": run_state,
        "transition_count": transition_count,
        "event_count": event_count,
        "usage_count": len(durable_usage),
        "artifact_count": len(durable_artifacts),
        "native_span_count": len(durable_spans),
        "trace_item_count": evidence.item_count,
        "trace_source_counts": evidence.source_counts,
        "trace_coverage": coverage,
        "initial_fixture_counts": first_snapshot,
        "durable_reopen": {
            "healthy": reopened_healthy,
            "run_present": durable_run is not None,
            "usage_count": len(durable_usage),
            "artifact_count": len(durable_artifacts),
            "native_span_count": len(durable_spans),
        },
        "cleanup_deleted_counts": cleanup_counts,
        "remaining_fixture_counts": remaining,
        "payload_free": payload_free,
        "mutation_performed": True,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "scope": "real WorkerRunController/NativeWorkerAdapter lifecycle, Postgres evidence persistence, connection reopen, and bounded trace projection",
        "certification_boundary": {
            "worker_registry_and_run_lifecycle": "checked",
            "artifact_and_usage_persistence": "checked",
            "payload_free_trace_projection": "checked",
            "postgres_connection_reopen": "checked",
            "scoped_fixture_cleanup": "checked",
            "live_model_backed_worker": "not_checked",
            "external_provider": "not_checked",
            "deployed_worker_runtime": "not_checked",
            "retention_enforcement": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"worker-run Postgres evidence certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
