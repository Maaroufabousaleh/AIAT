"""Certify durable gateway-worker to mail-edge evidence composition.

This probe runs the production ``GatewayWorkerAdapter`` and
``WorkerRunController`` against AIAT Postgres, persists a payload-free worker
artifact/usage/trace projection, records normalized delivery/webhook/bounce
observations in the identity-service Postgres store, and joins both stores by
worker and trace identity after independent connection reopen.  The gateway
and mail observations are bounded local fixtures: no external provider,
network endpoint, SMTP relay, sandbox, or live worker is contacted.

The checker requires separate worker and identity-service Postgres DSNs.  It
exits with status 2 when either database is not configured, unavailable, or
not at its expected migration head, and never falls back to in-memory storage.
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
IDENTITY_ROOT = MAS_ROOT / "apps" / "identity-service"
for _path in (CORE_ROOT, IDENTITY_ROOT):
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from identity_service.store import PostgresIdentityStore  # noqa: E402

from mas_core.llm_gateway.models import ChatMessage, ChatResponse, UsageStats  # noqa: E402
from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.observability.mail_edge import (  # noqa: E402
    MailEdgeObservation,
    build_mail_edge_observation,
    normalize_provider_webhook,
)
from mas_core.observability.trace_evidence import build_trace_evidence  # noqa: E402
from mas_core.observability.worker_trace_coverage import (  # noqa: E402
    WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
    evaluate_worker_mail_edge_coverage,
)
from mas_core.worker_contract.controller import WorkerRunController  # noqa: E402
from mas_core.worker_contract.models import ModelProfileReference, WorkerRunRequest  # noqa: E402
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter  # noqa: E402

CHECK_SCHEMA = "aiat.gateway-worker-mail-edge-postgres-certification.v1"
EXPECTED_WORKER_MIGRATION = "0042_worker_run_host_binding"
EXPECTED_IDENTITY_MIGRATION = "0003_mail_edge_observations"
WORKER_NAME = "aiat-cert-gateway-mail-edge-postgres-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
WORKER_ID = UUID("00000000-0000-4000-a000-000000000b61")
RUN_ID = UUID("00000000-0000-4000-a000-000000000b62")
TRACE_ID = "aiat-cert-gateway-mail-edge-postgres-v1-trace"
SPAN_ID = "aiat-cert-gateway-mail-edge-postgres-v1-span"
WORKER_SPAN_ID = "aiat-cert-gateway-mail-edge-postgres-v1-worker-span"
INTEGRATION_SPAN_ID = "aiat-cert-gateway-mail-edge-postgres-v1-mail-span"
IDEMPOTENCY_KEY = "aiat-cert-gateway-mail-edge-postgres-v1-idempotency"
WORKER_ID_TEXT = str(WORKER_ID)
PROVIDER_ID = "fixture-provider"
MODEL_ID = "fixture/model-v1"
EVENT_PREFIX = "aiat-cert-gateway-mail-edge-postgres-v1-"
PAYLOAD_MARKER = "gateway worker mail-edge postgres payload must never persist"
TIMESTAMP = "2026-08-18T12:00:00Z"


class _FixtureGateway:
    """Bounded gateway double used by the production gateway adapter."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.calls.append(dict(kwargs))
        return ChatResponse(
            model=str(kwargs["model"]),
            message=ChatMessage(role="assistant", content="durable mail-edge fixture answer"),
            usage=UsageStats(prompt_tokens=6, completion_tokens=4, total_tokens=10),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--worker-dsn",
        default=os.getenv(
            "AIAT_GATEWAY_WORKER_MAIL_EDGE_WORKER_DSN",
            os.getenv("POSTGRES_DSN", ""),
        ),
        help="AIAT worker Postgres DSN",
    )
    parser.add_argument(
        "--identity-dsn",
        default=os.getenv(
            "AIAT_GATEWAY_WORKER_MAIL_EDGE_IDENTITY_DSN",
            os.getenv("IDENTITY_DATABASE_DSN", ""),
        ),
        help="identity-service Postgres DSN",
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


def _blocked(reason: str, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
        "mode": "local-dual-postgres-worker-mail-edge",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }
    report.update(details)
    return report


async def _migration_version(engine: Any) -> str | None:
    async with engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _worker_counts(storage: AgentStorage) -> dict[str, int]:
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
            "artifacts": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_artifacts WHERE run_id = :run_id"),
                {"run_id": RUN_ID},
            ),
            "usage": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_usage_records WHERE run_id = :run_id"),
                {"run_id": RUN_ID},
            ),
            "spans": await connection.scalar(
                sa.text("SELECT count(*) FROM native_trace_spans WHERE trace_id = :trace_id"),
                {"trace_id": TRACE_ID},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup_worker(storage: AgentStorage) -> dict[str, int]:
    """Remove only this checker namespace from the worker database."""

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


async def _identity_rows(store: PostgresIdentityStore) -> list[dict[str, Any]]:
    async with store.engine.connect() as connection:
        result = await connection.execute(
            sa.text(
                """SELECT * FROM mail_edge_observations
                   WHERE event_id LIKE :prefix
                   ORDER BY event_id"""
            ),
            {"prefix": f"{EVENT_PREFIX}%"},
        )
        return [dict(row) for row in result.mappings().all()]


async def _cleanup_identity(store: PostgresIdentityStore) -> int:
    async with store.engine.begin() as connection:
        result = await connection.execute(
            sa.text("DELETE FROM mail_edge_observations WHERE event_id LIKE :prefix"),
            {"prefix": f"{EVENT_PREFIX}%"},
        )
        return int(result.rowcount or 0)


def _fixture_observations() -> list[MailEdgeObservation]:
    delivery = build_mail_edge_observation(
        provider="resend",
        source="delivery_attempt",
        event_id=f"{EVENT_PREFIX}attempt",
        event_type="queued",
        worker_id=WORKER_ID_TEXT,
        provider_message_ref=f"{EVENT_PREFIX}message",
        trace_id=TRACE_ID,
        span_id=f"{EVENT_PREFIX}attempt-span",
        occurred_at=TIMESTAMP,
        metadata={"attempt_number": 1},
    )
    delivered = normalize_provider_webhook(
        "resend",
        {
            "id": f"{EVENT_PREFIX}delivered",
            "type": "email.delivered",
            "created_at": TIMESTAMP,
            "data": {
                "email_id": f"{EVENT_PREFIX}message",
                "status": "delivered",
                "recipient": "private@example.invalid",
                "body": PAYLOAD_MARKER,
            },
        },
        signature_verified=True,
        worker_id=WORKER_ID_TEXT,
        trace_id=TRACE_ID,
        span_id=f"{EVENT_PREFIX}delivered-span",
    )
    bounced = normalize_provider_webhook(
        "resend",
        {
            "id": f"{EVENT_PREFIX}bounced",
            "type": "email.bounced",
            "created_at": TIMESTAMP,
            "data": {
                "email_id": f"{EVENT_PREFIX}message-bounced",
                "status": "permanent",
                "reason_code": "fixture",
                "body": PAYLOAD_MARKER,
            },
        },
        signature_verified=True,
        worker_id=WORKER_ID_TEXT,
        trace_id=TRACE_ID,
        span_id=f"{EVENT_PREFIX}bounced-span",
    )
    return [delivery, delivered, bounced]


def _safe_mail_rows(rows: list[dict[str, Any]]) -> list[MailEdgeObservation]:
    return [
        MailEdgeObservation.model_validate(
            {
                "id": row.get("id"),
                "schema_version": row.get("schema_version"),
                "provider": row.get("provider"),
                "source": row.get("source"),
                "event_id": row.get("event_id"),
                "event_type": row.get("event_type"),
                "outcome": row.get("outcome"),
                "failure_class": row.get("failure_class"),
                "worker_id": row.get("worker_id"),
                "outbound_request_id": row.get("outbound_request_id"),
                "provider_message_ref": row.get("provider_message_ref"),
                "trace_id": row.get("trace_id"),
                "span_id": row.get("span_id"),
                "occurred_at": row.get("occurred_at"),
                "signature_verified": row.get("signature_verified"),
                "metadata": row.get("metadata_json") or row.get("metadata") or {},
            }
        )
        for row in rows
    ]


async def _run(worker_dsn: str | None, identity_dsn: str | None) -> dict[str, Any]:
    worker_url = _normalize_dsn(worker_dsn)
    identity_url = _normalize_dsn(identity_dsn)
    if worker_url is None or identity_url is None:
        return _blocked(
            "gateway_worker_mail_edge_postgres_database_not_configured",
            worker_database_configured=worker_url is not None,
            identity_database_configured=identity_url is not None,
        )

    worker = AgentStorage(worker_url)
    identity = PostgresIdentityStore(identity_url)
    worker_migration: str | None = None
    identity_migration: str | None = None
    worker_cleanup: dict[str, int] = {}
    identity_cleanup = 0
    worker_remaining: dict[str, int] = {}
    identity_remaining = 0
    worker_reopened = False
    identity_reopened = False
    run_row: dict[str, Any] | None = None
    durable_run: dict[str, Any] | None = None
    durable_usage: list[dict[str, Any]] = []
    durable_artifacts: list[dict[str, Any]] = []
    durable_spans: list[dict[str, Any]] = []
    durable_mail_rows: list[dict[str, Any]] = []
    gateway_calls = 0
    outcome: Any = None
    try:
        await worker.connect()
        if not await identity.healthcheck():
            return _blocked(
                "gateway_worker_mail_edge_identity_database_unavailable",
                local_database_access_performed=True,
            )
        worker_migration = await _migration_version(worker.engine)
        identity_migration = await _migration_version(identity.engine)
        if worker_migration != EXPECTED_WORKER_MIGRATION or identity_migration != EXPECTED_IDENTITY_MIGRATION:
            return _blocked(
                "gateway_worker_mail_edge_migration_not_at_head",
                worker_migration=worker_migration,
                identity_migration=identity_migration,
                expected_worker_migration=EXPECTED_WORKER_MIGRATION,
                expected_identity_migration=EXPECTED_IDENTITY_MIGRATION,
                local_database_access_performed=True,
                network_access_performed=True,
            )
        await _cleanup_worker(worker)
        await _cleanup_identity(identity)
        registered = await worker.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_ID,
            adapter_type="aiat_gateway",
            adapter_config={"fixture": "gateway-mail-edge-postgres", "provider_id": PROVIDER_ID},
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1",
            source_repo="internal-fixture",
            source_revision="gateway-mail-edge-postgres-v1",
            version_pin="fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="GatewayWorkerAdapter",
            isolation_mode="native",
            model_mode="aiat_gateway",
        )
        canonical_worker_id = UUID(str(registered["id"]))
        request = WorkerRunRequest(
            run_id=RUN_ID,
            idempotency_key=IDEMPOTENCY_KEY,
            worker_id=str(canonical_worker_id),
            task_type="gateway_worker_mail_edge_postgres_fixture",
            task_input={
                "prompt": "return a bounded durable worker/mail-edge fixture answer",
                "max_tokens": 32,
                "temperature": 0.2,
                "private_marker": PAYLOAD_MARKER,
            },
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
            resolved_model_profile=ModelProfileReference(
                profile_id="gateway-mail-edge-postgres-profile-v1",
                version="fixture-v1",
                exact_model_id=MODEL_ID,
            ),
            timeout_seconds=30,
        )
        controller = WorkerRunController(storage=worker)
        await controller.create_run(request, worker_registry_id=canonical_worker_id)
        artifact = await worker.create_artifact(
            agent_id=str(canonical_worker_id),
            path="fixture://aiat/gateway-mail-edge-postgres-v1/report.json",
            metadata={"fixture_projection": True, "payload_free": True},
            sha256="e" * 64,
            size_bytes=96,
        )
        await worker.create_worker_artifact(
            run_id=RUN_ID,
            artifact_id=artifact["id"],
            kind="report",
            uri=artifact["path"],
            sha256=artifact["sha256"],
            size_bytes=artifact["size_bytes"],
            metadata=artifact["metadata"],
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
        )
        gateway = _FixtureGateway()
        adapter = GatewayWorkerAdapter(
            worker_id=str(canonical_worker_id),
            provider_id=PROVIDER_ID,
            gateway_client=gateway,
            runtime_version="gateway-mail-edge-postgres-fixture-v1",
        )
        try:
            outcome = await controller.execute(
                request,
                adapter,
                worker_registry_id=canonical_worker_id,
            )
        finally:
            await adapter.close()
        gateway_calls = len(gateway.calls)
        await worker.create_native_trace_span(
            trace_id=TRACE_ID,
            span_id=WORKER_SPAN_ID,
            parent_span_id=SPAN_ID,
            source_kind="worker",
            operation="worker.execute.gateway_mail_edge",
            service="worker_run_controller",
            status="success" if outcome.state == "SUCCEEDED" else "failure",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            attributes={"run_state": outcome.state, "fixture": True},
        )
        await worker.create_native_trace_span(
            trace_id=TRACE_ID,
            span_id=INTEGRATION_SPAN_ID,
            parent_span_id=SPAN_ID,
            source_kind="integration",
            operation="mail.provider_webhook.fixture",
            service="identity_service",
            status="success",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            attributes={"event_count": 3, "fixture": True},
        )
        for observation in _fixture_observations():
            await identity.record_mail_edge_observation(observation)
        run_row = await worker.get_worker_run(RUN_ID)
        durable_usage = await worker.list_worker_usage(RUN_ID)
        durable_artifacts = await worker.list_worker_artifacts(RUN_ID)
        durable_spans = await worker.list_native_trace_spans_by_trace(TRACE_ID)
        durable_mail_rows = await _identity_rows(identity)

        await worker.close()
        await identity.close()
        reopened_worker = AgentStorage(worker_url)
        reopened_identity = PostgresIdentityStore(identity_url)
        try:
            await reopened_worker.connect()
            worker_reopened = True
            identity_reopened = await reopened_identity.healthcheck()
            durable_run = await reopened_worker.get_worker_run(RUN_ID)
            durable_usage = await reopened_worker.list_worker_usage(RUN_ID)
            durable_artifacts = await reopened_worker.list_worker_artifacts(RUN_ID)
            durable_spans = await reopened_worker.list_native_trace_spans_by_trace(TRACE_ID)
            durable_mail_rows = await _identity_rows(reopened_identity)
            worker_cleanup = await _cleanup_worker(reopened_worker)
            identity_cleanup = await _cleanup_identity(reopened_identity)
            worker_remaining = await _worker_counts(reopened_worker)
            identity_remaining = len(await _identity_rows(reopened_identity))
        finally:
            with suppress(Exception):
                await reopened_worker.close()
            with suppress(Exception):
                await reopened_identity.close()
    except Exception as exc:
        return _blocked(
            "gateway_worker_mail_edge_postgres_checker_error",
            error_type=type(exc).__name__,
            error=str(exc),
            worker_migration=worker_migration,
            identity_migration=identity_migration,
            mutation_performed=True,
            local_database_access_performed=True,
            network_access_performed=True,
        )
    finally:
        if getattr(worker, "_engine", None) is not None:
            with suppress(Exception):
                await _cleanup_worker(worker)
            with suppress(Exception):
                await worker.close()
        with suppress(Exception):
            await _cleanup_identity(identity)
        with suppress(Exception):
            await identity.close()

    observations = _safe_mail_rows(durable_mail_rows)
    integration_rows = [
        {
            "id": f"{EVENT_PREFIX}integration-{item.event_id}",
            "connection_id": "identity-service-postgres-fixture",
            "evidence_type": f"mail.provider_webhook.{item.event_type}",
            "trace_id": item.trace_id,
            "created_at": item.occurred_at.isoformat(),
        }
        for item in observations
    ]
    evidence = build_trace_evidence(
        trace_id=TRACE_ID,
        worker_usage_rows=durable_usage,
        artifact_rows=durable_artifacts,
        integration_evidence_rows=integration_rows,
        native_span_rows=durable_spans,
        generated_at=TIMESTAMP,
    )
    combined = evaluate_worker_mail_edge_coverage(
        evidence,
        observations,
        trace_id=TRACE_ID,
        worker_id=WORKER_ID_TEXT,
        require_integration=True,
        require_mail_edge=True,
    )
    payload_free = PAYLOAD_MARKER not in json.dumps(
        {
            "evidence": evidence.model_dump(mode="json"),
            "mail_rows": durable_mail_rows,
            "spans": durable_spans,
        },
        default=str,
        sort_keys=True,
    )
    run_state = str((run_row or {}).get("state") or "unknown")
    usage = durable_usage[0] if durable_usage else {}
    passed = all(
        (
            worker_migration == EXPECTED_WORKER_MIGRATION,
            identity_migration == EXPECTED_IDENTITY_MIGRATION,
            outcome is not None and outcome.state == "SUCCEEDED",
            run_state == "SUCCEEDED",
            gateway_calls == 1,
            durable_run is not None,
            len(durable_usage) == 1,
            len(durable_artifacts) == 1,
            len(durable_spans) >= 3,
            len(observations) == 3,
            combined["status"] == "pass",
            usage.get("provider_id") == PROVIDER_ID,
            usage.get("exact_model_id") == MODEL_ID,
            payload_free,
            worker_reopened,
            identity_reopened,
            worker_remaining == {"workers": 0, "runs": 0, "artifacts": 0, "usage": 0, "spans": 0},
            identity_remaining == 0,
            sum(worker_cleanup.values()) >= 5,
            identity_cleanup >= 3,
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
        "mode": "local-dual-postgres-worker-mail-edge",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "worker_migration_version": worker_migration,
        "identity_migration_version": identity_migration,
        "controller_terminal_state": outcome.state if outcome is not None else None,
        "run_state": run_state,
        "gateway_call_count": gateway_calls,
        "worker_usage_count": len(durable_usage),
        "worker_artifact_count": len(durable_artifacts),
        "native_span_count": len(durable_spans),
        "mail_observation_count": len(observations),
        "worker_id": WORKER_ID_TEXT,
        "trace_id": TRACE_ID,
        "provider_id": PROVIDER_ID,
        "exact_model_id": MODEL_ID,
        "worker_mail_edge_coverage": combined,
        "trace_source_counts": evidence.source_counts,
        "trace_item_count": evidence.item_count,
        "payload_free": payload_free,
        "durable_reopen": {
            "worker_healthy": worker_reopened,
            "identity_healthy": identity_reopened,
            "worker_run_present": durable_run is not None,
            "worker_usage_count": len(durable_usage),
            "worker_artifact_count": len(durable_artifacts),
            "native_span_count": len(durable_spans),
            "mail_observation_count": len(observations),
        },
        "worker_cleanup_deleted_counts": worker_cleanup,
        "identity_cleanup_deleted_count": identity_cleanup,
        "remaining_worker_fixture_counts": worker_remaining,
        "remaining_identity_fixture_rows": identity_remaining,
        "mutation_performed": True,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "scope": "production GatewayWorkerAdapter/WorkerRunController, durable worker evidence, normalized identity mail-edge observations, dual-Postgres reopen/read-back, payload-free worker/mail-edge join, and scoped cleanup",
        "certification_boundary": {
            "gateway_worker_adapter_fixture_dispatch": "checked",
            "durable_worker_usage_artifact_trace": "checked",
            "normalized_delivery_webhook_bounce_observations": "checked",
            "worker_trace_mail_edge_correlation": "checked",
            "worker_postgres_connection_reopen": "checked",
            "identity_postgres_connection_reopen": "checked",
            "payload_free_projection": "checked",
            "scoped_cleanup": "checked",
            "external_provider_callback": "not_checked",
            "selected_live_worker": "not_checked",
            "provider_backed_recovery": "not_checked",
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.worker_dsn, args.identity_dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"gateway-worker mail-edge Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
