"""Certify durable gateway-worker to mail-edge evidence composition.

This probe runs the production ``GatewayWorkerAdapter`` and
``WorkerRunController`` against AIAT Postgres, persists a payload-free worker
artifact/usage/trace projection, records normalized delivery/webhook/bounce
observations in the identity-service Postgres store, and joins both stores by
worker and trace identity after independent connection reopen. With
``--identity-ingress``, delivered/bounced observations are written through the
real signed identity-service HTTP route, including duplicate/conflict/tamper
checks. With ``--provider-ingress``, a durable outbound attempt supplies the
provider-message correlation and the events are written through the real
Resend/Svix raw-body route, including the same replay/conflict/tamper checks.
The default gateway and mail observations are bounded local fixtures: no
external provider, network endpoint, SMTP relay, sandbox, or live worker is
contacted. ``--provider-recovery`` can be used in this local mode as well; it
injects one transient 429 into the fixture gateway and proves that the real
adapter/controller retry path settles durably without external access. With
``--live-provider`` and explicit opt-in, the checker first
reads the configured gateway's model listing, runs one exact selected model
through the durable worker/controller path, redacts generated content before
durable result persistence, and can exercise the local raw-provider ingress
with ``--provider-ingress``. That mode still does not claim an external mail
callback or delivery.

The checker requires separate worker and identity-service Postgres DSNs.  It
exits with status 2 when either database is not configured, unavailable, or
not at its expected migration head, and never falls back to in-memory storage.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
IDENTITY_ROOT = MAS_ROOT / "apps" / "identity-service"
SCRIPTS_ROOT = MAS_ROOT / "scripts"
for _path in (CORE_ROOT, IDENTITY_ROOT, SCRIPTS_ROOT):
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import check_gateway_worker_provider_live as _provider_live  # noqa: E402
from identity_service.clients.auth import SignedClient  # noqa: E402
from identity_service.config import IdentitySettings  # noqa: E402
from identity_service.main import create_app  # noqa: E402
from identity_service.models import IdentityState  # noqa: E402
from identity_service.store import PostgresIdentityStore  # noqa: E402

from mas_core.llm_gateway import LLMConfig, LLMGatewayClient  # noqa: E402
from mas_core.llm_gateway.client import LLMGatewayError  # noqa: E402
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
LIVE_PROMPT = "Reply with exactly the single word: ready"
LIVE_MAX_TOKENS = 16
LIVE_TEMPERATURE = 0.0
EVENT_PREFIX = "aiat-cert-gateway-mail-edge-postgres-v1-"
PAYLOAD_MARKER = "gateway worker mail-edge postgres payload must never persist"
TIMESTAMP = "2026-08-18T12:00:00Z"
IDENTITY_CLIENT_ID = "aiat-cert-gateway-mail-edge-postgres-client-v1"
IDENTITY_COMPANY_ID = UUID("00000000-0000-4000-a000-000000000b63")
IDENTITY_DOMAIN = "gateway-mail-edge-fixture.invalid"
IDENTITY_IDEMPOTENCY_KEY = "aiat-cert-gateway-mail-edge-postgres-v1-identity"
OUTBOUND_IDEMPOTENCY_KEY = "aiat-cert-gateway-mail-edge-postgres-v1-outbound"
OUTBOUND_MESSAGE_REF = f"{EVENT_PREFIX}message"
_RESEND_SIGNING_SECRET = b"aiat-gateway-worker-mail-edge-postgres-secret"
_RESEND_SIGNING_SECRET_B64 = "whsec_" + base64.b64encode(_RESEND_SIGNING_SECRET).decode("ascii")


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


class _RedactingGateway:
    """Discard generated content while retaining scalar request/usage metadata."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.calls.append(
            {
                "model": str(kwargs.get("model") or ""),
                "max_tokens": int(kwargs.get("max_tokens") or 0),
                "temperature": float(kwargs.get("temperature") or 0),
                "message_count": len(kwargs.get("messages") or []),
            }
        )
        response = await self.delegate.chat_completion(**kwargs)
        return ChatResponse(
            model=str(getattr(response, "model", "") or ""),
            finish_reason=str(getattr(response, "finish_reason", "stop") or "stop"),
            message=ChatMessage(role="assistant", content=None),
            usage=getattr(response, "usage", UsageStats()),
            tool_calls=[],
            extra={},
        )


class _TransientOnceGateway:
    """Inject one bounded transient failure before the real gateway call."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        # Keep the fixture call ledger visible through the retry wrapper.  The
        # live path wraps this object in ``_RedactingGateway`` instead.
        self.calls = getattr(delegate, "calls", [])
        self.attempts = 0
        self.injected = False
        self.forwarded_calls = 0

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.attempts += 1
        if not self.injected:
            self.injected = True
            raise LLMGatewayError(429, "synthetic transient recovery probe")
        self.forwarded_calls += 1
        return await self.delegate.chat_completion(**kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--live-provider",
        action="store_true",
        help="use the explicitly selected external gateway model instead of the fixture gateway",
    )
    parser.add_argument(
        "--allow-external-provider",
        action="store_true",
        default=_provider_live._truthy(os.getenv("AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH")),
        help="explicitly permit one bounded external provider completion",
    )
    parser.add_argument(
        "--gateway-url",
        default=_provider_live._configured_url(),
        help="live AIAT gateway URL for --live-provider",
    )
    parser.add_argument(
        "--api-key",
        default=_provider_live._configured_key(),
        help="live gateway bearer key for --live-provider",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("AIAT_LIVE_WORKER_MODEL", ""),
        help="exact selected model for --live-provider; auto is rejected",
    )
    parser.add_argument(
        "--provider-id",
        default=os.getenv("AIAT_LIVE_WORKER_PROVIDER_ID", "litellm"),
        help="provider identity recorded in live usage metadata",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--identity-ingress",
        action="store_true",
        help="exercise the signed identity-service HTTP ingress for provider events",
    )
    parser.add_argument(
        "--provider-ingress",
        action="store_true",
        help="exercise the raw-body Resend/Svix provider-facing HTTP ingress",
    )
    parser.add_argument(
        "--provider-recovery",
        action="store_true",
        help="inject one transient gateway failure, then retry one bounded call",
    )
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


def _identity_ingress_settings(identity_url: str) -> tuple[IdentitySettings, SignedClient]:
    """Build an isolated development identity client for the local ASGI fixture."""

    client = SignedClient(
        client_id=IDENTITY_CLIENT_ID,
        private_key=Ed25519PrivateKey.generate(),
    )
    settings = IdentitySettings(
        MAS_ENVIRONMENT="development",
        IDENTITY_PROFILE="development",
        identity_database_dsn=identity_url,
        agent_mail_domain="agents.aiat.local",
        outbound_relay_provider="disabled",
        identity_client_public_keys_json=json.dumps(
            {IDENTITY_CLIENT_ID: client.public_key_base64()}
        ),
        identity_client_scopes_json=json.dumps(
            {IDENTITY_CLIENT_ID: ["identity:delegate"]}
        ),
        resend_webhook_signing_secret=_RESEND_SIGNING_SECRET_B64,
    )
    return settings, client


def _ingress_payload(observation: MailEdgeObservation) -> dict[str, Any]:
    """Build a provider-shaped body whose content must not survive normalization."""

    status = "delivered" if observation.event_type == "delivered" else "permanent"
    return {
        "id": observation.event_id,
        "type": f"email.{observation.event_type}",
        "created_at": TIMESTAMP,
        "data": {
            "email_id": observation.provider_message_ref,
            "status": status,
            "reason_code": "fixture" if observation.event_type == "bounced" else None,
            "recipient": "private@example.invalid",
            "body": PAYLOAD_MARKER,
        },
    }


async def _post_signed_provider_event(
    client: httpx.AsyncClient,
    signer: SignedClient,
    *,
    observation: MailEdgeObservation,
    event_id: str | None = None,
    payload_override: dict[str, Any] | None = None,
    raw_override: bytes | None = None,
) -> httpx.Response:
    body = {
        "provider": "resend",
        "payload": payload_override or _ingress_payload(observation),
        "actor": {
            "actor_id": IDENTITY_CLIENT_ID,
            "purpose": "durable gateway worker mail-edge fixture",
        },
        "event_id": event_id or observation.event_id,
        "signature_verified": True,
        "worker_id": WORKER_ID_TEXT,
        "trace_id": TRACE_ID,
        "span_id": observation.span_id,
    }
    signed_raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    raw = raw_override or signed_raw
    headers = {
        "Content-Type": "application/json",
        **signer.sign_headers("POST", "/v1/mail-edge/provider-webhook", signed_raw),
    }
    return await client.post(
        "/v1/mail-edge/provider-webhook",
        content=raw,
        headers=headers,
    )


def _resend_headers(body: bytes, *, message_id: str, timestamp: int) -> dict[str, str]:
    signed = f"{message_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(
        hmac.new(_RESEND_SIGNING_SECRET, signed, hashlib.sha256).digest()
    ).decode("ascii")
    return {
        "Content-Type": "application/json",
        "svix-id": message_id,
        "svix-timestamp": str(timestamp),
        "svix-signature": f"v1,{signature}",
    }


async def _post_raw_provider_event(
    client: httpx.AsyncClient,
    *,
    observation: MailEdgeObservation,
    payload_override: dict[str, Any] | None = None,
    raw_override: bytes | None = None,
) -> httpx.Response:
    raw = json.dumps(
        payload_override or _ingress_payload(observation),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signed_raw = raw_override or raw
    return await client.post(
        "/v1/mail-edge/provider-webhook/resend",
        content=signed_raw,
        headers=_resend_headers(
            raw,
            message_id=f"{EVENT_PREFIX}svix-{observation.event_id}",
            timestamp=int(datetime.now(UTC).timestamp()),
        ),
    )


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
    """Remove this certificate's observations and optional mail fixtures."""

    async with store.engine.begin() as connection:
        observation_ids = (
            await connection.execute(
                sa.text(
                    "SELECT id::text FROM mail_edge_observations "
                    "WHERE event_id LIKE :prefix"
                ),
                {"prefix": f"{EVENT_PREFIX}%"},
            )
        ).scalars().all()
        deleted_audits = 0
        if observation_ids:
            audit_result = await connection.execute(
                sa.text(
                    "DELETE FROM identity_audit_events "
                    "WHERE action = 'mail.provider_event' "
                    "AND target_id = ANY(:target_ids)"
                ).bindparams(sa.bindparam("target_ids", type_=sa.ARRAY(sa.Text))),
                {"target_ids": [str(value) for value in observation_ids]},
            )
            deleted_audits = int(audit_result.rowcount or 0)
        observations = await connection.execute(
            sa.text("DELETE FROM mail_edge_observations WHERE event_id LIKE :prefix"),
            {"prefix": f"{EVENT_PREFIX}%"},
        )
        outbound_ids = (
            await connection.execute(
                sa.text(
                    "SELECT id FROM outbound_mail_requests "
                    "WHERE idempotency_key = :idempotency_key"
                ),
                {"idempotency_key": OUTBOUND_IDEMPOTENCY_KEY},
            )
        ).scalars().all()
        deleted_attempts = 0
        if outbound_ids:
            attempt_result = await connection.execute(
                sa.text(
                    "DELETE FROM outbound_delivery_attempts "
                    "WHERE outbound_request_id = ANY(:outbound_ids)"
                ).bindparams(sa.bindparam("outbound_ids", type_=sa.ARRAY(sa.UUID))),
                {"outbound_ids": outbound_ids},
            )
            deleted_attempts = int(attempt_result.rowcount or 0)
        outbound = await connection.execute(
            sa.text(
                "DELETE FROM outbound_mail_requests "
                "WHERE idempotency_key = :idempotency_key"
            ),
            {"idempotency_key": OUTBOUND_IDEMPOTENCY_KEY},
        )
        identity_ids = (
            await connection.execute(
                sa.text("SELECT id FROM agent_email_identities WHERE worker_id = :worker_id"),
                {"worker_id": WORKER_ID},
            )
        ).scalars().all()
        deleted_transitions = 0
        if identity_ids:
            transition_result = await connection.execute(
                sa.text(
                    "DELETE FROM identity_state_transitions "
                    "WHERE identity_id = ANY(:identity_ids)"
                ).bindparams(sa.bindparam("identity_ids", type_=sa.ARRAY(sa.UUID))),
                {"identity_ids": identity_ids},
            )
            deleted_transitions = int(transition_result.rowcount or 0)
        identities = await connection.execute(
            sa.text("DELETE FROM agent_email_identities WHERE worker_id = :worker_id"),
            {"worker_id": WORKER_ID},
        )
        domains = await connection.execute(
            sa.text(
                "DELETE FROM email_domains d WHERE d.domain = :domain "
                "AND NOT EXISTS (SELECT 1 FROM agent_email_identities i WHERE i.domain_id = d.id)"
            ),
            {"domain": IDENTITY_DOMAIN},
        )
    return sum(
        int(value or 0)
        for value in (
            deleted_audits,
            observations.rowcount,
            deleted_attempts,
            outbound.rowcount,
            deleted_transitions,
            identities.rowcount,
            domains.rowcount,
        )
    )


async def _identity_fixture_counts(store: PostgresIdentityStore) -> dict[str, int]:
    async with store.engine.connect() as connection:
        values = {
            "observations": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM mail_edge_observations "
                    "WHERE event_id LIKE :prefix"
                ),
                {"prefix": f"{EVENT_PREFIX}%"},
            ),
            "outbound_requests": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM outbound_mail_requests "
                    "WHERE idempotency_key = :idempotency_key"
                ),
                {"idempotency_key": OUTBOUND_IDEMPOTENCY_KEY},
            ),
            "delivery_attempts": await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM outbound_delivery_attempts d "
                    "JOIN outbound_mail_requests r ON r.id = d.outbound_request_id "
                    "WHERE r.idempotency_key = :idempotency_key"
                ),
                {"idempotency_key": OUTBOUND_IDEMPOTENCY_KEY},
            ),
            "identities": await connection.scalar(
                sa.text("SELECT count(*) FROM agent_email_identities WHERE worker_id = :worker_id"),
                {"worker_id": WORKER_ID},
            ),
            "domains": await connection.scalar(
                sa.text("SELECT count(*) FROM email_domains WHERE domain = :domain"),
                {"domain": IDENTITY_DOMAIN},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup_identity_client(store: PostgresIdentityStore) -> int:
    """Remove only the ephemeral signed client and its replay nonces."""

    async with store.engine.begin() as connection:
        deleted_nonces = await connection.execute(
            sa.text(
                "DELETE FROM identity_client_nonces WHERE client_id = :client_id"
            ),
            {"client_id": IDENTITY_CLIENT_ID},
        )
        deleted_clients = await connection.execute(
            sa.text(
                "DELETE FROM identity_client_registrations WHERE client_id = :client_id"
            ),
            {"client_id": IDENTITY_CLIENT_ID},
        )
    return int(deleted_nonces.rowcount or 0) + int(deleted_clients.rowcount or 0)


async def _identity_client_rows(store: PostgresIdentityStore) -> int:
    async with store.engine.connect() as connection:
        value = await connection.scalar(
            sa.text(
                "SELECT count(*) FROM identity_client_registrations "
                "WHERE client_id = :client_id"
            ),
            {"client_id": IDENTITY_CLIENT_ID},
        )
    return int(value or 0)


def _fixture_observations(*, outbound_request_id: UUID | None = None) -> list[MailEdgeObservation]:
    delivery = build_mail_edge_observation(
        provider="resend",
        source="delivery_attempt",
        event_id=f"{EVENT_PREFIX}attempt",
        event_type="queued",
        worker_id=WORKER_ID_TEXT,
        outbound_request_id=str(outbound_request_id) if outbound_request_id else None,
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
        outbound_request_id=str(outbound_request_id) if outbound_request_id else None,
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
                "email_id": f"{EVENT_PREFIX}message",
                "status": "permanent",
                "reason_code": "fixture",
                "body": PAYLOAD_MARKER,
            },
        },
        signature_verified=True,
        worker_id=WORKER_ID_TEXT,
        outbound_request_id=str(outbound_request_id) if outbound_request_id else None,
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


async def _prepare_mail_edge_outbound(store: PostgresIdentityStore) -> UUID:
    """Create a bounded durable outbound target for provider-message joins."""

    identity, _ = await store.provision_identity(
        company_id=IDENTITY_COMPANY_ID,
        worker_id=WORKER_ID,
        address=f"gateway-worker-{WORKER_ID.hex[:12]}@{IDENTITY_DOMAIN}",
        alias=None,
        domain=IDENTITY_DOMAIN,
        idempotency_key=IDENTITY_IDEMPOTENCY_KEY,
        quota_mb=100,
    )
    await store.set_identity_state(
        WORKER_ID,
        IdentityState.IDENTITY_ACTIVE,
        {"fixture": True, "source": "gateway-worker-mail-edge-postgres"},
    )
    outbound, _ = await store.create_outbound_request(
        worker_id=WORKER_ID,
        identity_id=identity["id"],
        sender=identity["address"],
        recipients=["provider-target@example.invalid"],
        subject="bounded fixture subject",
        body=PAYLOAD_MARKER,
        recipient_class="fixture",
        idempotency_key=OUTBOUND_IDEMPOTENCY_KEY,
    )
    await store.update_outbound_request(
        outbound["id"],
        state="SUBMITTED",
        provider_message_id=OUTBOUND_MESSAGE_REF,
        provider_correlation_id=f"{EVENT_PREFIX}provider-correlation",
    )
    await store.record_delivery_attempt(
        outbound_request_id=outbound["id"],
        provider_correlation_id=f"{EVENT_PREFIX}provider-correlation",
        provider_message_id=OUTBOUND_MESSAGE_REF,
        outcome="QUEUED",
        trace_id=TRACE_ID,
        span_id=f"{EVENT_PREFIX}attempt-span",
    )
    return UUID(str(outbound["id"]))


async def _run(
    worker_dsn: str | None,
    identity_dsn: str | None,
    *,
    identity_ingress: bool = False,
    provider_ingress: bool = False,
    live_provider: bool = False,
    allow_external_provider: bool = False,
    gateway_url: str | None = None,
    api_key: str | None = None,
    model_id: str | None = None,
    provider_id: str | None = None,
    timeout_s: float = 30.0,
    provider_recovery: bool = False,
    gateway_client: Any | None = None,
    listed_model_ids: set[str] | None = None,
) -> dict[str, Any]:
    if identity_ingress and provider_ingress:
        return _blocked(
            "gateway_worker_mail_edge_ingress_modes_are_mutually_exclusive"
        )
    if live_provider:
        if not allow_external_provider:
            return _blocked(
                "external_provider_dispatch_requires_explicit_opt_in",
                live_provider=True,
                opt_in_environment="AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH",
            )
        selected_model = str(model_id or "").strip()
        if not selected_model or selected_model.lower() == "auto":
            return _blocked(
                "selected_exact_model_id_is_required",
                live_provider=True,
            )
        if not str(provider_id or "").strip():
            return _blocked("provider_id_is_required", live_provider=True)
        if timeout_s < 1 or timeout_s > 120:
            return _blocked(
                "timeout_must_be_between_one_and_120_seconds",
                live_provider=True,
            )
    worker_url = _normalize_dsn(worker_dsn)
    identity_url = _normalize_dsn(identity_dsn)
    if worker_url is None or identity_url is None:
        return _blocked(
            "gateway_worker_mail_edge_postgres_database_not_configured",
            live_provider=live_provider,
            worker_database_configured=worker_url is not None,
            identity_database_configured=identity_url is not None,
        )

    selected_model = str(model_id or "").strip() if live_provider else MODEL_ID
    selected_provider = str(provider_id or "").strip() if live_provider else PROVIDER_ID
    selected_gateway_url = str(gateway_url or "").strip().rstrip("/")
    selected_api_key = str(api_key or "").strip()
    gateway_model_count: int | None = None
    if live_provider and gateway_client is None:
        if not _provider_live._validate_url(selected_gateway_url):
            return _blocked(
                "live_gateway_url_is_missing_or_invalid",
                live_provider=True,
            )
        if not selected_api_key:
            return _blocked(
                "live_gateway_api_key_is_missing",
                live_provider=True,
            )
        try:
            listed_model_ids, gateway_model_count = await _provider_live._listed_models(
                gateway_url=selected_gateway_url,
                api_key=selected_api_key,
                timeout_s=timeout_s,
            )
        except httpx.HTTPStatusError as exc:
            return _blocked(
                "live_gateway_model_listing_rejected",
                live_provider=True,
                gateway_http_status=exc.response.status_code,
                network_access_performed=True,
                external_network_access_performed=True,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return _blocked(
                "live_gateway_model_listing_unavailable",
                live_provider=True,
                error_type=type(exc).__name__,
                network_access_performed=True,
                external_network_access_performed=True,
            )
    elif live_provider:
        gateway_model_count = len(listed_model_ids or set())
    if live_provider and selected_model not in (listed_model_ids or set()):
        return _blocked(
            "selected_model_is_not_listed_by_live_gateway",
            live_provider=True,
            selected_model_id=selected_model,
            gateway_model_count=gateway_model_count,
            network_access_performed=gateway_client is None,
            external_network_access_performed=gateway_client is None,
        )

    worker = AgentStorage(worker_url)
    identity = PostgresIdentityStore(
        identity_url,
        content_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    worker_migration: str | None = None
    identity_migration: str | None = None
    worker_cleanup: dict[str, int] = {}
    identity_cleanup = 0
    identity_client_cleanup = 0
    worker_remaining: dict[str, int] = {}
    identity_remaining = 0
    identity_fixture_remaining: dict[str, int] = {}
    identity_client_remaining = 0
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
    ingress_statuses: dict[str, int] = {}
    ingress_idempotent_duplicate = False
    ingress_conflict_rejected = False
    ingress_tampered_rejected = False
    provider_ingress_statuses: dict[str, int] = {}
    provider_ingress_idempotent_duplicate = False
    provider_ingress_conflict_rejected = False
    provider_ingress_tampered_rejected = False
    live_gateway: LLMGatewayClient | Any | None = gateway_client if live_provider else None
    live_gateway_owned = False
    recovery_gateway: _TransientOnceGateway | None = None
    provider_attempts = 0
    provider_retry_count = 0
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
        if live_provider and live_gateway is None:
            live_gateway = LLMGatewayClient(
                LLMConfig.model_construct(
                    backend="litellm",
                    gateway_url=selected_gateway_url,
                    api_key=selected_api_key,
                    default_model=selected_model,
                    timeout_s=timeout_s,
                    max_retries=1,
                    retry_min_wait_s=0.25,
                    retry_max_wait_s=1.0,
                )
            )
            await live_gateway.start()
            live_gateway_owned = True
        await _cleanup_worker(worker)
        await _cleanup_identity(identity)
        registered = await worker.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_ID,
            adapter_type="aiat_gateway",
            adapter_config={
                "fixture": "gateway-mail-edge-postgres" if not live_provider else None,
                "provider_id": selected_provider,
                "selected_model_id": selected_model,
                "live_provider": live_provider,
            },
            sandbox_profile="standard",
            status="ACTIVE",
            version="fixture-v1" if not live_provider else "live-provider-v1",
            source_repo="internal-fixture" if not live_provider else "configured-gateway",
            source_revision="gateway-mail-edge-postgres-v1" if not live_provider else "operator-selected",
            version_pin="fixture-v1" if not live_provider else "operator-selected",
            evaluation_status="fixture-only" if not live_provider else "operator-opt-in",
            adapter_entrypoint="GatewayWorkerAdapter",
            isolation_mode="native",
            model_mode="aiat_gateway",
        )
        canonical_worker_id = UUID(str(registered["id"]))
        request = WorkerRunRequest(
            run_id=RUN_ID,
            idempotency_key=IDEMPOTENCY_KEY,
            worker_id=str(canonical_worker_id),
            task_type=(
                "gateway_worker_provider_mail_edge_live"
                if live_provider
                else "gateway_worker_mail_edge_postgres_fixture"
            ),
            task_input=(
                {
                    "prompt": LIVE_PROMPT,
                    "max_tokens": LIVE_MAX_TOKENS,
                    "temperature": LIVE_TEMPERATURE,
                }
                if live_provider
                else {
                    "prompt": "return a bounded durable worker/mail-edge fixture answer",
                    "max_tokens": 32,
                    "temperature": 0.2,
                    "private_marker": PAYLOAD_MARKER,
                }
            ),
            trace_id=TRACE_ID,
            span_id=SPAN_ID,
            resolved_model_profile=ModelProfileReference(
                profile_id="gateway-mail-edge-postgres-profile-v1",
                version="operator-selected" if live_provider else "fixture-v1",
                exact_model_id=selected_model,
            ),
            timeout_seconds=max(1, min(120, int(timeout_s))),
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
        if live_provider and live_gateway is not None:
            gateway_delegate: Any = live_gateway
            if provider_recovery:
                recovery_gateway = _TransientOnceGateway(gateway_delegate)
                gateway_delegate = recovery_gateway
            gateway = _RedactingGateway(gateway_delegate)
        else:
            fixture_gateway = _FixtureGateway()
            if provider_recovery:
                recovery_gateway = _TransientOnceGateway(fixture_gateway)
                gateway = recovery_gateway
            else:
                gateway = fixture_gateway
        adapter = GatewayWorkerAdapter(
            worker_id=str(canonical_worker_id),
            provider_id=selected_provider,
            gateway_client=gateway,
            runtime_version=(
                "gateway-worker-provider-mail-edge-live-v1"
                if live_provider
                else "gateway-mail-edge-postgres-fixture-v1"
            ),
            max_provider_retries=1 if provider_recovery else 0,
        )
        try:
            outcome = await controller.execute(
                request,
                adapter,
                worker_registry_id=canonical_worker_id,
            )
        finally:
            await adapter.close()
        gateway_calls = (
            recovery_gateway.attempts
            if recovery_gateway is not None and not live_provider
            else len(getattr(gateway, "calls", []))
        )
        replay_metadata = getattr(outcome.result, "replay_metadata", {}) or {}
        provider_attempts = int(replay_metadata.get("provider_attempts") or 0)
        provider_retry_count = int(replay_metadata.get("provider_retry_count") or 0)
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
            attributes={
                "run_state": outcome.state,
                "fixture": not live_provider,
                "live_provider": live_provider,
            },
        )
        await worker.create_native_trace_span(
            trace_id=TRACE_ID,
            span_id=INTEGRATION_SPAN_ID,
            parent_span_id=SPAN_ID,
            source_kind="integration",
            operation=(
                "mail.provider_webhook.live_provider"
                if live_provider
                else "mail.provider_webhook.fixture"
            ),
            service="identity_service",
            status="success",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            attributes={"event_count": 3, "fixture": not live_provider},
        )
        outbound_request_id: UUID | None = None
        if provider_ingress:
            outbound_request_id = await _prepare_mail_edge_outbound(identity)
        fixture_observations = _fixture_observations(
            outbound_request_id=outbound_request_id
        )
        if identity_ingress:
            settings, signer = _identity_ingress_settings(identity_url)
            app = create_app(settings=settings, store=identity)
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://identity-gateway-mail-edge-fixture",
            ) as client:
                await identity.record_mail_edge_observation(fixture_observations[0])
                delivered_response = await _post_signed_provider_event(
                    client,
                    signer,
                    observation=fixture_observations[1],
                )
                bounced_response = await _post_signed_provider_event(
                    client,
                    signer,
                    observation=fixture_observations[2],
                )
                duplicate_response = await _post_signed_provider_event(
                    client,
                    signer,
                    observation=fixture_observations[2],
                )
                conflict_payload = _ingress_payload(fixture_observations[2])
                conflict_payload["type"] = "email.delivered"
                conflict_response = await _post_signed_provider_event(
                    client,
                    signer,
                    observation=fixture_observations[2],
                    payload_override=conflict_payload,
                )
                signed_body = json.dumps(
                    {
                        "provider": "resend",
                        "payload": _ingress_payload(fixture_observations[2]),
                        "actor": {
                            "actor_id": IDENTITY_CLIENT_ID,
                            "purpose": "durable gateway worker mail-edge fixture",
                        },
                        "event_id": fixture_observations[2].event_id,
                        "signature_verified": True,
                        "worker_id": WORKER_ID_TEXT,
                        "trace_id": TRACE_ID,
                        "span_id": fixture_observations[2].span_id,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                tampered_response = await _post_signed_provider_event(
                    client,
                    signer,
                    observation=fixture_observations[2],
                    raw_override=signed_body + b" ",
                )
            ingress_statuses = {
                "delivered": delivered_response.status_code,
                "bounced": bounced_response.status_code,
                "duplicate": duplicate_response.status_code,
                "conflict": conflict_response.status_code,
                "tampered": tampered_response.status_code,
            }
            ingress_idempotent_duplicate = (
                duplicate_response.json().get("id") == bounced_response.json().get("id")
            )
            ingress_conflict_rejected = conflict_response.status_code == 409
            ingress_tampered_rejected = tampered_response.status_code == 401
        elif provider_ingress:
            settings, _signer = _identity_ingress_settings(identity_url)
            app = create_app(settings=settings, store=identity)
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://identity-gateway-mail-edge-provider-fixture",
            ) as client:
                await identity.record_mail_edge_observation(fixture_observations[0])
                delivered_response = await _post_raw_provider_event(
                    client,
                    observation=fixture_observations[1],
                )
                bounced_response = await _post_raw_provider_event(
                    client,
                    observation=fixture_observations[2],
                )
                duplicate_response = await _post_raw_provider_event(
                    client,
                    observation=fixture_observations[2],
                )
                conflict_payload = _ingress_payload(fixture_observations[2])
                conflict_payload["type"] = "email.delivered"
                conflict_response = await _post_raw_provider_event(
                    client,
                    observation=fixture_observations[2],
                    payload_override=conflict_payload,
                )
                original_raw = json.dumps(
                    _ingress_payload(fixture_observations[2]),
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                tampered_response = await _post_raw_provider_event(
                    client,
                    observation=fixture_observations[2],
                    raw_override=original_raw + b" ",
                )
            provider_ingress_statuses = {
                "delivered": delivered_response.status_code,
                "bounced": bounced_response.status_code,
                "duplicate": duplicate_response.status_code,
                "conflict": conflict_response.status_code,
                "tampered": tampered_response.status_code,
            }
            provider_ingress_idempotent_duplicate = (
                duplicate_response.json().get("id") == bounced_response.json().get("id")
            )
            provider_ingress_conflict_rejected = conflict_response.status_code == 409
            provider_ingress_tampered_rejected = tampered_response.status_code == 401
        else:
            for observation in fixture_observations:
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
            if identity_ingress or provider_ingress:
                identity_client_cleanup = await _cleanup_identity_client(reopened_identity)
            worker_remaining = await _worker_counts(reopened_worker)
            identity_remaining = len(await _identity_rows(reopened_identity))
            identity_fixture_remaining = await _identity_fixture_counts(reopened_identity)
            identity_client_remaining = await _identity_client_rows(reopened_identity)
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
        if identity_ingress or provider_ingress:
            with suppress(Exception):
                await _cleanup_identity_client(identity)
        with suppress(Exception):
            await identity.close()
        if live_gateway_owned and live_gateway is not None:
            with suppress(Exception):
                await live_gateway.stop()

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
    payload_projection: dict[str, Any] = {
        "evidence": evidence.model_dump(mode="json"),
        "mail_rows": durable_mail_rows,
        "spans": durable_spans,
    }
    if live_provider:
        payload_projection["durable_run"] = durable_run
    payload_free = PAYLOAD_MARKER not in json.dumps(
        payload_projection,
        default=str,
        sort_keys=True,
    )
    stored_output = ((durable_run or {}).get("result_json") or {}).get("output")
    generated_text_retained = bool(
        live_provider
        and isinstance(stored_output, dict)
        and str(stored_output.get("text") or "").strip()
    )
    run_state = str((run_row or {}).get("state") or "unknown")
    usage = durable_usage[0] if durable_usage else {}
    passed = all(
        (
            worker_migration == EXPECTED_WORKER_MIGRATION,
            identity_migration == EXPECTED_IDENTITY_MIGRATION,
            outcome is not None and outcome.state == "SUCCEEDED",
            run_state == "SUCCEEDED",
            gateway_calls == (2 if provider_recovery else 1),
            provider_attempts == (2 if provider_recovery else 1),
            provider_retry_count == (1 if provider_recovery else 0),
            durable_run is not None,
            len(durable_usage) == 1,
            len(durable_artifacts) == 1,
            len(durable_spans) >= 3,
            len(observations) == 3,
            combined["status"] == "pass",
            usage.get("provider_id") == selected_provider,
            usage.get("exact_model_id") == selected_model,
            payload_free,
            not generated_text_retained,
            worker_reopened,
            identity_reopened,
            worker_remaining == {"workers": 0, "runs": 0, "artifacts": 0, "usage": 0, "spans": 0},
            identity_remaining == 0,
            identity_fixture_remaining
            == {
                "observations": 0,
                "outbound_requests": 0,
                "delivery_attempts": 0,
                "identities": 0,
                "domains": 0,
            },
            sum(worker_cleanup.values()) >= 5,
            identity_cleanup >= 3,
            not identity_ingress
            or (
                ingress_statuses == {
                    "delivered": 200,
                    "bounced": 200,
                    "duplicate": 200,
                    "conflict": 409,
                    "tampered": 401,
                }
                and ingress_idempotent_duplicate
                and ingress_conflict_rejected
                and ingress_tampered_rejected
                and identity_client_cleanup >= 1
                and identity_client_remaining == 0
            ),
            not provider_ingress
            or (
                provider_ingress_statuses
                == {
                    "delivered": 200,
                    "bounced": 200,
                    "duplicate": 200,
                    "conflict": 409,
                    "tampered": 401,
                }
                and provider_ingress_idempotent_duplicate
                and provider_ingress_conflict_rejected
                and provider_ingress_tampered_rejected
                and identity_client_cleanup >= 1
                and identity_client_remaining == 0
            ),
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "coverage_schema": WORKER_MAIL_EDGE_COVERAGE_SCHEMA,
        "mode": (
            "live-dual-postgres-worker-mail-edge-provider-recovery"
            if live_provider and provider_recovery
            else "local-dual-postgres-worker-mail-edge-provider-recovery"
            if provider_recovery
            else "live-dual-postgres-worker-mail-edge"
            if live_provider
            else "local-dual-postgres-worker-mail-edge"
        ),
        "identity_ingress": identity_ingress,
        "provider_ingress": provider_ingress,
        "live_provider": live_provider,
        "provider_recovery": provider_recovery,
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "worker_migration_version": worker_migration,
        "identity_migration_version": identity_migration,
        "controller_terminal_state": outcome.state if outcome is not None else None,
        "run_state": run_state,
        "gateway_call_count": gateway_calls,
        "provider_attempts": provider_attempts,
        "provider_retry_count": provider_retry_count,
        "provider_recovery_injected": bool(recovery_gateway and recovery_gateway.injected),
        "external_provider_completion_attempt_count": (
            recovery_gateway.forwarded_calls
            if recovery_gateway is not None and live_provider
            else gateway_calls
            if live_provider and gateway_client is None
            else 0
        ),
        "worker_usage_count": len(durable_usage),
        "worker_artifact_count": len(durable_artifacts),
        "native_span_count": len(durable_spans),
        "mail_observation_count": len(observations),
        "worker_id": WORKER_ID_TEXT,
        "trace_id": TRACE_ID,
        "provider_id": selected_provider,
        "exact_model_id": selected_model,
        "gateway_model_count": gateway_model_count,
        "external_provider_call_performed": bool(
            live_provider and gateway_client is None and gateway_calls and outcome is not None
            and outcome.state == "SUCCEEDED"
        ),
        "generated_text_retained": generated_text_retained,
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
        "identity_client_cleanup_deleted_count": identity_client_cleanup,
        "identity_ingress_statuses": ingress_statuses,
        "identity_ingress_idempotent_duplicate": ingress_idempotent_duplicate,
        "identity_ingress_conflict_rejected": ingress_conflict_rejected,
        "identity_ingress_tampered_rejected": ingress_tampered_rejected,
        "provider_ingress_statuses": provider_ingress_statuses,
        "provider_ingress_idempotent_duplicate": provider_ingress_idempotent_duplicate,
        "provider_ingress_conflict_rejected": provider_ingress_conflict_rejected,
        "provider_ingress_tampered_rejected": provider_ingress_tampered_rejected,
        "identity_fixture_remaining": identity_fixture_remaining,
        "remaining_worker_fixture_counts": worker_remaining,
        "remaining_identity_fixture_rows": identity_remaining,
        "remaining_identity_client_rows": identity_client_remaining,
        "mutation_performed": True,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": bool(live_provider and gateway_client is None),
        "external_provider_mutation_performed": False,
        "scope": "production GatewayWorkerAdapter/WorkerRunController, durable worker evidence, normalized identity mail-edge observations, optional signed or Resend/Svix raw-body ingress, provider-message trace correlation, dual-Postgres reopen/read-back, payload-free worker/mail-edge join, and scoped cleanup",
        "certification_boundary": {
            "gateway_worker_adapter_fixture_dispatch": "not_checked" if live_provider else "checked",
            "selected_live_worker": "checked" if live_provider else "not_checked",
            "external_provider_model_listing": "checked" if live_provider else "not_checked",
            "external_provider_dispatch": "checked" if live_provider else "not_checked",
            "durable_worker_usage_artifact_trace": "checked",
            "normalized_delivery_webhook_bounce_observations": "checked",
            "identity_signed_http_ingress": "checked" if identity_ingress else "not_checked",
            "resend_raw_body_provider_ingress": "checked" if provider_ingress else "not_checked",
            "provider_message_trace_correlation": "checked" if provider_ingress else "not_checked",
            "worker_trace_mail_edge_correlation": "checked",
            "worker_postgres_connection_reopen": "checked",
            "identity_postgres_connection_reopen": "checked",
            "payload_free_projection": "checked",
            "scoped_cleanup": "checked",
            "external_provider_callback": "not_checked",
            "provider_backed_recovery": "checked" if provider_recovery else "not_checked",
            "external_provider_transient_recovery": (
                "checked" if live_provider and provider_recovery else "not_checked"
            ),
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(
        _run(
            args.worker_dsn,
            args.identity_dsn,
            identity_ingress=args.identity_ingress,
            provider_ingress=args.provider_ingress,
            live_provider=args.live_provider,
            allow_external_provider=args.allow_external_provider,
            gateway_url=args.gateway_url,
            api_key=args.api_key,
            model_id=args.model,
            provider_id=args.provider_id,
            timeout_s=args.timeout,
            provider_recovery=args.provider_recovery,
        )
    )
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"gateway-worker mail-edge Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
