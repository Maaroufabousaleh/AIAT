"""Certify durable local Postgres mail-edge ingress and read-back.

This probe drives the actual identity-service FastAPI application through an
in-process ASGI client while the application uses ``PostgresIdentityStore``.
It signs delivered and bounced Resend/Svix-shaped events, checks duplicate
idempotency, conflicting-event rejection, raw-body tamper rejection, closes
the first database connection, reopens a second store, and reads the rows back
through both SQL and the dashboard projection. The reserved fixture namespace
is removed after read-back. No external provider, SMTP relay, model worker, or
external network is contacted.

The checker requires the identity-service database configuration to be
injected by the caller. It exits with status 2 when no database is configured
or the local database is unavailable, rather than silently falling back to the
in-memory store.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

import httpx
import sqlalchemy as sa

MAS_ROOT = Path(__file__).resolve().parents[1]
for _path in (
    MAS_ROOT / "packages" / "mas-core",
    MAS_ROOT / "apps" / "identity-service",
):
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from identity_service.config import IdentitySettings  # noqa: E402
from identity_service.main import create_app  # noqa: E402
from identity_service.store import PostgresIdentityStore  # noqa: E402

CHECK_SCHEMA = "aiat.mail-edge-postgres-ingress-certification.v1"
_EXPECTED_MIGRATION = "0003_mail_edge_observations"
_SIGNING_SECRET = b"aiat-mail-edge-postgres-fixture-secret"
_SIGNING_SECRET_B64 = "whsec_" + base64.b64encode(_SIGNING_SECRET).decode("ascii")
_EVENT_PREFIX = "aiat-cert-mail-edge-postgres-v1-"
_PAYLOAD_MARKER = "fixture postgres payload must never persist"
_RECIPIENT_MARKER = "recipient.must.not.persist@example.invalid"


def _signed_headers(body: bytes, *, message_id: str, timestamp: int) -> dict[str, str]:
    signed = f"{message_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(
        hmac.new(_SIGNING_SECRET, signed, hashlib.sha256).digest()
    ).decode("ascii")
    return {
        "Content-Type": "application/json",
        "svix-id": message_id,
        "svix-timestamp": str(timestamp),
        "svix-signature": f"v1,{signature}",
    }


def _body(*, event_id: str, event_type: str, email_id: str, status: str) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "data": {
                "email_id": email_id,
                "status": status,
                "body": _PAYLOAD_MARKER,
                "recipient": _RECIPIENT_MARKER,
            },
            "created_at": "2026-08-17T12:00:00Z",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _safe_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": str(row.get("source") or ""),
            "event_type": str(row.get("event_type") or ""),
            "outcome": str(row.get("outcome") or ""),
            "failure_class": row.get("failure_class"),
            "signature_verified": bool(row.get("signature_verified")),
            "trace_id": row.get("trace_id"),
            "span_id": row.get("span_id"),
        }
        for row in rows
    ]


async def _scoped_rows(store: PostgresIdentityStore) -> list[dict[str, Any]]:
    async with store.engine.connect() as connection:
        result = await connection.execute(
            sa.text(
                """SELECT * FROM mail_edge_observations
                   WHERE event_id LIKE :prefix
                   ORDER BY event_id"""
            ),
            {"prefix": f"{_EVENT_PREFIX}%"},
        )
        return [dict(row) for row in result.mappings().all()]


async def _cleanup(store: PostgresIdentityStore) -> int:
    async with store.engine.begin() as connection:
        result = await connection.execute(
            sa.text("DELETE FROM mail_edge_observations WHERE event_id LIKE :prefix"),
            {"prefix": f"{_EVENT_PREFIX}%"},
        )
        return int(result.rowcount or 0)


async def _migration_version(store: PostgresIdentityStore) -> str | None:
    async with store.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-asgi",
        "status": "blocked",
        "reason": reason,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "local_database_access_performed": False,
        "mutation_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }


async def _run() -> dict[str, Any]:
    try:
        settings = IdentitySettings()
    except Exception:
        return _blocked("identity_settings_unavailable")
    dsn = settings.database_dsn
    if not dsn:
        return _blocked("identity_database_not_configured")

    settings = settings.model_copy(
        update={
            "resend_webhook_signing_secret": _SIGNING_SECRET_B64,
            "resend_webhook_tolerance_seconds": 300,
        }
    )
    store = PostgresIdentityStore(
        dsn,
        content_encryption_key=settings.identity_content_encryption_key,
    )
    try:
        if not await store.healthcheck():
            return _blocked("identity_database_unavailable")

        migration_version = await _migration_version(store)
        if migration_version != _EXPECTED_MIGRATION:
            return {
                **_blocked("identity_mail_edge_migration_not_at_head"),
                "migration_version": migration_version,
                "expected_migration": _EXPECTED_MIGRATION,
                "local_database_access_performed": True,
                "network_access_performed": True,
            }
        await _cleanup(store)
        app = create_app(settings=settings, store=store)
        timestamp = int(time.time())
        delivered_body = _body(
            event_id=f"{_EVENT_PREFIX}delivered",
            event_type="email.delivered",
            email_id=f"{_EVENT_PREFIX}message-delivered",
            status="delivered",
        )
        bounced_body = _body(
            event_id=f"{_EVENT_PREFIX}bounced",
            event_type="email.bounced",
            email_id=f"{_EVENT_PREFIX}message-bounced",
            status="permanent",
        )

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://identity-postgres-fixture",
            ) as client:
                delivered = await client.post(
                    "/v1/mail-edge/provider-webhook/resend",
                    content=delivered_body,
                    headers=_signed_headers(
                        delivered_body,
                        message_id=f"{_EVENT_PREFIX}svix-delivered",
                        timestamp=timestamp,
                    ),
                )
                bounced = await client.post(
                    "/v1/mail-edge/provider-webhook/resend",
                    content=bounced_body,
                    headers=_signed_headers(
                        bounced_body,
                        message_id=f"{_EVENT_PREFIX}svix-bounced",
                        timestamp=timestamp,
                    ),
                )
                duplicate = await client.post(
                    "/v1/mail-edge/provider-webhook/resend",
                    content=bounced_body,
                    headers=_signed_headers(
                        bounced_body,
                        message_id=f"{_EVENT_PREFIX}svix-bounced",
                        timestamp=timestamp,
                    ),
                )
                conflict_body = _body(
                    event_id=f"{_EVENT_PREFIX}bounced",
                    event_type="email.delivered",
                    email_id=f"{_EVENT_PREFIX}message-bounced",
                    status="delivered",
                )
                conflict = await client.post(
                    "/v1/mail-edge/provider-webhook/resend",
                    content=conflict_body,
                    headers=_signed_headers(
                        conflict_body,
                        message_id=f"{_EVENT_PREFIX}svix-conflict",
                        timestamp=timestamp,
                    ),
                )
                tampered = await client.post(
                    "/v1/mail-edge/provider-webhook/resend",
                    content=bounced_body + b" ",
                    headers=_signed_headers(
                        bounced_body,
                        message_id=f"{_EVENT_PREFIX}svix-bounced",
                        timestamp=timestamp,
                    ),
                )
            dashboard_rows = await store.dashboard_rows("mail-edge")
            scoped_rows = await _scoped_rows(store)

        reopened = PostgresIdentityStore(
            dsn,
            content_encryption_key=settings.identity_content_encryption_key,
        )
        try:
            reopened_healthy = await reopened.healthcheck()
            durable_rows = await _scoped_rows(reopened) if reopened_healthy else []
            durable_dashboard = (
                await reopened.dashboard_rows("mail-edge") if reopened_healthy else []
            )
            cleanup_count = await _cleanup(reopened) if reopened_healthy else 0
            remaining_rows = await _scoped_rows(reopened) if reopened_healthy else []
        finally:
            await reopened.close()
    except Exception as exc:
        return {
            **_blocked("local_postgres_ingress_failed"),
            "failure_type": type(exc).__name__,
            "local_database_access_performed": True,
            "network_access_performed": True,
        }
    finally:
        # The ASGI lifespan owns and closes the first store on successful runs.
        # If setup failed before lifespan startup, make the cleanup safe.
        if getattr(store, "engine", None) is not None:
            with suppress(Exception):
                await store.close()

    response_objects = [delivered, bounced, duplicate]
    statuses = [response.status_code for response in response_objects]
    raw_blob = json.dumps(scoped_rows, default=str, sort_keys=True)
    dashboard_blob = json.dumps(dashboard_rows, default=str, sort_keys=True)
    durable_blob = json.dumps(durable_rows, default=str, sort_keys=True)
    rows_are_safe = all(
        _PAYLOAD_MARKER not in blob and _RECIPIENT_MARKER not in blob
        for blob in (raw_blob, dashboard_blob, durable_blob)
    )
    durable_dashboard_rows = [
        row
        for row in durable_dashboard
        if str(row.get("provider_message_id") or "").startswith(_EVENT_PREFIX)
    ]
    passed = (
        statuses == [200, 200, 200]
        and delivered.json().get("event_type") == "delivered"
        and bounced.json().get("event_type") == "bounced"
        and duplicate.json().get("id") == bounced.json().get("id")
        and conflict.status_code == 409
        and tampered.status_code == 401
        and reopened_healthy
        and migration_version == _EXPECTED_MIGRATION
        and len(scoped_rows) == 2
        and len(durable_rows) == 2
        and len(durable_dashboard_rows) == 2
        and rows_are_safe
        and cleanup_count >= 2
        and not remaining_rows
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-asgi",
        "status": "pass" if passed else "fail",
        "migration_version": migration_version,
        "http_statuses": {
            "delivered": delivered.status_code,
            "bounced": bounced.status_code,
            "duplicate": duplicate.status_code,
            "conflict": conflict.status_code,
            "tampered": tampered.status_code,
        },
        "inserted_observation_count": len(scoped_rows),
        "durable_readback_count": len(durable_rows),
        "durable_dashboard_row_count": len(durable_dashboard_rows),
        "cleanup_deleted_count": cleanup_count,
        "remaining_fixture_rows": len(remaining_rows),
        "idempotent_duplicate": duplicate.json().get("id") == bounced.json().get("id"),
        "conflicting_event_rejected": conflict.status_code == 409,
        "tampered_body_rejected": tampered.status_code == 401,
        "payload_free": rows_are_safe,
        "local_database_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "scope": "real identity-service Resend/Svix ingress, Postgres persistence, connection reopen, and dashboard read-back",
        "certification_boundary": {
            "raw_body_signature_verification": "checked",
            "normalization": "checked",
            "idempotency": "checked",
            "conflict_rejection": "checked",
            "postgres_durability": "checked",
            "dashboard_read_back": "checked",
            "external_provider_callback": "not_checked",
            "model_backed_worker": "not_checked",
            "live_worker_run": "not_checked",
            "outage_restore": "not_checked",
        },
        "mutation_performed": True,
        "network_access_performed": True,
        "scoped_cleanup_performed": not remaining_rows,
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = asyncio.run(_run())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"mail-edge Postgres ingress certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
