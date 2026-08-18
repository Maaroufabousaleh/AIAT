"""Certify one local Worker Run through the committed host executor.

The probe uses a deterministic AIAT-owned worker-plane host, a committed run
binding, the real ``WorkerHostExecutor``, ``WorkerRunController``, and a native
fixture adapter.  It proves host admission, Worker Run claiming, durable
terminal evidence, binding release, connection-reopen read-back, and scoped
cleanup.  It does not claim a real gVisor/Firecracker sandbox, a provider call,
or a remote worker runtime.
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
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.observability.trace_evidence import build_trace_evidence  # noqa: E402
from mas_core.observability.worker_trace_coverage import (  # noqa: E402
    WORKER_TRACE_COVERAGE_SCHEMA,
    evaluate_worker_trace_coverage,
)
from mas_core.worker_contract.adapters import NativeWorkerAdapter  # noqa: E402
from mas_core.worker_contract.models import (  # noqa: E402
    ArtifactKind,
    WorkerArtifact,
    WorkerResult,
    WorkerRunRequest,
    WorkerUsage,
)
from mas_core.worker_registry.host_executor import (  # noqa: E402
    HOST_EXECUTION_SCHEMA,
    HostExecutionRequest,
    WorkerHostExecutor,
)
from mas_core.worker_registry.host_registry import WorkerHostRegistry  # noqa: E402
from mas_core.worker_registry.placement import WorkerPlacementRequest  # noqa: E402
from mas_core.worker_registry.run_host_binding import (  # noqa: E402
    RUN_HOST_BINDING_SCHEMA,
    RunHostBindingRequest,
    WorkerRunHostBindingService,
)

CHECK_SCHEMA = "aiat.worker-host-execution-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
WORKER_NAME = "aiat-cert-host-execution-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
HOST_NAME = "aiat-cert-host-execution-worker-v1"
HOST_PREFIX = f"{HOST_NAME}%"
HOST_UUID = UUID("00000000-0000-4000-a000-000000000b31")
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000b32")
RUN_ID = UUID("00000000-0000-4000-a000-000000000b33")
RESERVATION_ID = UUID("00000000-0000-4000-a000-000000000b34")
TOKEN = "aiat-host-execution-fixture-token-v1"
OWNER = "aiat-host-execution-fixture"
ASSIGNMENT_KEY = "aiat-cert-host-execution-v1-assignment"
TRACE_ID = "aiat-cert-host-execution-v1-trace"
SPAN_ID = "aiat-cert-host-execution-v1-span"
WORKER_SPAN_ID = "aiat-cert-host-execution-v1-worker-span"
IDEMPOTENCY_KEY = "aiat-cert-host-execution-v1-idempotency"
PAYLOAD_MARKER = "aiat host execution fixture payload must never enter the evidence report"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_EXECUTION_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_HOST_EXECUTION_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "mode": "local-postgres-worker-host-execution",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "licence_metadata_is_gate": False,
    }
    if migration_version is not None:
        report["migration_version"] = migration_version
    return report


async def _migration_version(storage: AgentStorage) -> str | None:
    async with storage.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _counts(storage: AgentStorage) -> dict[str, int]:
    async with storage.engine.connect() as connection:
        values = {
            "workers": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_registry WHERE name LIKE :prefix"),
                {"prefix": WORKER_PREFIX},
            ),
            "runs": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_runs WHERE id = :run_id"),
                {"run_id": RUN_ID},
            ),
            "bindings": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM worker_run_host_bindings WHERE assignment_key = :key"
                ),
                {"key": ASSIGNMENT_KEY},
            ),
            "reservations": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM worker_host_reservations WHERE reservation_key = :key"
                ),
                {"key": ASSIGNMENT_KEY},
            ),
            "hosts": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_hosts WHERE host_id LIKE :prefix"),
                {"prefix": HOST_PREFIX},
            ),
            "spans": await connection.scalar(
                sa.text("SELECT count(*) FROM native_trace_spans WHERE trace_id = :trace_id"),
                {"trace_id": TRACE_ID},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    """Delete only rows owned by this deterministic fixture namespace."""

    async with storage.engine.begin() as connection:
        artifact_rows = (
            (
                await connection.execute(
                    sa.text("SELECT artifact_id FROM worker_artifacts WHERE run_id = :run_id"),
                    {"run_id": RUN_ID},
                )
            )
            .scalars()
            .all()
        )
        artifact_ids = [int(value) for value in artifact_rows if value is not None]
        deleted_spans = await connection.execute(
            sa.text("DELETE FROM native_trace_spans WHERE trace_id = :trace_id"),
            {"trace_id": TRACE_ID},
        )
        deleted_links = await connection.execute(
            sa.text("DELETE FROM worker_artifacts WHERE run_id = :run_id"),
            {"run_id": RUN_ID},
        )
        deleted_bindings = await connection.execute(
            sa.text(
                """DELETE FROM worker_run_host_bindings
                   WHERE run_id = :run_id OR assignment_key = :assignment_key"""
            ),
            {"run_id": RUN_ID, "assignment_key": ASSIGNMENT_KEY},
        )
        deleted_reservations = await connection.execute(
            sa.text(
                """DELETE FROM worker_host_reservations
                   WHERE id = :reservation_id OR reservation_key = :assignment_key"""
            ),
            {"reservation_id": RESERVATION_ID, "assignment_key": ASSIGNMENT_KEY},
        )
        deleted_runs = await connection.execute(
            sa.text("DELETE FROM worker_runs WHERE id = :run_id"),
            {"run_id": RUN_ID},
        )
        deleted_artifacts = 0
        if artifact_ids:
            result = await connection.execute(
                sa.text("DELETE FROM artifacts WHERE id = ANY(:ids)").bindparams(
                    sa.bindparam("ids", type_=sa.ARRAY(sa.BigInteger))
                ),
                {"ids": artifact_ids},
            )
            deleted_artifacts = int(result.rowcount or 0)
        deleted_hosts = await connection.execute(
            sa.text("DELETE FROM worker_hosts WHERE host_id LIKE :prefix"),
            {"prefix": HOST_PREFIX},
        )
        deleted_workers = await connection.execute(
            sa.text("DELETE FROM worker_registry WHERE name LIKE :prefix"),
            {"prefix": WORKER_PREFIX},
        )
    return {
        "spans": int(deleted_spans.rowcount or 0),
        "worker_artifacts": int(deleted_links.rowcount or 0),
        "bindings": int(deleted_bindings.rowcount or 0),
        "reservations": int(deleted_reservations.rowcount or 0),
        "worker_runs": int(deleted_runs.rowcount or 0),
        "artifacts": deleted_artifacts,
        "hosts": int(deleted_hosts.rowcount or 0),
        "workers": int(deleted_workers.rowcount or 0),
    }


async def _fixture_worker(request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={"result": "host-execution-complete", "private_marker": PAYLOAD_MARKER},
        artifacts=[
            WorkerArtifact(
                kind=ArtifactKind.REPORT,
                name="host-execution-fixture-report.json",
                uri="fixture://aiat/host-execution-v1/report.json",
                sha256="c" * 64,
                size_bytes=128,
                mime_type="application/json",
            )
        ],
        usage=WorkerUsage(
            prompt_tokens=7,
            completion_tokens=13,
            cost_usd=0.0042,
            duration_ms=3.0,
            cpu_seconds=0.01,
            memory_bytes=2048,
            provider="fixture-gateway",
            exact_model_id="fixture-model-v1",
        ),
        completion_criteria={"criterion": "committed-host-execution"},
    )


def _safe_blob(*values: Any) -> str:
    return json.dumps(values, default=str, sort_keys=True)


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_host_execution_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    mutation_performed = False
    first_counts: dict[str, int] = {}
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    execution_result: Any = None
    run_row: dict[str, Any] | None = None
    binding_before: dict[str, Any] | None = None
    binding_after: dict[str, Any] | None = None
    durable_run: dict[str, Any] | None = None
    durable_binding: dict[str, Any] | None = None
    durable_usage: list[dict[str, Any]] = []
    durable_artifacts: list[dict[str, Any]] = []
    durable_spans: list[dict[str, Any]] = []
    transition_count = 0
    event_count = 0
    reopened_healthy = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_host_execution_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }
        await _cleanup(storage)
        first_counts = await _counts(storage)
        registered_worker = await storage.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_REGISTRY_ID,
            adapter_type="native",
            adapter_config={"fixture": "host-execution"},
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1",
            source_repo="internal-fixture",
            source_revision="host-execution-v1",
            version_pin="fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="NativeWorkerAdapter",
            isolation_mode="native",
            model_mode="aiat_gateway",
        )
        canonical_worker_id = UUID(str(registered_worker["id"]))
        registry = WorkerHostRegistry(storage)
        registered_host = await registry.register_host(
            host_id=HOST_NAME,
            host_uuid=HOST_UUID,
            registration_token=TOKEN,
            labels={"pool": "worker"},
            capabilities=["native"],
            host_plane="worker",
            sandbox_profile="standard",
            isolation_mode="native",
            capacity={
                "slots_total": 1,
                "slots_used": 0,
                "memory_bytes_total": 1024 * 1024 * 1024,
                "memory_bytes_used": 0,
                "gpu_total": 0,
                "gpu_used": 0,
            },
            metadata={"fixture": "host-execution"},
        )
        await registry.heartbeat(
            host_id=HOST_NAME,
            registration_token=TOKEN,
            lease_generation=registered_host["lease_generation"],
            lease_seconds=120,
        )
        request = WorkerRunRequest(
            run_id=RUN_ID,
            idempotency_key=IDEMPOTENCY_KEY,
            worker_id=str(canonical_worker_id),
            task_type="host_execution_fixture",
            task_input={"private_marker": PAYLOAD_MARKER, "operation": "worker-plane"},
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
            timeout_seconds=30,
        )
        await storage.create_worker_run(
            run_id=RUN_ID,
            worker_id=canonical_worker_id,
            idempotency_key=IDEMPOTENCY_KEY,
            task_type=request.task_type,
            request=request.model_dump(mode="json"),
            state="QUEUED",
        )
        mutation_performed = True
        binding_service = WorkerRunHostBindingService(storage)
        binding_request = RunHostBindingRequest(
            run_id=RUN_ID,
            worker_id=canonical_worker_id,
            assignment_key=ASSIGNMENT_KEY,
            owner=OWNER,
            placement=WorkerPlacementRequest(
                worker_id=str(canonical_worker_id),
                required_host_plane="worker",
                required_capabilities=frozenset({"native"}),
                required_labels=(("pool", "worker"),),
                required_sandbox_profile="standard",
                required_isolation_mode="native",
                slots=1,
            ),
            lease_seconds=90,
            metadata={"fixture": "host-execution"},
            reservation_id=RESERVATION_ID,
        )
        await binding_service.assign(binding_request)
        binding_before = await binding_service.commit(RUN_ID, owner=OWNER)
        adapter = NativeWorkerAdapter(
            _fixture_worker,
            worker_id=str(canonical_worker_id),
            runtime_version="fixture-runtime-v1",
        )
        try:
            executor = WorkerHostExecutor(storage, binding_service=binding_service)
            execution_result = await executor.execute(
                HostExecutionRequest(
                    run_id=RUN_ID,
                    host_id=HOST_NAME,
                    owner=OWNER,
                    lease_seconds=30,
                ),
                request,
                adapter,
                worker_registry_id=canonical_worker_id,
            )
        finally:
            await adapter.close()
        binding_after = execution_result.binding_after
        await storage.create_native_trace_span(
            trace_id=TRACE_ID,
            span_id=WORKER_SPAN_ID,
            parent_span_id=SPAN_ID,
            source_kind="worker",
            operation="worker.execute",
            service="worker_host_executor",
            status="success" if execution_result.outcome.state == "SUCCEEDED" else "failure",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            attributes={"run_state": execution_result.outcome.state, "fixture": True},
        )
        run_row = await storage.get_worker_run(RUN_ID)
        transitions = await storage.list_worker_run_transitions(RUN_ID)
        events = await storage.list_worker_events(RUN_ID)
        transition_count = len(transitions)
        event_count = len(events)

        reopened = AgentStorage(normalized_dsn)
        await storage.close()
        try:
            await reopened.connect()
            reopened_healthy = True
            durable_run = await reopened.get_worker_run(RUN_ID)
            durable_binding = await WorkerRunHostBindingService(reopened).get(RUN_ID)
            durable_usage = await reopened.list_worker_usage(RUN_ID)
            durable_artifacts = await reopened.list_worker_artifacts(RUN_ID)
            durable_spans = await reopened.list_native_trace_spans_by_trace(TRACE_ID)
            cleanup_counts = await _cleanup(reopened)
            remaining = await _counts(reopened)
        finally:
            with suppress(Exception):
                await reopened.close()
    except Exception as exc:  # pragma: no cover - live environment diagnostic
        with suppress(Exception):
            await storage.close()
        return {
            **_blocked("worker_host_execution_checker_error"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "migration_version": migration_version,
            "mutation_performed": mutation_performed,
        }
    finally:
        if getattr(storage, "_engine", None) is not None:
            with suppress(Exception):
                await _cleanup(storage)
            with suppress(Exception):
                await storage.close()

    evidence = build_trace_evidence(
        trace_id=TRACE_ID,
        worker_usage_rows=durable_usage,
        artifact_rows=durable_artifacts,
        native_span_rows=durable_spans,
    )
    coverage = evaluate_worker_trace_coverage(evidence)
    payload_free = PAYLOAD_MARKER not in _safe_blob(evidence.model_dump(mode="json"), durable_spans)
    run_state = str((run_row or {}).get("state") or "unknown")
    claim_state = str(
        (execution_result.claimed if execution_result else {}).get("state") or "unknown"
    )
    passed = all(
        (
            migration_version == EXPECTED_MIGRATION,
            run_state == "SUCCEEDED",
            claim_state == "CLAIMED",
            binding_before is not None and binding_before.get("state") == "COMMITTED",
            binding_before is not None and binding_before.get("reservation_state") == "COMMITTED",
            binding_before is not None and binding_before.get("host_plane") == "worker",
            binding_before is not None and binding_before.get("current_host_lease_valid") is True,
            binding_after is not None and binding_after.get("state") == "RELEASED",
            binding_after is not None and binding_after.get("reservation_state") == "RELEASED",
            transition_count >= 6,
            event_count >= 2,
            len(durable_usage) == 1,
            len(durable_artifacts) == 1,
            len(durable_spans) >= 1,
            coverage["status"] == "pass",
            payload_free,
            reopened_healthy,
            durable_run is not None,
            durable_binding is not None and durable_binding.get("state") == "RELEASED",
            remaining
            == {
                "workers": 0,
                "runs": 0,
                "bindings": 0,
                "reservations": 0,
                "hosts": 0,
                "spans": 0,
            },
            sum(cleanup_counts.values()) >= 8,
        )
    )
    report = {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-host-execution",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "run_state": run_state,
        "claim_state": claim_state,
        "transition_count": transition_count,
        "event_count": event_count,
        "usage_count": len(durable_usage),
        "artifact_count": len(durable_artifacts),
        "native_span_count": len(durable_spans),
        "trace_item_count": evidence.item_count,
        "trace_source_counts": evidence.source_counts,
        "trace_coverage": coverage,
        "host_admission": {
            "host_id": HOST_NAME,
            "host_plane": binding_before.get("host_plane") if binding_before else None,
            "binding_state_before": binding_before.get("state") if binding_before else None,
            "reservation_state_before": binding_before.get("reservation_state")
            if binding_before
            else None,
            "assignment_lease_generation": binding_before.get("host_lease_generation")
            if binding_before
            else None,
            "current_lease_generation": binding_before.get("current_host_lease_generation")
            if binding_before
            else None,
            "current_host_lease_valid": binding_before.get("current_host_lease_valid")
            if binding_before
            else None,
            "binding_state_after": binding_after.get("state") if binding_after else None,
            "reservation_state_after": binding_after.get("reservation_state")
            if binding_after
            else None,
        },
        "durable_reopen": {
            "healthy": reopened_healthy,
            "run_present": durable_run is not None,
            "binding_state": durable_binding.get("state") if durable_binding else None,
            "usage_count": len(durable_usage),
            "artifact_count": len(durable_artifacts),
            "native_span_count": len(durable_spans),
        },
        "initial_fixture_counts": first_counts,
        "cleanup_deleted_counts": cleanup_counts,
        "remaining_fixture_counts": remaining,
        "payload_free": payload_free,
        "mutation_performed": mutation_performed,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": True,
        "scope": "committed AIAT worker-plane binding admission, Worker Run claim, native fixture execution, durable evidence, release, connection reopen, and scoped cleanup",
        "certification_boundary": {
            "committed_binding_admission": "checked",
            "worker_plane_and_host_lease_fencing": "checked",
            "worker_run_claim": "checked",
            "native_fixture_worker_dispatch": "checked",
            "durable_terminal_evidence": "checked",
            "binding_and_reservation_release": "checked",
            "postgres_connection_reopen": "checked",
            "payload_free_trace_projection": "checked",
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
            "external_provider_or_remote_runtime": "not_checked",
            "provider_backed_recovery": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"worker-host execution Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
