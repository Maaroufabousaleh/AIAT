"""Resend relay health/validation adapter.

Resend is never a worker-facing sending API.  Mail submission is performed by
Stalwart; this adapter validates the configured relay and records safe provider
correlation metadata only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from mas_core.observability.mail_edge import MailEdgeObservation, normalize_provider_webhook

from ..models import redact

logger = logging.getLogger(__name__)


class ResendRelayAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        sending_domain: str,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
        webhook_signing_secret: str = "",
        webhook_tolerance_seconds: int = 300,
    ) -> None:
        self._api_key = api_key
        self.sending_domain = sending_domain
        self.timeout = timeout_seconds
        self._client = client
        self._webhook_signing_secret = webhook_signing_secret
        self.webhook_tolerance_seconds = webhook_tolerance_seconds

    @staticmethod
    def verify_webhook_signature(
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        signing_secret: str,
        now: int | float | None = None,
        tolerance_seconds: int = 300,
    ) -> bool:
        """Verify the Svix headers used by Resend webhooks.

        The provider signs the exact request bytes as ``svix-id.timestamp.body``.
        Verification is deliberately pure and returns only a boolean: callers
        must not log headers, secrets, or provider payloads.  A short timestamp
        window plus durable event-id idempotency bounds replay without creating
        an additional provider state store.
        """

        if not raw_body or tolerance_seconds <= 0:
            return False
        normalized_headers = {
            str(key).strip().lower(): str(value).strip()
            for key, value in headers.items()
        }
        message_id = normalized_headers.get("svix-id", "")
        timestamp_text = normalized_headers.get("svix-timestamp", "")
        signature_header = normalized_headers.get("svix-signature", "")
        if not message_id or not timestamp_text or not signature_header:
            return False
        try:
            timestamp = int(timestamp_text)
        except (TypeError, ValueError):
            return False
        current_time = time.time() if now is None else float(now)
        if abs(current_time - timestamp) > tolerance_seconds:
            return False

        encoded_secret = str(signing_secret or "").strip()
        if encoded_secret.startswith("whsec_"):
            encoded_secret = encoded_secret[len("whsec_"):]
        if not encoded_secret:
            return False
        try:
            secret = base64.b64decode(
                encoded_secret.encode("ascii"), altchars=b"-_", validate=True
            )
        except (ValueError, UnicodeEncodeError):
            return False
        if not secret:
            return False

        signed_content = (
            message_id.encode("utf-8")
            + b"."
            + timestamp_text.encode("ascii")
            + b"."
            + raw_body
        )
        expected = base64.b64encode(
            hmac.new(secret, signed_content, hashlib.sha256).digest()
        ).decode("ascii")
        for candidate in signature_header.split():
            version, separator, value = candidate.partition(",")
            if separator and version == "v1" and hmac.compare_digest(value, expected):
                return True
        return False

    def verify_configured_webhook_signature(
        self, raw_body: bytes, headers: Mapping[str, str], *, now: int | float | None = None
    ) -> bool:
        """Verify a webhook using the secret injected into this adapter."""

        return self.verify_webhook_signature(
            raw_body,
            headers,
            signing_secret=self._webhook_signing_secret,
            now=now,
            tolerance_seconds=self.webhook_tolerance_seconds,
        )

    async def _request(self, method: str, path: str) -> tuple[dict[str, Any], str]:
        correlation_id = str(uuid4())
        headers = {"Authorization": f"Bearer {self._api_key}", "X-Request-ID": correlation_id}
        try:
            if self._client is not None:
                response = await self._client.request(method, f"https://api.resend.com{path}", headers=headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(method, f"https://api.resend.com{path}", headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError("Resend relay validation unavailable") from exc
        if response.status_code >= 400:
            raise RuntimeError("Resend relay validation rejected")
        try:
            return response.json(), correlation_id
        except ValueError:
            return {}, correlation_id

    async def validate_relay_credentials(self) -> dict[str, Any]:
        _body, correlation_id = await self._request("GET", "/domains")
        return {"valid": True, "correlation_id": correlation_id}

    async def validate_sending_domain(self) -> dict[str, Any]:
        body, correlation_id = await self._request("GET", "/domains")
        domains = body.get("data") or []
        domain = next((item for item in domains if str(item.get("name", "")).lower() == self.sending_domain.lower()), None)
        return {"valid": bool(domain and str(domain.get("status", "")).lower() in {"verified", "active"}), "domain_id": (domain or {}).get("id"), "correlation_id": correlation_id}

    async def test_relay_connection(self) -> dict[str, Any]:
        # The SMTP route is exercised by deployment validation; API validation
        # here confirms the relay account is reachable without sending mail.
        return await self.validate_relay_credentials()

    @staticmethod
    def record_provider_message_id(provider_message_id: str) -> dict[str, str]:
        return {"provider_message_id": provider_message_id}

    @staticmethod
    def record_delivery_event(event: dict[str, Any]) -> dict[str, Any]:
        return redact(event)

    @staticmethod
    def normalize_webhook(
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
        signature_verified: bool = False,
        worker_id: str | None = None,
        outbound_request_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> MailEdgeObservation:
        """Normalize a verified-or-rejected Resend body without retaining it."""

        return normalize_provider_webhook(
            "resend",
            payload,
            event_id=event_id,
            signature_verified=signature_verified,
            worker_id=worker_id,
            outbound_request_id=outbound_request_id,
            trace_id=trace_id,
            span_id=span_id,
        )

    @staticmethod
    def classify_transient_or_permanent_failure(status_code: int | None, error: str | None = None) -> str:
        if status_code is None or status_code >= 500 or status_code == 429:
            return "transient"
        return "permanent"

    async def health_check(self) -> dict[str, Any]:
        return await self.validate_relay_credentials()
