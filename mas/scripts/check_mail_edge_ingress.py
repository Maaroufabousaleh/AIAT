"""Certify the real local mail-edge provider ingress boundary.

This probe drives the actual identity-service FastAPI application through an
in-process ASGI client with the real ``ResendRelayAdapter``,
``IdentityService``, and ``InMemoryIdentityStore``. It signs two provider
events, verifies duplicate idempotency and conflicting-event rejection, checks
raw-body tamper rejection, and reads the normalized rows back through the
store's dashboard projection. No provider, SMTP relay, database, or external
network is contacted. The result is deterministic integration evidence, not a
live provider or worker-run certificate.
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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

MAS_ROOT = Path(__file__).resolve().parents[1]
for _path in (
    MAS_ROOT / "packages" / "mas-core",
    MAS_ROOT / "apps" / "identity-service",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from identity_service.config import IdentitySettings  # noqa: E402
from identity_service.main import create_app  # noqa: E402
from identity_service.store import InMemoryIdentityStore  # noqa: E402

CHECK_SCHEMA = "aiat.mail-edge-ingress-certification.v1"
_SIGNING_SECRET = b"aiat-mail-edge-fixture-secret"
_SIGNING_SECRET_B64 = "whsec_" + base64.b64encode(_SIGNING_SECRET).decode("ascii")
_PAYLOAD_MARKER = "fixture payload must never persist"


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
                "recipient": "recipient.must.not.persist@example.invalid",
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


async def _run() -> dict[str, Any]:
    settings = IdentitySettings(
        resend_webhook_signing_secret=_SIGNING_SECRET_B64,
        resend_webhook_tolerance_seconds=300,
    )
    store = InMemoryIdentityStore()
    app = create_app(settings=settings, store=store)
    timestamp = int(time.time())
    delivered_body = _body(
        event_id="mail-edge-fixture-delivered",
        event_type="email.delivered",
        email_id="fixture-message-delivered",
        status="delivered",
    )
    bounced_body = _body(
        event_id="mail-edge-fixture-bounced",
        event_type="email.bounced",
        email_id="fixture-message-bounced",
        status="permanent",
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://identity-fixture"
        ) as client:
            delivered = await client.post(
                "/v1/mail-edge/provider-webhook/resend",
                content=delivered_body,
                headers=_signed_headers(
                    delivered_body,
                    message_id="svix-fixture-delivered",
                    timestamp=timestamp,
                ),
            )
            bounced = await client.post(
                "/v1/mail-edge/provider-webhook/resend",
                content=bounced_body,
                headers=_signed_headers(
                    bounced_body,
                    message_id="svix-fixture-bounced",
                    timestamp=timestamp,
                ),
            )
            duplicate = await client.post(
                "/v1/mail-edge/provider-webhook/resend",
                content=bounced_body,
                headers=_signed_headers(
                    bounced_body,
                    message_id="svix-fixture-bounced",
                    timestamp=timestamp,
                ),
            )
            conflict_body = _body(
                event_id="mail-edge-fixture-bounced",
                event_type="email.delivered",
                email_id="fixture-message-bounced",
                status="delivered",
            )
            conflict = await client.post(
                "/v1/mail-edge/provider-webhook/resend",
                content=conflict_body,
                headers=_signed_headers(
                    conflict_body,
                    message_id="svix-fixture-conflict",
                    timestamp=timestamp,
                ),
            )
            tampered = await client.post(
                "/v1/mail-edge/provider-webhook/resend",
                content=bounced_body + b" ",
                headers=_signed_headers(
                    bounced_body,
                    message_id="svix-fixture-bounced",
                    timestamp=timestamp,
                ),
            )

        rows = await store.dashboard_rows("mail-edge")
        raw_store = json.dumps(
            list(store.mail_edge_observations.values()), default=str, sort_keys=True
        )
        raw_audit = json.dumps(store.audit, default=str, sort_keys=True)

    response_objects = [delivered, bounced, duplicate]
    response_json = [response.json() if response.content else {} for response in response_objects]
    event_ids = {
        str(payload.get("event_id") or "")
        for payload in response_json
        if isinstance(payload, Mapping)
    }
    statuses = [response.status_code for response in response_objects]
    rows_are_safe = all(
        isinstance(row, Mapping)
        and "provider" not in row
        and _PAYLOAD_MARKER not in json.dumps(row, default=str)
        for row in rows
    )
    passed = (
        statuses == [200, 200, 200]
        and delivered.json().get("event_type") == "delivered"
        and bounced.json().get("event_type") == "bounced"
        and duplicate.json().get("id") == bounced.json().get("id")
        and conflict.status_code == 409
        and tampered.status_code == 401
        and len(store.mail_edge_observations) == 2
        and len(rows) == 2
        and event_ids == {"mail-edge-fixture-delivered", "mail-edge-fixture-bounced"}
        and rows_are_safe
        and _PAYLOAD_MARKER not in raw_store
        and _PAYLOAD_MARKER not in raw_audit
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-asgi-fixture",
        "status": "pass" if passed else "fail",
        "http_statuses": {
            "delivered": delivered.status_code,
            "bounced": bounced.status_code,
            "duplicate": duplicate.status_code,
            "conflict": conflict.status_code,
            "tampered": tampered.status_code,
        },
        "stored_observation_count": len(store.mail_edge_observations),
        "dashboard_row_count": len(rows),
        "dashboard_rows": _safe_rows(rows),
        "idempotent_duplicate": duplicate.json().get("id") == bounced.json().get("id"),
        "conflicting_event_rejected": conflict.status_code == 409,
        "tampered_body_rejected": tampered.status_code == 401,
        "payload_free": rows_are_safe and _PAYLOAD_MARKER not in raw_store and _PAYLOAD_MARKER not in raw_audit,
        "fixture_store_mutated": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "scope": "real identity-service Resend/Svix ingress, normalized in-memory persistence, and dashboard read-back",
        "certification_boundary": {
            "raw_body_signature_verification": "checked",
            "normalization": "checked",
            "idempotency": "checked",
            "conflict_rejection": "checked",
            "dashboard_read_back": "checked",
            "postgres_durability": "not_checked",
            "external_provider_callback": "not_checked",
            "model_backed_worker": "not_checked",
            "live_worker_run": "not_checked",
            "outage_restore": "not_checked",
        },
        "mutation_performed": False,
        "network_access_performed": False,
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
        print(f"mail-edge ingress certification: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
