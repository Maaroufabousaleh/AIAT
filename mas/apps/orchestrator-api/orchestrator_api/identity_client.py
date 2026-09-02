"""Signed laptop/control-plane client for the self-hosted identity-service.

This client intentionally exposes business operations only.  It contains no
identity database DSN and no method for obtaining mailbox, Stalwart, Resend,
or browser-cookie credentials.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mas_core.observability.tracing import (
    current_trace_id,
    is_safe_span_id,
    is_safe_trace_id,
)

_VERSION = "aiat.identity.v1"


class CursorStorage(Protocol):
    async def get_identity_reconciliation_cursor(self, client_id: str) -> int: ...
    async def set_identity_reconciliation_cursor(self, client_id: str, cursor: int) -> None: ...


@dataclass(frozen=True)
class IdentityClientConfig:
    url: str
    client_id: str
    private_key_b64: str
    timeout_seconds: float = 15.0

    @classmethod
    def from_environment(cls) -> IdentityClientConfig:
        url = os.getenv("IDENTITY_SERVICE_URL", "").rstrip("/")
        client_id = os.getenv("AIAT_IDENTITY_CLIENT_ID", "operator-laptop")
        private_key_b64 = os.getenv("AIAT_IDENTITY_CLIENT_PRIVATE_KEY", "")
        if not url or not private_key_b64:
            raise RuntimeError("IDENTITY_SERVICE_URL and AIAT_IDENTITY_CLIENT_PRIVATE_KEY are required for identity operations")
        return cls(url=url, client_id=client_id, private_key_b64=private_key_b64)


class SignedIdentityClient:
    def __init__(self, config: IdentityClientConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        parsed_url = urlsplit(config.url)
        if (
            not parsed_url.hostname
            or parsed_url.scheme not in {"http", "https"}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("IDENTITY_SERVICE_URL must be an origin without credentials, query, or fragment")
        environment = os.getenv("MAS_ENVIRONMENT", "development").strip().lower()
        if environment in {"production", "prod", "staging"} and parsed_url.scheme != "https":
            raise ValueError("IDENTITY_SERVICE_URL must use HTTPS in production")
        try:
            raw_key = base64.b64decode(config.private_key_b64, validate=True)
            if len(raw_key) != 32:
                raise ValueError
            self._key = Ed25519PrivateKey.from_private_bytes(raw_key)
        except Exception as exc:
            raise ValueError("AIAT_IDENTITY_CLIENT_PRIVATE_KEY must be a 32-byte base64 Ed25519 key") from exc
        self._client = client

    def _headers(self, method: str, path: str, raw_body: bytes) -> dict[str, str]:
        timestamp = int(time.time())
        nonce = str(uuid4())
        digest = hashlib.sha256(raw_body).hexdigest()
        canonical = f"{_VERSION}\n{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()
        signature = base64.b64encode(self._key.sign(canonical)).decode()
        headers = {
            "Content-Type": "application/json",
            "X-AIAT-Signature-Version": _VERSION,
            "X-AIAT-Client-ID": self.config.client_id,
            "X-AIAT-Timestamp": str(timestamp),
            "X-AIAT-Nonce": nonce,
            "X-AIAT-Signature": signature,
        }
        trace_id = current_trace_id()
        if trace_id:
            headers["X-AIAT-Trace-ID"] = trace_id
        return headers

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        raw_body = json.dumps(body or {}, separators=(",", ":"), sort_keys=True, default=str).encode()
        try:
            if self._client is not None:
                response = await self._client.request(method, f"{self.config.url}{path}", content=raw_body, headers=self._headers(method, path, raw_body), timeout=self.config.timeout_seconds)
            else:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds, follow_redirects=False) as client:
                    response = await client.request(method, f"{self.config.url}{path}", content=raw_body, headers=self._headers(method, path, raw_body))
        except httpx.HTTPError as exc:
            raise RuntimeError("identity service is unavailable") from exc
        if response.status_code >= 400:
            # Do not include a possibly secret-bearing upstream body in a
            # control-plane error or worker-visible tool response.
            raise RuntimeError(f"identity service rejected operation ({response.status_code})")
        return response.json()

    async def provision_worker(self, *, company_id: UUID, worker_id: UUID, actor_id: str, purpose: str, friendly_alias: str | None = None, mailbox_class: str = "permanent") -> dict[str, Any]:
        return await self.request("POST", "/v1/worker-identities/provision", {"company_id": str(company_id), "worker_id": str(worker_id), "actor": {"actor_id": actor_id, "purpose": purpose}, "idempotency_key": f"mailbox:{company_id}:{worker_id}", "friendly_alias": friendly_alias, "mailbox_class": mailbox_class})

    async def suspend_worker(self, *, worker_id: UUID, actor_id: str, purpose: str) -> dict[str, Any]:
        return await self.request("POST", f"/v1/worker-identities/{worker_id}/suspend", {"actor": {"actor_id": actor_id, "purpose": purpose}})

    async def archive_worker(self, *, worker_id: UUID, actor_id: str, purpose: str) -> dict[str, Any]:
        return await self.request("POST", f"/v1/worker-identities/{worker_id}/archive", {"actor": {"actor_id": actor_id, "purpose": purpose}})

    async def reconcile(self, storage: CursorStorage, *, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await storage.get_identity_reconciliation_cursor(self.config.client_id)
        response = await self.request("POST", "/v1/sync/events", {"cursor": cursor, "limit": limit})
        events = list(response.get("events") or [])
        next_cursor = int(response.get("next_cursor", cursor))
        # Storing only after a complete signed response keeps replay safe. Event
        # consumers must remain idempotent by event id/sequence.
        await storage.set_identity_reconciliation_cursor(self.config.client_id, next_cursor)
        await self.request("POST", "/v1/sync/ack", {"cursor": next_cursor})
        return events

    async def reconcile_worker_lifecycle(self, storage: Any, *, limit: int = 100) -> list[dict[str, Any]]:
        """Apply identity outbox events idempotently before advancing the cursor."""
        cursor = await storage.get_identity_reconciliation_cursor(self.config.client_id)
        response = await self.request("POST", "/v1/sync/events", {"cursor": cursor, "limit": limit})
        processed: list[dict[str, Any]] = []
        for event in list(response.get("events") or []):
            payload = event.get("payload_json") or event.get("payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            worker_id = payload.get("worker_id")
            if worker_id:
                state = {
                    "mailbox.provisioned": "IDENTITY_VERIFYING",
                    "mailbox.identity_active": "IDENTITY_ACTIVE",
                    "mailbox.provisioning_failed": "IDENTITY_PROVISIONING_FAILED",
                    "mailbox.suspended": "SUSPENDED",
                    "mailbox.archived": "ARCHIVED",
                }.get(str(event.get("event_type")))
                if state:
                    await storage.upsert_worker_identity_lifecycle(
                        worker_id=UUID(str(worker_id)),
                        state=state,
                        identity_address=payload.get("address"),
                        last_event_sequence=int(event["sequence"]),
                        failure_code=payload.get("error_code"),
                        evidence={"event_id": str(event.get("id")), "event_type": event.get("event_type")},
                    )
            # Cursor updates are monotonic and happen only after this event's
            # state mirror has committed. Replays remain safe by sequence.
            await storage.set_identity_reconciliation_cursor(self.config.client_id, int(event["sequence"]))
            await self.request("POST", "/v1/sync/ack", {"cursor": int(event["sequence"])})
            processed.append(event)
        return processed

    async def list_mail_delivery_observations(
        self,
        *,
        since: datetime | None = None,
        limit: int = 10_000,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return safe scalar outbound-delivery observations for SLO reads.

        The identity service remains the mail authority.  This method consumes
        its admin dashboard projection and deliberately drops recipients,
        subjects, provider IDs, provider correlation IDs, sanitized error text,
        and any message content before returning a tiny SLO/trace-shaped row.
        Safe AIAT trace/span IDs are retained only when present so the
        orchestrator can correlate a mail delivery attempt without importing
        identity-service payloads.
        """

        requested_trace_id = str(trace_id or "").strip() or None
        if requested_trace_id and not is_safe_trace_id(requested_trace_id):
            return []
        response = await self.request(
            "POST",
            "/v1/dashboard/mail-relay",
            {"limit": max(1, min(int(limit), 10_000))},
        )
        rows = response.get("items") if isinstance(response, dict) else []
        observations: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            row_trace_id = str(row.get("trace_id") or "").strip()
            if requested_trace_id and row_trace_id != requested_trace_id:
                continue
            occurred_at = row.get("occurred_at") or row.get("attempted_at")
            if since is not None and occurred_at is not None:
                try:
                    parsed = (
                        occurred_at
                        if isinstance(occurred_at, datetime)
                        else datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
                    )
                    parsed = parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
                    if parsed < since:
                        continue
                except (TypeError, ValueError):
                    continue
            outcome = str(row.get("outcome") or "").strip().lower()
            source_kind = str(row.get("source") or "").strip().lower()
            source = (
                "identity_outbound_delivery_attempts"
                if not source_kind or source_kind == "delivery_attempt"
                else f"identity_mail_edge_{source_kind}"
            )
            observation = {
                    "id": str(row.get("id") or row.get("outbound_request_id") or "unknown"),
                    "status": "success"
                    if outcome in {"success", "submitted", "sent", "delivered", "accepted"}
                    else "failed",
                    "occurred_at": occurred_at,
                    "source": source,
                }
            event_type = str(row.get("event_type") or "").strip().lower()
            if event_type in {"queued", "sent", "delivered", "deferred", "bounced", "complained", "failed", "unknown"}:
                observation["event_type"] = event_type
            if is_safe_trace_id(row_trace_id):
                observation["trace_id"] = row_trace_id
            row_span_id = str(row.get("span_id") or "").strip()
            if is_safe_span_id(row_span_id):
                observation["span_id"] = row_span_id
            observations.append(observation)
        return observations
