"""Certify selected Model Profile resolution on a committed worker host.

This probe exercises the AIAT-owned model-profile catalogue and deterministic
resolver, persists an immutable resolution snapshot, and carries that
snapshot through the normal Worker Host Executor and Worker Run Controller.
Dispatch uses the production ``GatewayWorkerAdapter`` against a bounded local
gateway double: no model provider or remote runtime is contacted.  The probe
therefore certifies control-plane model selection, gateway-adapter propagation,
and durable settlement, not provider availability, provider recovery, or a
gVisor/Firecracker sandbox.
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

from mas_core.llm_gateway.model_profiles import (  # noqa: E402
    ModelProfile,
    ModelProfileStatus,
    ModelProfileVersion,
    ModelResolutionRequest,
)
from mas_core.llm_gateway.model_resolver import ModelProfileResolver  # noqa: E402
from mas_core.llm_gateway.models import ChatMessage, ChatResponse, UsageStats  # noqa: E402
from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.observability.trace_evidence import build_trace_evidence  # noqa: E402
from mas_core.observability.worker_trace_coverage import (  # noqa: E402
    WORKER_TRACE_COVERAGE_SCHEMA,
    evaluate_worker_trace_coverage,
)
from mas_core.worker_contract.models import (  # noqa: E402
    ModelProfileReference,
    WorkerRunRequest,
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
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter  # noqa: E402

CHECK_SCHEMA = "aiat.worker-host-model-resolution-postgres-certification.v1"
EXPECTED_MIGRATION = "0042_worker_run_host_binding"
WORKER_NAME = "aiat-cert-host-model-resolution-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
HOST_NAME = "aiat-cert-host-model-resolution-worker-v1"
HOST_PREFIX = f"{HOST_NAME}%"
HOST_UUID = UUID("00000000-0000-4000-a000-000000000b51")
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000b52")
RUN_ID = UUID("00000000-0000-4000-a000-000000000b53")
RESERVATION_ID = UUID("00000000-0000-4000-a000-000000000b54")
SNAPSHOT_ID = UUID("00000000-0000-4000-a000-000000000b55")
PROFILE_LOGICAL_ID = "aiat-cert-host-model-profile-v1"
PROFILE_VERSION = "fixture-v1"
PROVIDER_ID = "fixture-provider-gateway"
EXACT_MODEL_ID = "fixture-model-v1"
TOKEN = "aiat-host-model-resolution-fixture-token-v1"
OWNER = "aiat-host-model-resolution-fixture"
ASSIGNMENT_KEY = "aiat-cert-host-model-resolution-v1-assignment"
TRACE_ID = "aiat-cert-host-model-resolution-v1-trace"
SPAN_ID = "aiat-cert-host-model-resolution-v1-span"
WORKER_SPAN_ID = "aiat-cert-host-model-resolution-v1-worker-span"
IDEMPOTENCY_KEY = "aiat-cert-host-model-resolution-v1-idempotency"
PAYLOAD_MARKER = "aiat model resolution fixture payload must never enter the evidence report"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_MODEL_RESOLUTION_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_WORKER_HOST_MODEL_RESOLUTION_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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
        "mode": "local-postgres-worker-host-model-resolution",
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
            "snapshots": await connection.scalar(
                sa.text("SELECT count(*) FROM model_resolution_snapshots WHERE id = :snapshot_id"),
                {"snapshot_id": SNAPSHOT_ID},
            ),
            "profiles": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM model_profiles WHERE logical_profile_id = :profile_id"
                ),
                {"profile_id": PROFILE_LOGICAL_ID},
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
        deleted_snapshots = await connection.execute(
            sa.text("DELETE FROM model_resolution_snapshots WHERE id = :snapshot_id"),
            {"snapshot_id": SNAPSHOT_ID},
        )
        deleted_versions = await connection.execute(
            sa.text(
                """DELETE FROM model_profile_versions
                   WHERE profile_id IN (
                       SELECT id FROM model_profiles
                       WHERE logical_profile_id = :profile_id
                   )"""
            ),
            {"profile_id": PROFILE_LOGICAL_ID},
        )
        deleted_profiles = await connection.execute(
            sa.text("DELETE FROM model_profiles WHERE logical_profile_id = :profile_id"),
            {"profile_id": PROFILE_LOGICAL_ID},
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
        "snapshots": int(deleted_snapshots.rowcount or 0),
        "model_profile_versions": int(deleted_versions.rowcount or 0),
        "model_profiles": int(deleted_profiles.rowcount or 0),
    }


class _FixtureGateway:
    """Bounded local gateway double used by the production gateway adapter."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.calls.append(dict(kwargs))
        return ChatResponse(
            model=str(kwargs["model"]),
            message=ChatMessage(
                role="assistant",
                content="durable host gateway fixture answer",
            ),
            usage=UsageStats(prompt_tokens=5, completion_tokens=9, total_tokens=14),
        )


def _safe_blob(*values: Any) -> str:
    return json.dumps(values, default=str, sort_keys=True)


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_host_model_resolution_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    migration_version: str | None = None
    mutation_performed = False
    first_counts: dict[str, int] = {}
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    execution_result: Any = None
    binding_before: dict[str, Any] | None = None
    binding_after: dict[str, Any] | None = None
    durable_run: dict[str, Any] | None = None
    durable_worker: dict[str, Any] | None = None
    durable_snapshot: dict[str, Any] | None = None
    durable_profile: dict[str, Any] | None = None
    durable_usage: list[dict[str, Any]] = []
    durable_artifacts: list[dict[str, Any]] = []
    durable_spans: list[dict[str, Any]] = []
    gateway_call_count = 0
    transition_count = 0
    event_count = 0
    reopened_healthy = False
    resolved_snapshot: dict[str, Any] | None = None
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_host_model_resolution_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }
        await _cleanup(storage)
        first_counts = await _counts(storage)

        profile_row = await storage.create_model_profile(
            logical_profile_id=PROFILE_LOGICAL_ID,
            purpose="deterministic local worker model-resolution fixture",
            approved_provider_ids=[PROVIDER_ID],
            required_capabilities=[],
            fallback_profile_ids=[],
            status="approved",
            owner="aiat-fixture",
        )
        await storage.create_model_profile_version(
            profile_id=profile_row["id"],
            version=PROFILE_VERSION,
            provider_id=PROVIDER_ID,
            exact_model_id=EXACT_MODEL_ID,
            capabilities=["structured_output", "reasoning"],
            constraints={"context_window": 4096, "max_output_tokens": 1024},
            provider_settings={"transport": "local-fixture", "network": "disabled"},
            status="approved",
            api_version="fixture-api-v1",
            version_metadata={"fixture": True},
        )
        durable_profile = await storage.get_model_profile(PROFILE_LOGICAL_ID)
        if durable_profile is None or not durable_profile.get("versions"):
            raise RuntimeError("model profile fixture was not durable")
        stored_version = durable_profile["versions"][0]
        profile = ModelProfile(
            profile_id=PROFILE_LOGICAL_ID,
            purpose=str(durable_profile["purpose"]),
            approved_provider_ids=frozenset(durable_profile["approved_provider_ids"] or []),
            versions=(
                ModelProfileVersion(
                    version=str(stored_version["version"]),
                    provider_id=str(stored_version["provider_id"]),
                    exact_model_id=str(stored_version["exact_model_id"]),
                    api_version=stored_version.get("api_version"),
                    capabilities=frozenset(stored_version.get("capabilities") or []),
                    context_window=int((stored_version.get("constraints_json") or {}).get("context_window") or 0),
                    max_output_tokens=int((stored_version.get("constraints_json") or {}).get("max_output_tokens") or 0),
                    reasoning="reasoning" in (stored_version.get("capabilities") or []),
                    structured_output="structured_output" in (stored_version.get("capabilities") or []),
                    provider_settings=dict(stored_version.get("provider_settings") or {}),
                    status=ModelProfileStatus.APPROVED,
                ),
            ),
            status=ModelProfileStatus.APPROVED,
            owner=str(durable_profile.get("owner") or "aiat-fixture"),
        )
        resolution_request = ModelResolutionRequest(
            task_type="host_model_resolution_fixture",
            requested_profile_id=PROFILE_LOGICAL_ID,
            task_required_capabilities=frozenset({"reasoning"}),
            prompt_tokens=5,
            expected_output_tokens=9,
        )
        resolved = ModelProfileResolver().resolve((profile,), resolution_request)
        resolved = resolved.model_copy(update={"snapshot_id": SNAPSHOT_ID})
        resolved_snapshot = resolved.model_dump(mode="json")
        await storage.create_model_resolution_snapshot(snapshot=resolved_snapshot)
        durable_snapshot = await storage.get_model_resolution_snapshot(SNAPSHOT_ID)
        if durable_snapshot is None:
            raise RuntimeError("model resolution snapshot was not durable")

        registered_worker = await storage.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_REGISTRY_ID,
            adapter_type="aiat_gateway",
            adapter_config={
                "fixture": "host-model-resolution",
                "provider_id": PROVIDER_ID,
                "gateway_backend": "local-fixture",
            },
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1",
            source_repo="internal-fixture",
            source_revision="host-model-resolution-v1",
            version_pin="fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="GatewayWorkerAdapter",
            isolation_mode="native",
            model_profile_id=PROFILE_LOGICAL_ID,
            model_mode="aiat_gateway",
        )
        canonical_worker_id = UUID(str(registered_worker["id"]))
        durable_worker = registered_worker
        registry = WorkerHostRegistry(storage)
        registered_host = await registry.register_host(
            host_id=HOST_NAME,
            host_uuid=HOST_UUID,
            registration_token=TOKEN,
            labels={"pool": "worker"},
            capabilities=["aiat_gateway", "model_resolution"],
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
            metadata={"fixture": "host-model-resolution"},
        )
        await registry.heartbeat(
            host_id=HOST_NAME,
            registration_token=TOKEN,
            lease_generation=registered_host["lease_generation"],
            lease_seconds=120,
        )
        requested_reference = ModelProfileReference(profile_id=PROFILE_LOGICAL_ID)
        resolved_reference = ModelProfileReference(
            profile_id=PROFILE_LOGICAL_ID,
            version=PROFILE_VERSION,
            exact_model_id=EXACT_MODEL_ID,
            resolution_snapshot_id=SNAPSHOT_ID,
        )
        request = WorkerRunRequest(
            run_id=RUN_ID,
            idempotency_key=IDEMPOTENCY_KEY,
            worker_id=str(canonical_worker_id),
            task_type="host_model_resolution_fixture",
            task_input={
                "prompt": "reply with a bounded durable host gateway fixture answer",
                "max_tokens": 32,
                "temperature": 0.2,
                "private_marker": PAYLOAD_MARKER,
                "operation": "worker-plane",
            },
            requested_model_profile=requested_reference,
            resolved_model_profile=resolved_reference,
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
            model_resolution_snapshot_id=SNAPSHOT_ID,
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
                required_capabilities=frozenset({"aiat_gateway", "model_resolution"}),
                required_labels=(("pool", "worker"),),
                required_sandbox_profile="standard",
                required_isolation_mode="native",
                slots=1,
            ),
            lease_seconds=90,
            metadata={"fixture": "host-model-resolution"},
            reservation_id=RESERVATION_ID,
        )
        await binding_service.assign(binding_request)
        binding_before = await binding_service.commit(RUN_ID, owner=OWNER)
        gateway = _FixtureGateway()
        adapter = GatewayWorkerAdapter(
            worker_id=str(canonical_worker_id),
            provider_id=PROVIDER_ID,
            gateway_client=gateway,
            runtime_version="gateway-host-model-resolution-fixture-v1",
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
                model_resolution_snapshot_id=SNAPSHOT_ID,
            )
        finally:
            await adapter.close()
        gateway_call_count = len(gateway.calls)
        binding_after = execution_result.binding_after
        stored_artifact = await storage.create_artifact(
            agent_id=str(canonical_worker_id),
            path="fixture://aiat/host-model-resolution-v1/report.json",
            metadata={"fixture_projection": True, "payload_free": True},
            sha256="d" * 64,
            size_bytes=128,
        )
        await storage.create_worker_artifact(
            run_id=RUN_ID,
            artifact_id=stored_artifact["id"],
            kind="report",
            uri=stored_artifact["path"],
            sha256=stored_artifact["sha256"],
            size_bytes=stored_artifact["size_bytes"],
            metadata=stored_artifact["metadata"],
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
        )
        await storage.create_native_trace_span(
            trace_id=TRACE_ID,
            span_id=WORKER_SPAN_ID,
            parent_span_id=SPAN_ID,
            source_kind="worker",
            operation="worker.execute.model_resolution",
            service="worker_host_executor",
            status="success" if execution_result.outcome.state == "SUCCEEDED" else "failure",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            attributes={
                "run_state": execution_result.outcome.state,
                "model_resolution_snapshot_id": str(SNAPSHOT_ID),
                "fixture": True,
            },
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
            durable_worker = await reopened.get_worker(WORKER_REGISTRY_ID)
            durable_snapshot = await reopened.get_model_resolution_snapshot(SNAPSHOT_ID)
            durable_profile = await reopened.get_model_profile(PROFILE_LOGICAL_ID)
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
            **_blocked("worker_host_model_resolution_checker_error"),
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
    payload_free = PAYLOAD_MARKER not in _safe_blob(
        evidence.model_dump(mode="json"), durable_spans
    )
    run_state = str((run_row or {}).get("state") or "unknown")
    claim_state = str(
        (execution_result.claimed if execution_result else {}).get("state") or "unknown"
    )
    run_request = (durable_run or {}).get("request_json") or {}
    persisted_resolved = run_request.get("resolved_model_profile") or {}
    persisted_requested = run_request.get("requested_model_profile") or {}
    usage = durable_usage[0] if durable_usage else {}
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
            durable_worker is not None
            and durable_worker.get("adapter_type") == "aiat_gateway"
            and durable_worker.get("adapter_entrypoint") == "GatewayWorkerAdapter"
            and durable_worker.get("model_mode") == "aiat_gateway"
            and durable_worker.get("model_profile_id") == PROFILE_LOGICAL_ID,
            durable_snapshot is not None
            and durable_snapshot.get("id") == SNAPSHOT_ID
            and durable_snapshot.get("requested_profile_id") == PROFILE_LOGICAL_ID
            and durable_snapshot.get("resolved_profile_id") == PROFILE_LOGICAL_ID
            and durable_snapshot.get("resolved_profile_version") == PROFILE_VERSION
            and durable_snapshot.get("provider_id") == PROVIDER_ID
            and durable_snapshot.get("exact_model_id") == EXACT_MODEL_ID
            and durable_snapshot.get("policy_failure_code") is None,
            durable_profile is not None
            and len(durable_profile.get("versions") or []) == 1
            and durable_profile["versions"][0].get("status") == "approved",
            durable_binding is not None and durable_binding.get("state") == "RELEASED",
            durable_run is not None
            and durable_run.get("model_resolution_snapshot_id") == SNAPSHOT_ID,
            persisted_requested.get("profile_id") == PROFILE_LOGICAL_ID,
            persisted_resolved.get("profile_id") == PROFILE_LOGICAL_ID,
            persisted_resolved.get("version") == PROFILE_VERSION,
            persisted_resolved.get("exact_model_id") == EXACT_MODEL_ID,
            persisted_resolved.get("resolution_snapshot_id") == str(SNAPSHOT_ID),
            usage.get("provider_id") == PROVIDER_ID,
            usage.get("exact_model_id") == EXACT_MODEL_ID,
            gateway_call_count == 1,
            remaining
            == {
                "workers": 0,
                "runs": 0,
                "bindings": 0,
                "reservations": 0,
                "hosts": 0,
                "snapshots": 0,
                "profiles": 0,
                "spans": 0,
            },
            sum(cleanup_counts.values()) >= 11,
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "binding_schema": RUN_HOST_BINDING_SCHEMA,
        "coverage_schema": WORKER_TRACE_COVERAGE_SCHEMA,
        "mode": "local-postgres-worker-host-model-resolution",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "run_state": run_state,
        "claim_state": claim_state,
        "transition_count": transition_count,
        "event_count": event_count,
        "gateway_call_count": gateway_call_count,
        "usage_count": len(durable_usage),
        "artifact_count": len(durable_artifacts),
        "native_span_count": len(durable_spans),
        "trace_item_count": evidence.item_count,
        "trace_source_counts": evidence.source_counts,
        "trace_coverage": coverage,
        "model_resolution": {
            "profile_id": PROFILE_LOGICAL_ID,
            "profile_version": PROFILE_VERSION,
            "snapshot_id": str(SNAPSHOT_ID),
            "provider_id": PROVIDER_ID,
            "exact_model_id": EXACT_MODEL_ID,
            "selection_reason": durable_snapshot.get("selection_reason") if durable_snapshot else None,
            "worker_model_mode": durable_worker.get("model_mode") if durable_worker else None,
            "worker_model_profile_id": durable_worker.get("model_profile_id") if durable_worker else None,
            "worker_adapter_type": durable_worker.get("adapter_type") if durable_worker else None,
            "worker_adapter_entrypoint": durable_worker.get("adapter_entrypoint") if durable_worker else None,
            "run_snapshot_id": str(durable_run.get("model_resolution_snapshot_id")) if durable_run else None,
            "request_reference": persisted_resolved,
            "usage_provider_id": usage.get("provider_id"),
            "usage_exact_model_id": usage.get("exact_model_id"),
        },
        "host_admission": {
            "host_id": HOST_NAME,
            "host_plane": binding_before.get("host_plane") if binding_before else None,
            "binding_state_before": binding_before.get("state") if binding_before else None,
            "reservation_state_before": binding_before.get("reservation_state") if binding_before else None,
            "current_host_lease_valid": binding_before.get("current_host_lease_valid") if binding_before else None,
            "binding_state_after": binding_after.get("state") if binding_after else None,
            "reservation_state_after": binding_after.get("reservation_state") if binding_after else None,
        },
        "durable_reopen": {
            "healthy": reopened_healthy,
            "run_present": durable_run is not None,
            "worker_present": durable_worker is not None,
            "snapshot_present": durable_snapshot is not None,
            "profile_present": durable_profile is not None,
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
        "scope": "AIAT model profile/version resolution, durable snapshot propagation, committed worker-host admission, production GatewayWorkerAdapter over a bounded local gateway fixture, evidence, release, reopen, and cleanup",
        "certification_boundary": {
            "approved_model_profile_and_version": "checked",
            "deterministic_model_profile_resolution": "checked",
            "durable_model_resolution_snapshot": "checked",
            "worker_model_mode_and_profile_binding": "checked",
            "run_snapshot_and_request_reference_propagation": "checked",
            "usage_provider_and_exact_model_attribution": "checked",
            "committed_worker_host_binding_and_lease": "checked",
            "gateway_worker_adapter_fixture_dispatch": "checked",
            "durable_terminal_evidence": "checked",
            "binding_and_reservation_release": "checked",
            "postgres_connection_reopen": "checked",
            "payload_free_trace_projection": "checked",
            "external_model_provider_or_network": "not_checked",
            "provider_backed_recovery": "not_checked",
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
            "independent_host_or_remote_runtime": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"worker-host model-resolution Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
