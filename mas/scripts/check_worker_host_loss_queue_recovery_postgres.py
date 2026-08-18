"""Certify queue recovery after a fenced worker host is lost.

This probe starts one run on a committed worker-host binding, expires both the
host lease and the Worker Run claim lease, reconciles the host to ``OFFLINE``,
requeues the run through the canonical storage recovery loop, rejects a stale
executor attempt, reassigns the queued run to a second worker host, and
executes the retry through ``WorkerHostExecutor``.  It proves AIAT-owned
fencing and queue/reassignment semantics only; independent machines, gVisor,
Firecracker, external providers, and provider-backed recovery are not claimed.
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
    WorkerHostExecutionRejected,
    WorkerHostExecutor,
)
from mas_core.worker_registry.host_recovery import (  # noqa: E402
    HOST_RECOVERY_SCHEMA,
    HostLeaseRecovery,
)
from mas_core.worker_registry.host_registry import WorkerHostRegistry  # noqa: E402
from mas_core.worker_registry.placement import WorkerPlacementRequest  # noqa: E402
from mas_core.worker_registry.run_host_binding import (  # noqa: E402
    RUN_HOST_BINDING_SCHEMA,
    RUN_HOST_RECOVERY_SCHEMA,
    RunHostBindingRequest,
    WorkerRunHostBindingService,
)

CHECK_SCHEMA = "aiat.worker-host-loss-queue-recovery-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
WORKER_NAME = "aiat-cert-host-loss-queue-recovery-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
HOST_PREFIX = "aiat-cert-host-loss-queue-recovery-worker-v1-"
HOST_A = f"{HOST_PREFIX}a"
HOST_B = f"{HOST_PREFIX}b"
HOST_UUID_A = UUID("00000000-0000-4000-a000-000000000d41")
HOST_UUID_B = UUID("00000000-0000-4000-a000-000000000d42")
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000d43")
RUN_ID = UUID("00000000-0000-4000-a000-000000000d44")
RESERVATION_A = UUID("00000000-0000-4000-a000-000000000d45")
RESERVATION_B = UUID("00000000-0000-4000-a000-000000000d46")
TOKEN_A = "aiat-host-loss-queue-recovery-token-a-v1"
TOKEN_B = "aiat-host-loss-queue-recovery-token-b-v1"
OWNER = "aiat-host-loss-queue-recovery-owner-v1"
ASSIGNMENT_A = "aiat-cert-host-loss-queue-recovery-v1-assignment-a"
ASSIGNMENT_B = "aiat-cert-host-loss-queue-recovery-v1-assignment-b"
IDEMPOTENCY_KEY = "aiat-cert-host-loss-queue-recovery-v1-idempotency"
TRACE_ID = "aiat-cert-host-loss-queue-recovery-v1-trace"
SPAN_ID = "aiat-cert-host-loss-queue-recovery-v1-span"
WORKER_SPAN_ID = "aiat-cert-host-loss-queue-recovery-v1-worker-span"
PAYLOAD_MARKER = "aiat host loss recovery fixture payload must never enter evidence"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_LOSS_QUEUE_RECOVERY_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_HOST_LOSS_QUEUE_RECOVERY_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "binding_recovery_schema": RUN_HOST_RECOVERY_SCHEMA,
        "host_recovery_schema": HOST_RECOVERY_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-host-loss-queue-recovery",
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
                sa.text("SELECT count(*) FROM worker_run_host_bindings WHERE run_id = :run_id"),
                {"run_id": RUN_ID},
            ),
            "reservations": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM worker_host_reservations WHERE id IN (:reservation_a, :reservation_b)"
                ),
                {"reservation_a": RESERVATION_A, "reservation_b": RESERVATION_B},
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
    async with storage.engine.begin() as connection:
        artifact_rows = (
            await connection.execute(
                sa.text("SELECT artifact_id FROM worker_artifacts WHERE run_id = :run_id"),
                {"run_id": RUN_ID},
            )
        ).scalars().all()
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
            sa.text("DELETE FROM worker_run_host_bindings WHERE run_id = :run_id"),
            {"run_id": RUN_ID},
        )
        deleted_reservations = await connection.execute(
            sa.text(
                "DELETE FROM worker_host_reservations WHERE id IN (:reservation_a, :reservation_b)"
            ),
            {"reservation_a": RESERVATION_A, "reservation_b": RESERVATION_B},
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


async def _fixture_worker(request: WorkerRunRequest, _adapter: Any) -> WorkerResult:
    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={"result": "host-loss-recovered", "private_marker": PAYLOAD_MARKER},
        artifacts=[
            WorkerArtifact(
                kind=ArtifactKind.REPORT,
                name=f"host-loss-recovered-{request.run_id}.json",
                uri=f"fixture://aiat/host-loss-recovered/{request.run_id}.json",
                sha256="e" * 64,
                size_bytes=104,
                mime_type="application/json",
            )
        ],
        usage=WorkerUsage(
            prompt_tokens=6,
            completion_tokens=11,
            cost_usd=0.0025,
            duration_ms=2.0,
            cpu_seconds=0.01,
            memory_bytes=1024,
            provider="fixture-gateway",
            exact_model_id="fixture-model-v1",
        ),
        completion_criteria={"criterion": "host-loss-queue-recovery"},
    )


def _request() -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        worker_id=str(WORKER_REGISTRY_ID),
        task_type="host_loss_queue_recovery_fixture",
        task_input={"private_marker": PAYLOAD_MARKER, "operation": "recover-and-retry"},
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
        timeout_seconds=30,
    )


async def _register_hosts(registry: WorkerHostRegistry) -> None:
    common = {
        "labels": {"pool": "worker"},
        "capabilities": ["native"],
        "host_plane": "worker",
        "sandbox_profile": "standard",
        "isolation_mode": "native",
        "capacity": {
            "slots_total": 1,
            "slots_used": 0,
            "memory_bytes_total": 1024 * 1024 * 1024,
            "memory_bytes_used": 0,
            "gpu_total": 0,
            "gpu_used": 0,
        },
        "metadata": {"fixture": "host-loss-queue-recovery"},
    }
    registered_a = await registry.register_host(
        host_id=HOST_A,
        host_uuid=HOST_UUID_A,
        registration_token=TOKEN_A,
        priority=2,
        **common,
    )
    registered_b = await registry.register_host(
        host_id=HOST_B,
        host_uuid=HOST_UUID_B,
        registration_token=TOKEN_B,
        priority=1,
        **common,
    )
    await registry.heartbeat(
        host_id=HOST_A,
        registration_token=TOKEN_A,
        lease_generation=registered_a["lease_generation"],
        lease_seconds=120,
    )
    await registry.heartbeat(
        host_id=HOST_B,
        registration_token=TOKEN_B,
        lease_generation=registered_b["lease_generation"],
        lease_seconds=120,
    )


def _binding_request(
    *, assignment_key: str, reservation_id: UUID
) -> RunHostBindingRequest:
    return RunHostBindingRequest(
        run_id=RUN_ID,
        worker_id=WORKER_REGISTRY_ID,
        assignment_key=assignment_key,
        owner=OWNER,
        placement=WorkerPlacementRequest(
            worker_id=str(WORKER_REGISTRY_ID),
            required_host_plane="worker",
            required_capabilities=frozenset({"native"}),
            required_labels=(("pool", "worker"),),
            required_sandbox_profile="standard",
            required_isolation_mode="native",
            slots=1,
        ),
        lease_seconds=90,
        metadata={"fixture": "host-loss-queue-recovery"},
        reservation_id=reservation_id,
    )


async def _expire_host_and_run(storage: AgentStorage) -> None:
    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text(
                """UPDATE worker_hosts
                   SET lease_expires_at = now() - interval '1 second'
                   WHERE id = :host_id AND status = 'READY'"""
            ),
            {"host_id": HOST_UUID_A},
        )
        if result.rowcount != 1:
            raise RuntimeError("host-loss fixture was not READY before expiry")
        run_result = await connection.execute(
            sa.text(
                """UPDATE worker_runs
                   SET lease_expires_at = now() - interval '1 second'
                   WHERE id = :run_id AND state = 'CLAIMED'"""
            ),
            {"run_id": RUN_ID},
        )
        if run_result.rowcount != 1:
            raise RuntimeError("host-loss fixture run was not CLAIMED before expiry")


async def _create_run(storage: AgentStorage, request: WorkerRunRequest) -> None:
    await storage.create_worker_run(
        run_id=request.run_id,
        worker_id=WORKER_REGISTRY_ID,
        idempotency_key=request.idempotency_key,
        task_type=request.task_type,
        request=request.model_dump(mode="json"),
        state="QUEUED",
    )


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_host_loss_queue_recovery_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    mutation_performed = False
    first_counts: dict[str, int] = {}
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    stale_binding: dict[str, Any] | None = None
    reassigned_binding: dict[str, Any] | None = None
    execution_result: Any = None
    durable_run: dict[str, Any] | None = None
    durable_binding: dict[str, Any] | None = None
    durable_usage: list[dict[str, Any]] = []
    durable_artifacts: list[dict[str, Any]] = []
    durable_spans: list[dict[str, Any]] = []
    transition_count = 0
    event_count = 0
    stale_executor_reason: str | None = None
    stale_run_state: str | None = None
    host_recovery_report: dict[str, Any] = {}
    run_recovery_rows: list[dict[str, Any]] = []
    reopened_healthy = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_host_loss_queue_recovery_migration_not_at_head",
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
            adapter_config={"fixture": "host-loss-queue-recovery"},
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1",
            source_repo="internal-fixture",
            source_revision="host-loss-queue-recovery-v1",
            version_pin="fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="NativeWorkerAdapter",
            isolation_mode="native",
            model_mode="none",
        )
        canonical_worker_id = UUID(str(registered_worker["id"]))
        await _register_hosts(WorkerHostRegistry(storage))
        request = _request()
        await _create_run(storage, request)
        mutation_performed = True
        binding_service = WorkerRunHostBindingService(storage)
        await binding_service.assign(
            _binding_request(assignment_key=ASSIGNMENT_A, reservation_id=RESERVATION_A)
        )
        await binding_service.commit(RUN_ID, owner=OWNER)
        claimed_before_loss = await storage.claim_worker_run(
            owner=OWNER,
            lease_seconds=30,
            run_id=RUN_ID,
        )
        if claimed_before_loss is None or claimed_before_loss.get("state") != "CLAIMED":
            raise RuntimeError("host-loss fixture run was not claimable")
        await _expire_host_and_run(storage)
        host_recovery_report = await HostLeaseRecovery(storage).reconcile_expired_hosts(
            host_ids=[HOST_A]
        )
        run_recovery_rows = await storage.recover_expired_worker_runs()
        stale_binding = await binding_service.get(RUN_ID)
        adapter = NativeWorkerAdapter(
            _fixture_worker,
            worker_id=str(canonical_worker_id),
            runtime_version="fixture-runtime-v1",
        )
        try:
            try:
                await WorkerHostExecutor(storage, binding_service=binding_service).execute(
                    HostExecutionRequest(
                        run_id=RUN_ID,
                        host_id=HOST_A,
                        owner=OWNER,
                        lease_seconds=30,
                    ),
                    request,
                    adapter,
                    worker_registry_id=canonical_worker_id,
                )
            except WorkerHostExecutionRejected as exc:
                stale_executor_reason = exc.reason_code
            stale_run = await storage.get_worker_run(RUN_ID)
            stale_run_state = str((stale_run or {}).get("state") or "unknown")
        finally:
            await adapter.close()
        reassigned_binding = await binding_service.reassign_after_host_loss(
            _binding_request(assignment_key=ASSIGNMENT_B, reservation_id=RESERVATION_B),
            recovery_reason="host_lease_expired_and_worker_claim_requeued",
        )
        retry_adapter = NativeWorkerAdapter(
            _fixture_worker,
            worker_id=str(canonical_worker_id),
            runtime_version="fixture-runtime-v1",
        )
        try:
            execution_result = await WorkerHostExecutor(
                storage, binding_service=binding_service
            ).execute(
                HostExecutionRequest(
                    run_id=RUN_ID,
                    host_id=HOST_B,
                    owner=OWNER,
                    lease_seconds=30,
                ),
                request,
                retry_adapter,
                worker_registry_id=canonical_worker_id,
            )
        finally:
            await retry_adapter.close()
        await storage.create_native_trace_span(
            trace_id=TRACE_ID,
            span_id=WORKER_SPAN_ID,
            parent_span_id=SPAN_ID,
            source_kind="worker",
            operation="worker.execute.recovered",
            service="worker_host_executor",
            status="success" if execution_result.outcome.state == "SUCCEEDED" else "failure",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            attributes={
                "run_state": execution_result.outcome.state,
                "host_id": execution_result.host_id,
                "recovered": True,
            },
        )
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
            reopened_bindings = WorkerRunHostBindingService(reopened)
            durable_binding = await reopened_bindings.get(RUN_ID)
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
            **_blocked("worker_host_loss_queue_recovery_checker_error"),
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
    payload_free = PAYLOAD_MARKER not in json.dumps(
        (evidence.model_dump(mode="json"), durable_spans),
        default=str,
        sort_keys=True,
    )
    run_state = str((durable_run or {}).get("state") or "unknown")
    recovered_fixture_hosts = [
        item
        for item in host_recovery_report.get("hosts", [])
        if item.get("host_id") == HOST_A
    ]
    passed = all(
        (
            migration_version == EXPECTED_MIGRATION,
            host_recovery_report.get("status") == "RECOVERED",
            host_recovery_report.get("recovered_host_count") == 1,
            len(recovered_fixture_hosts) == 1,
            recovered_fixture_hosts[0].get("expired_reservation_count") == 1,
            stale_binding is not None
            and stale_binding.get("host_id") == HOST_A
            and stale_binding.get("reservation_state") == "EXPIRED"
            and stale_binding.get("current_host_lease_valid") is False,
            len(run_recovery_rows) == 1
            and run_recovery_rows[0].get("state") == "QUEUED",
            stale_executor_reason == "run_host_reservation_not_committed",
            stale_run_state == "QUEUED",
            reassigned_binding is not None
            and reassigned_binding.get("host_id") == HOST_B
            and reassigned_binding.get("state") == "COMMITTED"
            and reassigned_binding.get("reservation_state") == "COMMITTED",
            execution_result is not None
            and execution_result.host_id == HOST_B
            and execution_result.claimed.get("state") == "CLAIMED",
            run_state == "SUCCEEDED",
            durable_binding is not None
            and durable_binding.get("host_id") == HOST_B
            and durable_binding.get("state") == "RELEASED"
            and durable_binding.get("reservation_state") == "RELEASED",
            int((durable_run or {}).get("attempt_count") or 0) == 2,
            transition_count >= 8,
            event_count >= 2,
            len(durable_usage) == 1,
            len(durable_artifacts) == 1,
            len(durable_spans) >= 3,
            coverage.get("status") == "pass",
            payload_free,
            reopened_healthy,
            remaining
            == {
                "workers": 0,
                "runs": 0,
                "bindings": 0,
                "reservations": 0,
                "hosts": 0,
                "spans": 0,
            },
            sum(cleanup_counts.values()) >= 10,
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "binding_recovery_schema": RUN_HOST_RECOVERY_SCHEMA,
        "host_recovery_schema": HOST_RECOVERY_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-host-loss-queue-recovery",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "host_ids": [HOST_A, HOST_B],
        "host_recovery": {
            "status": host_recovery_report.get("status"),
            "recovered_host_count": host_recovery_report.get("recovered_host_count"),
            "expired_reservation_count": host_recovery_report.get(
                "expired_reservation_count"
            ),
            "host_filter": host_recovery_report.get("host_filter"),
            "lost_host_id": HOST_A,
            "lost_host_status_after": stale_binding.get("host_status")
            if stale_binding
            else None,
        },
        "worker_run_recovery": {
            "claimed_before_loss": claimed_before_loss.get("state")
            if claimed_before_loss
            else None,
            "requeued_count": len(run_recovery_rows),
            "stale_executor_rejection": stale_executor_reason,
            "requeued_state_before_reassignment": stale_run_state,
            "reassigned_host_id": reassigned_binding.get("host_id")
            if reassigned_binding
            else None,
            "reassigned_binding_state": reassigned_binding.get("state")
            if reassigned_binding
            else None,
            "retry_claim_state": execution_result.claimed.get("state")
            if execution_result
            else None,
            "attempt_count": int((durable_run or {}).get("attempt_count") or 0),
        },
        "run_state": run_state,
        "binding_state": durable_binding.get("state") if durable_binding else None,
        "reservation_state": durable_binding.get("reservation_state")
        if durable_binding
        else None,
        "transition_count": transition_count,
        "event_count": event_count,
        "usage_count": len(durable_usage),
        "artifact_count": len(durable_artifacts),
        "native_span_count": len(durable_spans),
        "trace_coverage": coverage,
        "durable_reopen": {
            "healthy": reopened_healthy,
            "run_present": durable_run is not None,
            "binding_host_id": durable_binding.get("host_id")
            if durable_binding
            else None,
            "binding_state": durable_binding.get("state")
            if durable_binding
            else None,
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
        "scope": "fenced host loss, canonical Worker Run requeue, stale executor rejection, alternate-host reassignment, native retry, durable reopen, and scoped cleanup",
        "certification_boundary": {
            "host_lease_fencing": "checked",
            "worker_run_claim_lease_requeue": "checked",
            "stale_lost_host_execution_rejection": "checked",
            "durable_binding_reassignment": "checked",
            "alternate_worker_host_retry": "checked",
            "payload_free_usage_artifact_trace_evidence": "checked",
            "postgres_connection_reopen": "checked",
            "independent_deployed_hosts": "not_checked",
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
            "external_provider_or_remote_runtime": "not_checked",
            "provider_backed_recovery": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"worker host-loss queue recovery Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
