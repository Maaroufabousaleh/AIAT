"""Certify worker execution across independent Python processes.

The parent process creates two durable worker-host bindings, then launches two
separate Python child processes.  Each child reconnects to Postgres and uses the
production ``WorkerHostExecutor``/``WorkerRunController`` boundary to claim and
settle one run.  The parent reopens the store, verifies payload-free evidence,
and removes only the fixture namespace.

This is a local process-isolation certificate on one Compose host.  It does not
claim independent machines, Firecracker/gVisor, external providers, provider
outage recovery, or a production host-loss drill.  Licence metadata is not an
operational gate.
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

CHECK_SCHEMA = "aiat.worker-independent-process-execution-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
WORKER_NAME = "aiat-cert-independent-process-execution-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
HOST_PREFIX = "aiat-cert-independent-process-worker-v1-"
HOST_A = f"{HOST_PREFIX}a"
HOST_B = f"{HOST_PREFIX}b"
HOST_UUID_A = UUID("00000000-0000-4000-a000-000000000d31")
HOST_UUID_B = UUID("00000000-0000-4000-a000-000000000d32")
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000d33")
RUN_A = UUID("00000000-0000-4000-a000-000000000d34")
RUN_B = UUID("00000000-0000-4000-a000-000000000d35")
RESERVATION_A = UUID("00000000-0000-4000-a000-000000000d36")
RESERVATION_B = UUID("00000000-0000-4000-a000-000000000d37")
TRACE_A = "aiat-cert-independent-process-execution-v1-trace-a"
TRACE_B = "aiat-cert-independent-process-execution-v1-trace-b"
SPAN_A = "aiat-cert-independent-process-execution-v1-span-a"
SPAN_B = "aiat-cert-independent-process-execution-v1-span-b"
WORKER_SPAN_A = "aiat-cert-independent-process-execution-v1-worker-span-a"
WORKER_SPAN_B = "aiat-cert-independent-process-execution-v1-worker-span-b"
IDEMPOTENCY_A = "aiat-cert-independent-process-execution-v1-idempotency-a"
IDEMPOTENCY_B = "aiat-cert-independent-process-execution-v1-idempotency-b"
OWNER_A = "aiat-independent-process-owner-a"
OWNER_B = "aiat-independent-process-owner-b"
TOKEN_A = "aiat-independent-process-token-a-v1"
TOKEN_B = "aiat-independent-process-token-b-v1"
ASSIGNMENT_PREFIX = "aiat-cert-independent-process-execution-v1-assignment-"
ASSIGNMENT_A = f"{ASSIGNMENT_PREFIX}a"
ASSIGNMENT_B = f"{ASSIGNMENT_PREFIX}b"
PAYLOAD_MARKER = "aiat independent process fixture payload must never enter evidence"
RUN_IDS = (RUN_A, RUN_B)
TRACE_IDS = (TRACE_A, TRACE_B)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_INDEPENDENT_PROCESS_EXECUTION_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="worker Postgres DSN",
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--host-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--owner", default="", help=argparse.SUPPRESS)
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


def _blocked(reason: str, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-independent-worker-processes",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "process_dispatch_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "licence_metadata_is_gate": False,
    }
    report.update(details)
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
                sa.text("SELECT count(*) FROM worker_runs WHERE id IN (:run_a, :run_b)"),
                {"run_a": RUN_A, "run_b": RUN_B},
            ),
            "bindings": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM worker_run_host_bindings "
                    "WHERE assignment_key LIKE :prefix"
                ),
                {"prefix": f"{ASSIGNMENT_PREFIX}%"},
            ),
            "reservations": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM worker_host_reservations "
                    "WHERE reservation_key LIKE :prefix"
                ),
                {"prefix": f"{ASSIGNMENT_PREFIX}%"},
            ),
            "hosts": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_hosts WHERE host_id LIKE :prefix"),
                {"prefix": HOST_PREFIX},
            ),
            "usage": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_usage_records WHERE run_id IN (:run_a, :run_b)"),
                {"run_a": RUN_A, "run_b": RUN_B},
            ),
            "artifacts": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_artifacts WHERE run_id IN (:run_a, :run_b)"),
                {"run_a": RUN_A, "run_b": RUN_B},
            ),
            "spans": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM native_trace_spans "
                    "WHERE trace_id IN (:trace_a, :trace_b)"
                ),
                {"trace_a": TRACE_A, "trace_b": TRACE_B},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    """Delete only this checker namespace."""

    async with storage.engine.begin() as connection:
        artifact_rows = (
            await connection.execute(
                sa.text(
                    "SELECT artifact_id FROM worker_artifacts "
                    "WHERE run_id IN (:run_a, :run_b)"
                ),
                {"run_a": RUN_A, "run_b": RUN_B},
            )
        ).scalars().all()
        artifact_ids = [int(value) for value in artifact_rows if value is not None]
        deleted_spans = await connection.execute(
            sa.text(
                "DELETE FROM native_trace_spans WHERE trace_id IN (:trace_a, :trace_b)"
            ),
            {"trace_a": TRACE_A, "trace_b": TRACE_B},
        )
        deleted_links = await connection.execute(
            sa.text(
                "DELETE FROM worker_artifacts WHERE run_id IN (:run_a, :run_b)"
            ),
            {"run_a": RUN_A, "run_b": RUN_B},
        )
        deleted_bindings = await connection.execute(
            sa.text(
                """DELETE FROM worker_run_host_bindings
                   WHERE run_id IN (:run_a, :run_b)
                      OR assignment_key LIKE :assignment_prefix"""
            ),
            {
                "run_a": RUN_A,
                "run_b": RUN_B,
                "assignment_prefix": f"{ASSIGNMENT_PREFIX}%",
            },
        )
        deleted_reservations = await connection.execute(
            sa.text(
                """DELETE FROM worker_host_reservations
                   WHERE id IN (:reservation_a, :reservation_b)
                      OR reservation_key LIKE :assignment_prefix"""
            ),
            {
                "reservation_a": RESERVATION_A,
                "reservation_b": RESERVATION_B,
                "assignment_prefix": f"{ASSIGNMENT_PREFIX}%",
            },
        )
        deleted_runs = await connection.execute(
            sa.text("DELETE FROM worker_runs WHERE id IN (:run_a, :run_b)"),
            {"run_a": RUN_A, "run_b": RUN_B},
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


async def _fixture_worker(
    request: WorkerRunRequest,
    _adapter: NativeWorkerAdapter,
) -> WorkerResult:
    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={"result": "independent-process-complete", "private_marker": PAYLOAD_MARKER},
        artifacts=[
            WorkerArtifact(
                kind=ArtifactKind.REPORT,
                name=f"independent-process-{request.run_id}.json",
                uri=f"fixture://aiat/independent-process/{request.run_id}.json",
                sha256="f" * 64,
                size_bytes=96,
                mime_type="application/json",
            )
        ],
        usage=WorkerUsage(
            prompt_tokens=5,
            completion_tokens=8,
            cost_usd=0.0017,
            duration_ms=3.0,
            cpu_seconds=0.01,
            memory_bytes=1024,
            provider="fixture-process",
            exact_model_id="fixture-process-model-v1",
        ),
        completion_criteria={"criterion": "independent-process-execution"},
    )


def _request(*, run_id: UUID, idempotency_key: str, trace_id: str, span_id: str) -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=run_id,
        idempotency_key=idempotency_key,
        worker_id=str(WORKER_REGISTRY_ID),
        task_type="independent_process_execution_fixture",
        task_input={"private_marker": PAYLOAD_MARKER, "operation": "process-boundary"},
        trace_id=trace_id,
        span_id=span_id,
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
        "metadata": {"fixture": "independent-process-execution"},
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
    *,
    run_id: UUID,
    assignment_key: str,
    owner: str,
    reservation_id: UUID,
) -> RunHostBindingRequest:
    return RunHostBindingRequest(
        run_id=run_id,
        worker_id=WORKER_REGISTRY_ID,
        assignment_key=assignment_key,
        owner=owner,
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
        metadata={"fixture": "independent-process-execution"},
        reservation_id=reservation_id,
    )


async def _create_run(storage: AgentStorage, request: WorkerRunRequest) -> None:
    await storage.create_worker_run(
        run_id=request.run_id,
        worker_id=WORKER_REGISTRY_ID,
        idempotency_key=request.idempotency_key,
        task_type=request.task_type,
        request=request.model_dump(mode="json"),
        state="QUEUED",
    )


async def _child_run(*, run_id: UUID, host_id: str, owner: str) -> int:
    dsn = _normalize_dsn(os.getenv("AIAT_INDEPENDENT_PROCESS_DSN"))
    if dsn is None:
        print(json.dumps({"status": "blocked", "reason": "child_dsn_not_configured"}))
        return 2
    request_data = {
        RUN_A: (IDEMPOTENCY_A, TRACE_A, SPAN_A),
        RUN_B: (IDEMPOTENCY_B, TRACE_B, SPAN_B),
    }.get(run_id)
    if request_data is None:
        print(json.dumps({"status": "blocked", "reason": "child_run_not_in_fixture"}))
        return 2
    storage = AgentStorage(dsn)
    adapter: NativeWorkerAdapter | None = None
    try:
        await storage.connect()
        adapter = NativeWorkerAdapter(
            _fixture_worker,
            worker_id=str(WORKER_REGISTRY_ID),
            runtime_version="fixture-independent-process-v1",
        )
        result = await WorkerHostExecutor(storage).execute(
            HostExecutionRequest(
                run_id=run_id,
                host_id=host_id,
                owner=owner,
                lease_seconds=30,
            ),
            _request(
                run_id=run_id,
                idempotency_key=request_data[0],
                trace_id=request_data[1],
                span_id=request_data[2],
            ),
            adapter,
            worker_registry_id=WORKER_REGISTRY_ID,
        )
        print(
            json.dumps(
                {
                    "status": "pass" if result.outcome.state == "SUCCEEDED" else "fail",
                    "run_id": str(result.run_id),
                    "host_id": result.host_id,
                    "terminal_state": result.outcome.state,
                    "pid": os.getpid(),
                },
                sort_keys=True,
            )
        )
        return 0 if result.outcome.state == "SUCCEEDED" else 1
    except Exception as exc:  # pragma: no cover - child diagnostics
        print(
            json.dumps(
                {
                    "status": "fail",
                    "pid": os.getpid(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    finally:
        if adapter is not None:
            with suppress(Exception):
                await adapter.close()
        with suppress(Exception):
            await storage.close()


async def _spawn_child(
    *,
    dsn: str,
    run_id: UUID,
    host_id: str,
    owner: str,
) -> dict[str, Any]:
    child_env = os.environ.copy()
    child_env["AIAT_INDEPENDENT_PROCESS_DSN"] = dsn
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--run-id",
        str(run_id),
        "--host-id",
        host_id,
        "--owner",
        owner,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return {
            "status": "fail",
            "pid": process.pid,
            "return_code": process.returncode,
            "error_type": "TimeoutError",
        }
    lines = [line.strip() for line in stdout.decode(errors="replace").splitlines() if line.strip()]
    parsed: dict[str, Any] = {}
    if lines:
        try:
            candidate = json.loads(lines[-1])
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            parsed = {}
    if process.returncode != 0:
        parsed.setdefault("status", "fail")
        parsed.setdefault("error_type", "ChildProcessError")
    parsed["pid"] = int(parsed.get("pid") or process.pid or 0)
    parsed["return_code"] = int(process.returncode or 0)
    if stderr:
        parsed["stderr_present"] = True
    return parsed


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_independent_process_execution_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    mutation_performed = False
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    durable_runs: list[dict[str, Any]] = []
    durable_bindings: list[dict[str, Any] | None] = []
    durable_usage: list[dict[str, Any]] = []
    durable_artifacts: list[dict[str, Any]] = []
    durable_spans: dict[str, list[dict[str, Any]]] = {TRACE_A: [], TRACE_B: []}
    child_reports: list[dict[str, Any]] = []
    reopened_healthy = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return _blocked(
                "worker_independent_process_execution_migration_not_at_head",
                migration_version=migration_version,
                expected_migration=EXPECTED_MIGRATION,
                local_database_access_performed=True,
            )
        await _cleanup(storage)
        registered_worker = await storage.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_REGISTRY_ID,
            adapter_type="native",
            adapter_config={"fixture": "independent-process-execution"},
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1",
            source_repo="internal-fixture",
            source_revision="independent-process-v1",
            version_pin="fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="NativeWorkerAdapter",
            isolation_mode="native",
            model_mode="none",
        )
        canonical_worker_id = UUID(str(registered_worker["id"]))
        if canonical_worker_id != WORKER_REGISTRY_ID:
            return _blocked(
                "worker_independent_process_fixture_worker_identity_mismatch",
                local_database_access_performed=True,
            )
        await _register_hosts(WorkerHostRegistry(storage))
        request_a = _request(
            run_id=RUN_A,
            idempotency_key=IDEMPOTENCY_A,
            trace_id=TRACE_A,
            span_id=SPAN_A,
        )
        request_b = _request(
            run_id=RUN_B,
            idempotency_key=IDEMPOTENCY_B,
            trace_id=TRACE_B,
            span_id=SPAN_B,
        )
        await _create_run(storage, request_a)
        await _create_run(storage, request_b)
        binding_service = WorkerRunHostBindingService(storage)
        await binding_service.assign(
            _binding_request(
                run_id=RUN_A,
                assignment_key=ASSIGNMENT_A,
                owner=OWNER_A,
                reservation_id=RESERVATION_A,
            )
        )
        await binding_service.commit(RUN_A, owner=OWNER_A)
        await binding_service.assign(
            _binding_request(
                run_id=RUN_B,
                assignment_key=ASSIGNMENT_B,
                owner=OWNER_B,
                reservation_id=RESERVATION_B,
            )
        )
        await binding_service.commit(RUN_B, owner=OWNER_B)
        mutation_performed = True
        child_reports = list(
            await asyncio.gather(
                _spawn_child(
                    dsn=normalized_dsn,
                    run_id=RUN_A,
                    host_id=HOST_A,
                    owner=OWNER_A,
                ),
                _spawn_child(
                    dsn=normalized_dsn,
                    run_id=RUN_B,
                    host_id=HOST_B,
                    owner=OWNER_B,
                ),
            )
        )
        for trace_id, span_id, report in (
            (TRACE_A, WORKER_SPAN_A, child_reports[0]),
            (TRACE_B, WORKER_SPAN_B, child_reports[1]),
        ):
            await storage.create_native_trace_span(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=SPAN_A if trace_id == TRACE_A else SPAN_B,
                source_kind="worker",
                operation="worker.execute.independent_process",
                service="worker_host_executor_process",
                status="success" if report.get("status") == "pass" else "failure",
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                attributes={
                    "run_state": report.get("terminal_state"),
                    "host_id": report.get("host_id"),
                    "process_id": report.get("pid"),
                },
            )
        await storage.close()
        reopened = AgentStorage(normalized_dsn)
        try:
            await reopened.connect()
            reopened_healthy = True
            durable_runs = [
                await reopened.get_worker_run(RUN_A) or {},
                await reopened.get_worker_run(RUN_B) or {},
            ]
            reopened_bindings = WorkerRunHostBindingService(reopened)
            durable_bindings = [
                await reopened_bindings.get(RUN_A),
                await reopened_bindings.get(RUN_B),
            ]
            durable_usage = [
                *await reopened.list_worker_usage(RUN_A),
                *await reopened.list_worker_usage(RUN_B),
            ]
            durable_artifacts = [
                *await reopened.list_worker_artifacts(RUN_A),
                *await reopened.list_worker_artifacts(RUN_B),
            ]
            for trace_id in TRACE_IDS:
                durable_spans[trace_id] = await reopened.list_native_trace_spans_by_trace(trace_id)
            cleanup_counts = await _cleanup(reopened)
            remaining = await _counts(reopened)
        finally:
            with suppress(Exception):
                await reopened.close()
    except Exception as exc:  # pragma: no cover - live environment diagnostic
        return _blocked(
            "worker_independent_process_execution_checker_error",
            error_type=type(exc).__name__,
            error=str(exc),
            migration_version=migration_version,
            mutation_performed=mutation_performed,
            local_database_access_performed=True,
            process_dispatch_performed=bool(child_reports),
        )
    finally:
        if getattr(storage, "_engine", None) is not None:
            with suppress(Exception):
                await _cleanup(storage)
            with suppress(Exception):
                await storage.close()

    coverage: dict[str, dict[str, Any]] = {}
    payload_free = True
    for trace_id in TRACE_IDS:
        trace_usage = [row for row in durable_usage if str(row.get("trace_id") or "") == trace_id]
        trace_artifacts = [row for row in durable_artifacts if str(row.get("trace_id") or "") == trace_id]
        evidence = build_trace_evidence(
            trace_id=trace_id,
            worker_usage_rows=trace_usage,
            artifact_rows=trace_artifacts,
            native_span_rows=durable_spans[trace_id],
        )
        coverage[trace_id] = evaluate_worker_trace_coverage(evidence)
        payload_free = payload_free and PAYLOAD_MARKER not in json.dumps(
            (evidence.model_dump(mode="json"), durable_spans[trace_id]),
            default=str,
            sort_keys=True,
        )

    run_states = [str(row.get("state") or "unknown") for row in durable_runs]
    assigned_hosts = [str((binding or {}).get("host_id") or "") for binding in durable_bindings]
    binding_states = [str((binding or {}).get("state") or "unknown") for binding in durable_bindings]
    reservation_states = [
        str((binding or {}).get("reservation_state") or "unknown")
        for binding in durable_bindings
    ]
    pids = [int(report.get("pid") or 0) for report in child_reports]
    passed = all(
        (
            migration_version == EXPECTED_MIGRATION,
            len(child_reports) == 2,
            all(report.get("status") == "pass" for report in child_reports),
            all(int(report.get("return_code") or 1) == 0 for report in child_reports),
            len(set(pids)) == 2 and all(pid > 0 for pid in pids),
            run_states == ["SUCCEEDED", "SUCCEEDED"],
            assigned_hosts == [HOST_A, HOST_B],
            binding_states == ["RELEASED", "RELEASED"],
            reservation_states == ["RELEASED", "RELEASED"],
            len(durable_usage) == 2,
            len(durable_artifacts) == 2,
            all(len(durable_spans[trace_id]) >= 3 for trace_id in TRACE_IDS),
            all(value.get("status") == "pass" for value in coverage.values()),
            payload_free,
            reopened_healthy,
            remaining
            == {
                "workers": 0,
                "runs": 0,
                "bindings": 0,
                "reservations": 0,
                "hosts": 0,
                "usage": 0,
                "artifacts": 0,
                "spans": 0,
            },
            sum(cleanup_counts.values()) >= 12,
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-independent-worker-processes",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "controller_terminal_states": run_states,
        "child_process_count": len(child_reports),
        "child_process_ids": pids,
        "child_reports": [
            {
                "status": report.get("status"),
                "run_id": report.get("run_id"),
                "host_id": report.get("host_id"),
                "terminal_state": report.get("terminal_state"),
                "return_code": report.get("return_code"),
            }
            for report in child_reports
        ],
        "worker_usage_count": len(durable_usage),
        "worker_artifact_count": len(durable_artifacts),
        "native_span_counts": {trace_id: len(durable_spans[trace_id]) for trace_id in TRACE_IDS},
        "payload_free": payload_free,
        "trace_coverage": coverage,
        "durable_reopen": {
            "healthy": reopened_healthy,
            "run_count": len(durable_runs),
            "usage_count": len(durable_usage),
            "artifact_count": len(durable_artifacts),
            "trace_counts": {trace_id: len(durable_spans[trace_id]) for trace_id in TRACE_IDS},
        },
        "cleanup_deleted_counts": cleanup_counts,
        "remaining_fixture_counts": remaining,
        "mutation_performed": True,
        "local_database_access_performed": True,
        "process_dispatch_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "certification_boundary": {
            "independent_python_processes": "checked",
            "durable_worker_host_bindings": "checked",
            "production_worker_host_executor": "checked",
            "production_worker_run_controller": "checked",
            "worker_postgres_connection_reopen": "checked",
            "payload_free_trace_projection": "checked",
            "scoped_cleanup": "checked",
            "independent_deployed_hosts": "not_checked",
            "host_loss_or_split_brain": "not_checked",
            "external_provider_dispatch": "not_checked",
            "provider_outage_recovery": "not_checked",
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.child:
        try:
            run_id = UUID(str(args.run_id))
        except (TypeError, ValueError):
            print(json.dumps({"status": "blocked", "reason": "child_run_id_invalid"}))
            return 2
        return asyncio.run(
            _child_run(run_id=run_id, host_id=args.host_id, owner=args.owner)
        )
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"independent worker process Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
