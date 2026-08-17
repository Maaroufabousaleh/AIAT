"""Resend relay health/validation adapter.

Resend is never a worker-facing sending API.  Mail submission is performed by
Stalwart; this adapter validates the configured relay and records safe provider
correlation metadata only.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import httpx

from mas_core.observability.mail_edge import MailEdgeObservation, normalize_provider_webhook

from ..models import redact

logger = logging.getLogger(__name__)


class ResendRelayAdapter:
    def __init__(self, *, api_key: str, sending_domain: str, timeout_seconds: float = 15.0, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self.sending_domain = sending_domain
        self.timeout = timeout_seconds
        self._client = client

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
