"""Certify concurrent native Worker Run execution across two worker hosts.

This probe extends the committed-binding host executor with a deterministic
two-host exercise. It reserves two worker-plane hosts, binds two queued runs,
executes them concurrently through ``WorkerHostExecutor``, races a duplicate
host claim for one run, and replays both the terminal and alias idempotency
requests without redispatch. It reopens Postgres and removes only its fixture
namespace. It proves multi-host native adapter execution, host-specific lease
admission, and bounded duplicate-effect protection; it does not claim gVisor,
Firecracker, provider, remote-runtime, independent-machine, or outage-recovery
evidence.
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
    WorkerHostExecutionResult,
    WorkerHostExecutor,
)
from mas_core.worker_registry.host_registry import WorkerHostRegistry  # noqa: E402
from mas_core.worker_registry.placement import WorkerPlacementRequest  # noqa: E402
from mas_core.worker_registry.run_host_binding import (  # noqa: E402
    RUN_HOST_BINDING_SCHEMA,
    RunHostBindingRequest,
    WorkerRunHostBindingService,
)

CHECK_SCHEMA = "aiat.worker-multi-host-execution-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
WORKER_NAME = "aiat-cert-multi-host-execution-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
HOST_PREFIX = "aiat-cert-multi-host-execution-worker-v1-"
HOST_A = f"{HOST_PREFIX}a"
HOST_B = f"{HOST_PREFIX}b"
HOST_UUID_A = UUID("00000000-0000-4000-a000-000000000c31")
HOST_UUID_B = UUID("00000000-0000-4000-a000-000000000c32")
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000c33")
RUN_A = UUID("00000000-0000-4000-a000-000000000c34")
RUN_B = UUID("00000000-0000-4000-a000-000000000c35")
ALIAS_RUN = UUID("00000000-0000-4000-a000-000000000c38")
RESERVATION_A = UUID("00000000-0000-4000-a000-000000000c36")
RESERVATION_B = UUID("00000000-0000-4000-a000-000000000c37")
TOKEN_A = "aiat-multi-host-execution-token-a-v1"
TOKEN_B = "aiat-multi-host-execution-token-b-v1"
OWNER_A = "aiat-multi-host-execution-owner-a"
OWNER_B = "aiat-multi-host-execution-owner-b"
ASSIGNMENT_PREFIX = "aiat-cert-multi-host-execution-v1-assignment-"
ASSIGNMENT_A = f"{ASSIGNMENT_PREFIX}a"
ASSIGNMENT_B = f"{ASSIGNMENT_PREFIX}b"
TRACE_A = "aiat-cert-multi-host-execution-v1-trace-a"
TRACE_B = "aiat-cert-multi-host-execution-v1-trace-b"
SPAN_A = "aiat-cert-multi-host-execution-v1-span-a"
SPAN_B = "aiat-cert-multi-host-execution-v1-span-b"
WORKER_SPAN_A = "aiat-cert-multi-host-execution-v1-worker-span-a"
WORKER_SPAN_B = "aiat-cert-multi-host-execution-v1-worker-span-b"
IDEMPOTENCY_A = "aiat-cert-multi-host-execution-v1-idempotency-a"
IDEMPOTENCY_B = "aiat-cert-multi-host-execution-v1-idempotency-b"
PAYLOAD_MARKER = "aiat multi-host fixture payload must never enter the evidence report"
RUN_IDS = (RUN_A, RUN_B)
TRACE_IDS = (TRACE_A, TRACE_B)

_DISPATCH_COUNT = 0
_RUN_A_STARTED: asyncio.Event | None = None
_RUN_A_RELEASE: asyncio.Event | None = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_MULTI_HOST_EXECUTION_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_MULTI_HOST_EXECUTION_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-multi-host-execution",
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
                sa.text("SELECT count(*) FROM worker_runs WHERE id IN (:run_a, :run_b)"),
                {"run_a": RUN_A, "run_b": RUN_B},
            ),
            "bindings": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_run_host_bindings WHERE assignment_key LIKE :prefix"),
                {"prefix": f"{ASSIGNMENT_PREFIX}%"},
            ),
            "reservations": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_host_reservations WHERE reservation_key LIKE :prefix"),
                {"prefix": f"{ASSIGNMENT_PREFIX}%"},
            ),
            "hosts": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_hosts WHERE host_id LIKE :prefix"),
                {"prefix": HOST_PREFIX},
            ),
            "spans": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM native_trace_spans WHERE trace_id IN (:trace_a, :trace_b)"
                ),
                {"trace_a": TRACE_A, "trace_b": TRACE_B},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    """Delete only rows owned by the two-run fixture namespace."""

    async with storage.engine.begin() as connection:
        artifact_rows = (
            await connection.execute(
                sa.text(
                    "SELECT artifact_id FROM worker_artifacts WHERE run_id IN (:run_a, :run_b)"
                ),
                {"run_a": RUN_A, "run_b": RUN_B},
            )
        ).scalars().all()
        artifact_ids = [int(value) for value in artifact_rows if value is not None]
        deleted_spans = await connection.execute(
            sa.text("DELETE FROM native_trace_spans WHERE trace_id IN (:trace_a, :trace_b)"),
            {"trace_a": TRACE_A, "trace_b": TRACE_B},
        )
        deleted_links = await connection.execute(
            sa.text("DELETE FROM worker_artifacts WHERE run_id IN (:run_a, :run_b)"),
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


async def _fixture_worker(request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
    global _DISPATCH_COUNT
    _DISPATCH_COUNT += 1
    if request.run_id == RUN_A and _RUN_A_STARTED is not None:
        _RUN_A_STARTED.set()
        if _RUN_A_RELEASE is not None:
            await _RUN_A_RELEASE.wait()
    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={"result": "multi-host-complete", "private_marker": PAYLOAD_MARKER},
        artifacts=[
            WorkerArtifact(
                kind=ArtifactKind.REPORT,
                name=f"multi-host-{request.run_id}.json",
                uri=f"fixture://aiat/multi-host/{request.run_id}.json",
                sha256="d" * 64,
                size_bytes=96,
                mime_type="application/json",
            )
        ],
        usage=WorkerUsage(
            prompt_tokens=5,
            completion_tokens=9,
            cost_usd=0.0021,
            duration_ms=2.0,
            cpu_seconds=0.01,
            memory_bytes=1024,
            provider="fixture-gateway",
            exact_model_id="fixture-model-v1",
        ),
        completion_criteria={"criterion": "multi-host-native-execution"},
    )


def _request(*, run_id: UUID, idempotency_key: str, trace_id: str, span_id: str) -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=run_id,
        idempotency_key=idempotency_key,
        worker_id=str(WORKER_REGISTRY_ID),
        task_type="multi_host_execution_fixture",
        task_input={"private_marker": PAYLOAD_MARKER, "operation": "multi-host"},
        trace_id=trace_id,
        span_id=span_id,
        timeout_seconds=30,
    )


async def _register_hosts(registry: WorkerHostRegistry) -> None:
    common = {
        "labels": {"pool": "worker", "fixture": "multi-host-execution"},
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
        "metadata": {"fixture": "multi-host-execution"},
    }
    registered_a = await registry.register_host(
        host_id=HOST_A,
        host_uuid=HOST_UUID_A,
        registration_token=TOKEN_A,
        # Keep both reservations on this certificate's deterministic host
        # pair when other disposable worker fixtures share Postgres.
        priority=100,
        **common,
    )
    registered_b = await registry.register_host(
        host_id=HOST_B,
        host_uuid=HOST_UUID_B,
        registration_token=TOKEN_B,
        priority=99,
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
            required_labels=(
                ("pool", "worker"),
                ("fixture", "multi-host-execution"),
            ),
            required_sandbox_profile="standard",
            required_isolation_mode="native",
            slots=1,
        ),
        lease_seconds=90,
        metadata={"fixture": "multi-host-execution"},
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


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_multi_host_execution_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    mutation_performed = False
    first_counts: dict[str, int] = {}
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    host_a_result: WorkerHostExecutionResult | None = None
    host_b_result: WorkerHostExecutionResult | None = None
    duplicate_attempt_error: Exception | None = None
    replay_outcome: Any = None
    alias_replay_outcome: Any = None
    durable_runs: list[dict[str, Any]] = []
    durable_bindings: list[dict[str, Any] | None] = []
    durable_usage: list[dict[str, Any]] = []
    durable_artifacts: list[dict[str, Any]] = []
    durable_spans: dict[str, list[dict[str, Any]]] = {TRACE_A: [], TRACE_B: []}
    transition_counts: dict[str, int] = {}
    event_counts: dict[str, int] = {}
    reopened_healthy = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_multi_host_execution_migration_not_at_head",
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
            adapter_config={"fixture": "multi-host-execution"},
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1",
            source_repo="internal-fixture",
            source_revision="multi-host-v1",
            version_pin="fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="NativeWorkerAdapter",
            isolation_mode="native",
            model_mode="none",
        )
        canonical_worker_id = UUID(str(registered_worker["id"]))
        if canonical_worker_id != WORKER_REGISTRY_ID:
            return {
                **_blocked("worker_multi_host_fixture_worker_identity_mismatch"),
                "local_database_access_performed": True,
            }
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
        mutation_performed = True
        global _DISPATCH_COUNT, _RUN_A_STARTED, _RUN_A_RELEASE
        _DISPATCH_COUNT = 0
        _RUN_A_STARTED = asyncio.Event()
        _RUN_A_RELEASE = asyncio.Event()
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
        adapters = [
            NativeWorkerAdapter(
                _fixture_worker,
                worker_id=str(canonical_worker_id),
                runtime_version="fixture-runtime-v1",
            ),
            NativeWorkerAdapter(
                _fixture_worker,
                worker_id=str(canonical_worker_id),
                runtime_version="fixture-runtime-v1",
            ),
            NativeWorkerAdapter(
                _fixture_worker,
                worker_id=str(canonical_worker_id),
                runtime_version="fixture-runtime-v1",
            ),
        ]
        tasks: list[asyncio.Task[Any]] = []
        try:
            executor_a = WorkerHostExecutor(storage, binding_service=binding_service)
            executor_b = WorkerHostExecutor(storage, binding_service=binding_service)
            duplicate_executor = WorkerHostExecutor(storage, binding_service=binding_service)
            tasks = [
                asyncio.create_task(
                    executor_a.execute(
                        HostExecutionRequest(
                            run_id=RUN_A,
                            host_id=HOST_A,
                            owner=OWNER_A,
                            lease_seconds=30,
                        ),
                        request_a,
                        adapters[0],
                        worker_registry_id=canonical_worker_id,
                    )
                ),
                asyncio.create_task(
                    duplicate_executor.execute(
                        HostExecutionRequest(
                            run_id=RUN_A,
                            host_id=HOST_A,
                            owner=f"{OWNER_A}-duplicate",
                            lease_seconds=30,
                        ),
                        request_a,
                        adapters[2],
                        worker_registry_id=canonical_worker_id,
                    )
                ),
                asyncio.create_task(
                    executor_b.execute(
                        HostExecutionRequest(
                            run_id=RUN_B,
                            host_id=HOST_B,
                            owner=OWNER_B,
                            lease_seconds=30,
                        ),
                        request_b,
                        adapters[1],
                        worker_registry_id=canonical_worker_id,
                    )
                ),
            ]
            if _RUN_A_STARTED is None or _RUN_A_RELEASE is None:
                raise RuntimeError("duplicate-effect dispatch gates were not initialized")
            try:
                await asyncio.wait_for(_RUN_A_STARTED.wait(), timeout=5)
            finally:
                _RUN_A_RELEASE.set()
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            host_a_results = [
                item for item in gathered[:2] if isinstance(item, WorkerHostExecutionResult)
            ]
            host_a_errors = [
                item for item in gathered[:2] if isinstance(item, Exception)
            ]
            if len(host_a_results) == 1:
                host_a_result = host_a_results[0]
            if len(host_a_errors) == 1:
                duplicate_attempt_error = host_a_errors[0]
            if isinstance(gathered[2], WorkerHostExecutionResult):
                host_b_result = gathered[2]
            elif isinstance(gathered[2], Exception):
                raise gathered[2]
        finally:
            if _RUN_A_RELEASE is not None:
                _RUN_A_RELEASE.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(adapter.close() for adapter in adapters))
        from mas_core.worker_contract.controller import WorkerRunController

        replay_adapter = NativeWorkerAdapter(
            _fixture_worker,
            worker_id=str(canonical_worker_id),
            runtime_version="fixture-runtime-v1",
        )
        try:
            replay_controller = WorkerRunController(storage=storage)
            replay_outcome = await replay_controller.execute(
                request_a,
                replay_adapter,
                worker_registry_id=canonical_worker_id,
            )
            alias_replay_outcome = await replay_controller.execute(
                request_a.model_copy(update={"run_id": ALIAS_RUN}),
                replay_adapter,
                worker_registry_id=canonical_worker_id,
            )
        finally:
            await replay_adapter.close()
        if host_a_result is None or host_b_result is None:
            raise RuntimeError("multi-host execution did not produce both successful host results")
        for trace_id, span_id, result in (
            (TRACE_A, WORKER_SPAN_A, host_a_result),
            (TRACE_B, WORKER_SPAN_B, host_b_result),
        ):
            await storage.create_native_trace_span(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=SPAN_A if trace_id == TRACE_A else SPAN_B,
                source_kind="worker",
                operation="worker.execute",
                service="worker_host_executor",
                status="success" if result.outcome.state == "SUCCEEDED" else "failure",
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                attributes={"run_state": result.outcome.state, "host_id": result.host_id},
            )
        run_a = await storage.get_worker_run(RUN_A)
        run_b = await storage.get_worker_run(RUN_B)
        durable_runs = [run_a or {}, run_b or {}]
        durable_bindings = [
            await binding_service.get(RUN_A),
            await binding_service.get(RUN_B),
        ]
        transition_counts = {
            str(RUN_A): len(await storage.list_worker_run_transitions(RUN_A)),
            str(RUN_B): len(await storage.list_worker_run_transitions(RUN_B)),
        }
        event_counts = {
            str(RUN_A): len(await storage.list_worker_events(RUN_A)),
            str(RUN_B): len(await storage.list_worker_events(RUN_B)),
        }
        reopened = AgentStorage(normalized_dsn)
        await storage.close()
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
        with suppress(Exception):
            await storage.close()
        return {
            **_blocked("worker_multi_host_execution_checker_error"),
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
    host_results = [host_a_result, host_b_result]
    assigned_hosts = [
        result.binding_before.get("host_id") if result else None for result in host_results
    ]
    claim_states = [
        str((result.claimed if result else {}).get("state") or "unknown")
        for result in host_results
    ]
    duplicate_rejection_reason = (
        duplicate_attempt_error.reason_code
        if isinstance(duplicate_attempt_error, WorkerHostExecutionRejected)
        else type(duplicate_attempt_error).__name__
        if duplicate_attempt_error is not None
        else None
    )
    replay_state = str(getattr(replay_outcome, "state", "unknown"))
    replay_run_id = str(getattr(replay_outcome, "run_id", ""))
    alias_replay_state = str(getattr(alias_replay_outcome, "state", "unknown"))
    alias_replay_run_id = str(getattr(alias_replay_outcome, "run_id", ""))
    binding_states = [str((binding or {}).get("state") or "unknown") for binding in durable_bindings]
    reservation_states = [
        str((binding or {}).get("reservation_state") or "unknown") for binding in durable_bindings
    ]
    passed = all(
        (
            migration_version == EXPECTED_MIGRATION,
            run_states == ["SUCCEEDED", "SUCCEEDED"],
            assigned_hosts == [HOST_A, HOST_B],
            claim_states == ["CLAIMED", "CLAIMED"],
            binding_states == ["RELEASED", "RELEASED"],
            reservation_states == ["RELEASED", "RELEASED"],
            all(
                result is not None
                and result.binding_before.get("host_plane") == "worker"
                and result.binding_before.get("current_host_lease_valid") is True
                and result.binding_before.get("host_lease_generation")
                == result.binding_before.get("current_host_lease_generation")
                for result in host_results
            ),
            all(value >= 6 for value in transition_counts.values()),
            all(value >= 2 for value in event_counts.values()),
            len(durable_usage) == 2,
            len(durable_artifacts) == 2,
            all(len(durable_spans[trace_id]) >= 3 for trace_id in TRACE_IDS),
            all(item.get("status") == "pass" for item in coverage.values()),
            payload_free,
            _DISPATCH_COUNT == 2,
            duplicate_rejection_reason == "worker_run_claim_failed",
            replay_state == "SUCCEEDED",
            replay_run_id == str(RUN_A),
            alias_replay_state == "SUCCEEDED",
            alias_replay_run_id == str(RUN_A),
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
            sum(cleanup_counts.values()) >= 16,
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-multi-host-execution",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "host_ids": [HOST_A, HOST_B],
        "assigned_hosts": assigned_hosts,
        "run_states": run_states,
        "claim_states": claim_states,
        "dispatch_count": _DISPATCH_COUNT,
        "duplicate_attempt_count": 1,
        "duplicate_rejection_reason": duplicate_rejection_reason,
        "terminal_replay": {
            "state": replay_state,
            "run_id": replay_run_id,
        },
        "alias_replay": {
            "state": alias_replay_state,
            "run_id": alias_replay_run_id,
        },
        "binding_states": binding_states,
        "reservation_states": reservation_states,
        "transition_counts": transition_counts,
        "event_counts": event_counts,
        "usage_count": len(durable_usage),
        "artifact_count": len(durable_artifacts),
        "native_span_counts": {trace_id: len(rows) for trace_id, rows in durable_spans.items()},
        "trace_coverage": coverage,
        "initial_fixture_counts": first_counts,
        "durable_reopen": {
            "healthy": reopened_healthy,
            "run_count": sum(bool(row) for row in durable_runs),
            "released_binding_count": sum(state == "RELEASED" for state in binding_states),
            "usage_count": len(durable_usage),
            "artifact_count": len(durable_artifacts),
        },
        "cleanup_deleted_counts": cleanup_counts,
        "remaining_fixture_counts": remaining,
        "payload_free": payload_free,
        "mutation_performed": mutation_performed,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": True,
        "scope": "two committed worker-plane bindings, concurrent native fixture execution on distinct hosts, duplicate-claim rejection, terminal and alias replay, durable evidence, reopen, release, and scoped cleanup",
        "certification_boundary": {
            "distinct_worker_plane_hosts": "checked",
            "host_lease_generation_and_current_lease": "checked",
            "concurrent_worker_run_claims": "checked",
            "native_fixture_dispatch_on_each_host": "checked",
            "duplicate_effect_protection": "checked",
            "terminal_replay_without_redispatch": "checked",
            "alias_replay_without_redispatch": "checked",
            "durable_usage_artifact_trace_evidence": "checked",
            "binding_and_reservation_release": "checked",
            "postgres_connection_reopen": "checked",
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
            "external_provider_or_remote_runtime": "not_checked",
            "host_loss_split_brain_recovery": "not_checked",
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
        print(f"worker multi-host execution Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
