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
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
        return {
            "Content-Type": "application/json",
            "X-AIAT-Signature-Version": _VERSION,
            "X-AIAT-Client-ID": self.config.client_id,
            "X-AIAT-Timestamp": str(timestamp),
            "X-AIAT-Nonce": nonce,
            "X-AIAT-Signature": signature,
        }

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
