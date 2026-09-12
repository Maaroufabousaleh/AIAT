"""Cloudflare Email Worker inbound adapter and deterministic local fixture.

The production adapter talks only to the narrow AIAT mail-edge API.  It never
holds a Cloudflare account token and never exposes a provider administration
endpoint.  ``CloudflareEdgeFixture`` mirrors the default Worker/D1 chunk
contract without network access; an explicit fixture option retains the
optional R2 compatibility path for provider regression tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4

import httpx

from ..base import MailProviderError

_ADDRESS_RE = re.compile(r"^[^@\s<>\x00-\x1f\x7f]+@[^@\s<>\x00-\x1f\x7f]+$")
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_MAX_ADDRESS_LENGTH = 320
_MAX_HEADER_LENGTH = 998
_DEFAULT_MAX_MESSAGE_BYTES = 10 * 1024 * 1024
# A 25 MiB raw message is base64-expanded in the signed JSON response. Keep
# enough headroom for that expansion plus bounded metadata without accepting
# unbounded provider responses.
_MAX_API_RESPONSE_BYTES = 36 * 1024 * 1024
_AUTH_VERSION = "aiat.mail-edge.v1"
_D1_RAW_CHUNK_SIZE = 256 * 1024


def normalize_email_address(value: str) -> str:
    """Normalize an SMTP envelope address and reject ambiguous input."""

    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate) > _MAX_ADDRESS_LENGTH
        or not _ADDRESS_RE.fullmatch(candidate)
        or candidate.count("@") != 1
    ):
        raise ValueError("recipient is not a safe email address")
    local, domain = candidate.rsplit("@", 1)
    if not local or "." not in domain and domain.lower() not in {"localhost", "invalid"}:
        # Local development domains may be single-label; production DNS names
        # are still validated by configuration and deployment preflight.
        raise ValueError("recipient domain is not valid")
    return f"{local.casefold()}@{domain.casefold().rstrip('.') }"


def _bounded(value: Any, *, max_length: int, fallback: str = "") -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        return text[:max_length]
    return text or fallback


def _header_message_id(raw: bytes) -> str | None:
    try:
        parsed = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        value = _bounded(parsed.get("Message-ID"), max_length=300)
        return value or None
    except Exception:
        return None


def _header_sender(parsed: Any) -> str | None:
    try:
        addresses = getaddresses([str(parsed.get("From", ""))])
    except Exception:
        return None
    for _display, address in addresses:
        candidate = address.strip()
        if candidate and _ADDRESS_RE.fullmatch(candidate):
            return normalize_email_address(candidate)
    return None


def _part_content(part: Any) -> str:
    try:
        value = part.get_content()
    except Exception:
        try:
            payload = part.get_payload(decode=True)
            value = payload.decode(part.get_content_charset() or "utf-8", "replace") if payload else ""
        except Exception:
            return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def normalize_raw_message(
    raw: bytes,
    *,
    message_id: str,
    envelope_recipient: str,
    received_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Parse bounded MIME into the existing provider-neutral mail view.

    The envelope recipient is supplied by the Worker and is intentionally used
    for ``to``.  A forged ``To`` header cannot move a message between workers.
    Malformed MIME remains readable as a bounded empty-body message and keeps a
    safe ``parse_error`` marker for operators.
    """

    recipient = normalize_email_address(envelope_recipient)
    parse_error = False
    try:
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:
        parsed = None
        parse_error = True

    sender: str | None = None
    subject = ""
    text_body = ""
    html_body = ""
    date_value: datetime | None = None
    if parsed is not None:
        sender = _header_sender(parsed)
        subject = _bounded(parsed.get("Subject"), max_length=_MAX_HEADER_LENGTH)
        try:
            date_value = parsedate_to_datetime(str(parsed.get("Date"))) if parsed.get("Date") else None
        except (TypeError, ValueError, IndexError, OverflowError):
            date_value = None
        try:
            parts = list(parsed.walk()) if parsed.is_multipart() else [parsed]
            for part in parts:
                if part.is_multipart():
                    continue
                content_type = str(part.get_content_type()).lower()
                content = _part_content(part)[:100_000]
                if content_type == "text/plain" and not text_body:
                    text_body = content
                elif content_type == "text/html" and not html_body:
                    html_body = content
            if not text_body and not html_body and not parsed.is_multipart():
                text_body = _part_content(parsed)[:100_000]
        except Exception:
            parse_error = True

    if isinstance(received_at, datetime):
        timestamp = received_at
    elif received_at:
        try:
            timestamp = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(UTC)
    else:
        timestamp = date_value or datetime.now(UTC)
    timestamp = timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    sender_view = [{"email": sender}] if sender else []
    body_values: dict[str, dict[str, Any]] = {}
    if text_body:
        body_values["text"] = {"value": text_body, "isTruncated": len(text_body) >= 100_000}
    if html_body:
        body_values["html"] = {"value": html_body, "isTruncated": len(html_body) >= 100_000}
    preview = (text_body or html_body).replace("\n", " ").strip()[:200]
    view: dict[str, Any] = {
        "id": message_id,
        "receivedAt": timestamp.isoformat(),
        "from": sender_view,
        # This is the SMTP envelope recipient, never the untrusted To header.
        "to": [{"email": recipient}],
        "subject": subject,
        "preview": preview,
        "bodyValues": body_values,
        "envelope_recipient": recipient,
    }
    if parse_error:
        view["parse_error"] = True
    return view


class CloudflareProviderError(MailProviderError):
    """Sanitized Cloudflare mail-edge failure."""


class CloudflareInboundAdapter:
    """Signed client for the AIAT Cloudflare Email Worker API."""

    provider_name = "cloudflare"

    def __init__(
        self,
        *,
        edge_url: str,
        auth_secret: str,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
        max_message_bytes: int = _DEFAULT_MAX_MESSAGE_BYTES,
        auth_tolerance_seconds: int = 300,
    ) -> None:
        parsed = urlsplit(edge_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("CLOUDFLARE_MAIL_EDGE_URL must be an origin without credentials or query")
        if max_message_bytes < 1024 or max_message_bytes > 25 * 1024 * 1024:
            raise ValueError("mail-edge message limit is outside the supported range")
        self.edge_url = edge_url.rstrip("/")
        self._auth_secret = str(auth_secret or "")
        self.timeout = timeout_seconds
        self._client = client
        self.max_message_bytes = max_message_bytes
        self.auth_tolerance_seconds = auth_tolerance_seconds

    def _headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = str(uuid4())
        digest = hashlib.sha256(body).hexdigest()
        canonical = f"{_AUTH_VERSION}\n{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()
        signature = base64.b64encode(hmac.new(self._auth_secret.encode(), canonical, hashlib.sha256).digest()).decode()
        return {
            "Content-Type": "application/json",
            "X-AIAT-Mail-Edge-Version": _AUTH_VERSION,
            "X-AIAT-Mail-Edge-Timestamp": timestamp,
            "X-AIAT-Mail-Edge-Nonce": nonce,
            "X-AIAT-Mail-Edge-Signature": signature,
        }

    async def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> tuple[dict[str, Any], str]:
        body = json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":")).encode()
        correlation_id = str(uuid4())
        headers = self._headers(method, path, body)
        headers["X-Request-ID"] = correlation_id
        try:
            if self._client is not None:
                response = await self._client.request(
                    method, f"{self.edge_url}{path}", content=body, headers=headers, timeout=self.timeout
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                    response = await client.request(
                        method, f"{self.edge_url}{path}", content=body, headers=headers
                    )
        except httpx.TimeoutException as exc:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_TIMEOUT", "mail edge request timed out", transient=True, correlation_id=correlation_id) from exc
        except httpx.HTTPError as exc:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_UNAVAILABLE", "mail edge request failed", transient=True, correlation_id=correlation_id) from exc
        if response.status_code >= 500:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_UNAVAILABLE", "mail edge server error", transient=True, correlation_id=correlation_id)
        if response.status_code == 404:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_NOT_FOUND", "mail edge resource was not found", correlation_id=correlation_id)
        if response.status_code >= 400:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_REJECTED", "mail edge rejected the request", correlation_id=correlation_id)
        if len(response.content) > _MAX_API_RESPONSE_BYTES:
            raise CloudflareProviderError(
                "CLOUDFLARE_MAIL_EDGE_INVALID_RESPONSE",
                "mail edge response exceeded the bounded limit",
                transient=True,
                correlation_id=correlation_id,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_INVALID_RESPONSE", "mail edge returned invalid JSON", transient=True, correlation_id=correlation_id) from exc
        if not isinstance(data, dict):
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_INVALID_RESPONSE", "mail edge returned an invalid response", transient=True, correlation_id=correlation_id)
        return data, correlation_id

    async def health_check(self) -> dict[str, Any]:
        data, correlation_id = await self._request("GET", "/v1/health")
        return {"healthy": data.get("status") == "ok", "provider": self.provider_name, "correlation_id": correlation_id}

    async def provision_identity(
        self,
        address: str,
        *,
        identity_id: str,
        worker_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = normalize_email_address(address)
        data, correlation_id = await self._request(
            "POST",
            "/v1/recipients/register",
            {
                "identity_id": str(identity_id),
                "worker_id": str(worker_id),
                "address": normalized,
                "state": "VERIFYING",
                "idempotency_key": idempotency_key,
            },
        )
        return {
            "provider": self.provider_name,
            "provider_reference": str(data.get("provider_reference") or f"recipient:{normalized}"),
            "correlation_id": correlation_id,
            "result": {"registered": True},
        }

    async def _lifecycle(
        self,
        action: str,
        provider_reference: str,
        *,
        identity_id: str,
        address: str,
    ) -> dict[str, Any]:
        data, correlation_id = await self._request(
            "POST",
            f"/v1/recipients/{action}",
            {
                "identity_id": str(identity_id),
                "address": normalize_email_address(address),
                "provider_reference": provider_reference,
            },
        )
        return {"provider": self.provider_name, "correlation_id": correlation_id, "result": {"state": data.get("state", action.upper())}}

    async def activate_identity(self, provider_reference: str, *, identity_id: str, address: str) -> dict[str, Any]:
        return await self._lifecycle("activate", provider_reference, identity_id=identity_id, address=address)

    async def suspend_identity(self, provider_reference: str, *, identity_id: str, address: str) -> dict[str, Any]:
        return await self._lifecycle("suspend", provider_reference, identity_id=identity_id, address=address)

    async def retire_identity(self, provider_reference: str, *, identity_id: str, address: str) -> dict[str, Any]:
        return await self._lifecycle("retire", provider_reference, identity_id=identity_id, address=address)

    async def add_alias(self, provider_reference: str, alias: str, *, identity_id: str | None = None) -> dict[str, Any]:
        data, correlation_id = await self._request(
            "POST",
            "/v1/recipients/alias",
            {"provider_reference": provider_reference, "identity_id": identity_id, "address": normalize_email_address(alias)},
        )
        return {"provider": self.provider_name, "correlation_id": correlation_id, "result": {"state": data.get("state", "ACTIVE")}}

    async def list_events(self, *, after: int, limit: int) -> dict[str, Any]:
        if after < 0 or limit < 1 or limit > 1000:
            raise ValueError("mail-edge cursor or limit is invalid")
        data, correlation_id = await self._request("GET", f"/v1/events?after={after}&limit={limit}")
        return {"provider": self.provider_name, "correlation_id": correlation_id, **data}

    async def fetch_message(self, message_id: str) -> dict[str, Any]:
        safe_id = quote(str(message_id), safe="")
        data, correlation_id = await self._request("GET", f"/v1/messages/{safe_id}")
        encoded = data.get("raw_mime_base64")
        if not isinstance(encoded, str):
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_INVALID_MESSAGE", "mail edge message body is unavailable", transient=True, correlation_id=correlation_id)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_INVALID_MESSAGE", "mail edge message body is invalid", transient=True, correlation_id=correlation_id) from exc
        if len(raw) > self.max_message_bytes:
            raise CloudflareProviderError("CLOUDFLARE_MAIL_EDGE_MESSAGE_TOO_LARGE", "mail edge message exceeds the configured limit", correlation_id=correlation_id)
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        view = normalize_raw_message(
            raw,
            message_id=str(data.get("message_id") or message_id),
            envelope_recipient=str(data.get("envelope_recipient") or metadata.get("envelope_recipient") or ""),
            received_at=data.get("received_at"),
        )
        return {
            "provider": self.provider_name,
            "correlation_id": correlation_id,
            "message_id": str(data.get("message_id") or message_id),
            "identity_id": data.get("identity_id"),
            "worker_id": data.get("worker_id"),
            "provider_event_id": data.get("provider_event_id"),
            "raw_mime": raw,
            "message": view,
        }

    async def acknowledge_event(self, event_id: str) -> dict[str, Any]:
        safe_id = quote(str(event_id), safe="")
        data, correlation_id = await self._request("POST", f"/v1/events/{safe_id}/ack")
        return {"provider": self.provider_name, "correlation_id": correlation_id, **data}

    async def verify_delivery(
        self,
        provider_reference: str,
        provider_message_id: str,
        *,
        identity_id: str | None = None,
    ) -> dict[str, Any]:
        message = await self.fetch_message(provider_message_id)
        if identity_id and str(message.get("identity_id")) != str(identity_id):
            raise ValueError("mail-edge delivery evidence belongs to another identity")
        # Cloudflare's provider reference is an opaque registry binding (not
        # the address itself). The identity correlation above is authoritative;
        # the envelope address is still available to the caller for evidence.
        del provider_reference
        return message

    async def delete_message(self, provider_reference: str, message_id: str) -> dict[str, Any]:
        safe_id = quote(str(message_id), safe="")
        data, correlation_id = await self._request(
            "POST", f"/v1/messages/{safe_id}/delete",
            {"provider_reference": str(provider_reference)},
        )
        return {"provider": self.provider_name, "correlation_id": correlation_id, **data}

    async def mark_processed(self, provider_reference: str, message_id: str) -> dict[str, Any]:
        safe_id = quote(str(message_id), safe="")
        data, correlation_id = await self._request(
            "POST", f"/v1/messages/{safe_id}/processed",
            {"provider_reference": str(provider_reference)},
        )
        return {"provider": self.provider_name, "correlation_id": correlation_id, **data}

    async def protect_message(self, provider_reference: str, message_id: str, *, until: str) -> dict[str, Any]:
        safe_id = quote(str(message_id), safe="")
        data, correlation_id = await self._request(
            "POST", f"/v1/messages/{safe_id}/protect",
            {"provider_reference": str(provider_reference), "until": until},
        )
        return {"provider": self.provider_name, "correlation_id": correlation_id, **data}

    async def read_message(self, provider_reference: str, message_id: str) -> dict[str, Any]:
        fetched = await self.fetch_message(message_id)
        return {"correlation_id": fetched["correlation_id"], "result": {"list": [fetched["message"]]}}


class CloudflareEdgeFixture:
    """No-network default D1-chunk emulator used by provider tests."""

    def __init__(
        self,
        *,
        auth_secret: str = "fixture-mail-edge-secret",
        max_message_bytes: int = _DEFAULT_MAX_MESSAGE_BYTES,
        storage_backend: str = "d1",
    ) -> None:
        if storage_backend not in {"d1", "r2"}:
            raise ValueError("storage_backend must be d1 or r2")
        self.auth_secret = auth_secret
        self.max_message_bytes = max_message_bytes
        self.storage_backend = storage_backend
        self.registry: dict[str, dict[str, Any]] = {}
        self.chunks: dict[str, list[bytes]] = {}
        self.d1: dict[str, dict[str, Any]] = {"recipients": {}, "messages": {}, "events": {}, "chunks": self.chunks}
        self.r2: dict[str, bytes] = {}
        self.events: list[dict[str, Any]] = []
        self._events_by_provider_key: dict[str, dict[str, Any]] = {}
        self._messages_by_id: dict[str, dict[str, Any]] = {}
        self._nonces: set[str] = set()
        self._sequence = 0

    @staticmethod
    def _response(status: int, body: Mapping[str, Any]) -> httpx.Response:
        return httpx.Response(status, json=dict(body))

    def _verify(self, request: httpx.Request) -> bool:
        headers = {str(key).lower(): str(value) for key, value in request.headers.items()}
        if len(self.auth_secret) < 20 or headers.get("x-aiat-mail-edge-version") != _AUTH_VERSION:
            return False
        nonce = headers.get("x-aiat-mail-edge-nonce", "")
        try:
            timestamp = int(headers.get("x-aiat-mail-edge-timestamp", ""))
        except ValueError:
            return False
        if not nonce or nonce in self._nonces or abs(time.time() - timestamp) > 300:
            return False
        signature = headers.get("x-aiat-mail-edge-signature", "")
        path = request.url.raw_path.decode() if isinstance(request.url.raw_path, bytes) else str(request.url.raw_path)
        body = bytes(request.content or b"")
        digest = hashlib.sha256(body).hexdigest()
        canonical = f"{_AUTH_VERSION}\n{request.method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()
        expected = base64.b64encode(hmac.new(self.auth_secret.encode(), canonical, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(signature, expected):
            return False
        self._nonces.add(nonce)
        return True

    @staticmethod
    def _safe_reference(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text or not _REFERENCE_RE.fullmatch(text):
            raise ValueError(f"{label} is invalid")
        return text

    def _register(self, body: Mapping[str, Any], state: str) -> dict[str, Any]:
        identity_id = self._safe_reference(body.get("identity_id"), "identity_id")
        worker_id = self._safe_reference(body.get("worker_id"), "worker_id")
        address = normalize_email_address(str(body.get("address") or ""))
        existing = self.registry.get(address)
        if existing is not None and str(existing["identity_id"]) != identity_id:
            raise ValueError("recipient is already owned by another identity")
        if existing is not None and str(existing["worker_id"]) != worker_id:
            raise ValueError("recipient is already owned by another worker")
        if existing is not None and str(existing.get("state")) in {"SUSPENDED", "RETIRED"}:
            raise ValueError("recipient lifecycle must be reconciled before registration")
        provider_reference = str(existing.get("provider_reference")) if existing else f"recipient:{hashlib.sha256(address.encode()).hexdigest()[:32]}"
        effective_state = str(existing.get("state")) if existing and str(existing.get("state")) == "ACTIVE" else state
        row = {
            "identity_id": identity_id,
            "worker_id": worker_id,
            "address": address,
            "state": effective_state,
            "provider_reference": provider_reference,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.registry[address] = row
        self.d1["recipients"][address] = dict(row)
        return {"provider_reference": provider_reference, "state": state}

    def receive_message(self, envelope_recipient: str, raw_mime: bytes, *, provider_message_id: str | None = None) -> dict[str, Any]:
        """Deliver a message using the SMTP envelope recipient."""

        try:
            recipient = normalize_email_address(envelope_recipient)
        except ValueError:
            return {"accepted": False, "reason": "invalid_recipient"}
        raw = bytes(raw_mime)
        if len(raw) > self.max_message_bytes:
            return {"accepted": False, "reason": "message_too_large"}
        identity = self.registry.get(recipient)
        if identity is None:
            return {"accepted": False, "reason": "unknown_recipient"}
        if str(identity.get("state")) not in {"ACTIVE", "VERIFYING"}:
            return {"accepted": False, "reason": "recipient_inactive"}
        header_id = _header_message_id(raw)
        source_id = _bounded(provider_message_id or header_id, max_length=300, fallback="")
        if source_id:
            provider_key = f"{recipient}\x00{source_id}"
            content_digest = hashlib.sha256(raw).hexdigest()
        else:
            content_digest = hashlib.sha256(raw).hexdigest()
            provider_key = f"{recipient}\x00raw:{content_digest}"
        existing_event = self._events_by_provider_key.get(provider_key)
        if existing_event is not None:
            if existing_event.get("content_digest") != content_digest:
                return {"accepted": False, "reason": "provider_event_conflict"}
            return {"accepted": True, "duplicate": True, "event_id": existing_event["event_id"], "message_id": existing_event["message_id"]}
        message_id = "m-" + hashlib.sha256(
            recipient.encode() + b"\x00" + source_id.encode() + b"\x00" + raw
        ).hexdigest()[:48]
        object_key = (
            f"d1://mail-message/{message_id}"
            if self.storage_backend == "d1"
            else f"inbound/{identity['identity_id']}/{message_id}.eml"
        )
        received_at = datetime.now(UTC).isoformat()
        view = normalize_raw_message(raw, message_id=message_id, envelope_recipient=recipient, received_at=received_at)
        self._sequence += 1
        event = {
            "sequence": self._sequence,
            "event_id": "evt-" + hashlib.sha256(provider_key.encode()).hexdigest()[:48],
            "event_type": "inbound.message.received",
            "message_id": message_id,
            "provider_message_id": source_id or None,
            "provider_event_id": "evt-" + hashlib.sha256(provider_key.encode()).hexdigest()[:48],
            "identity_id": identity["identity_id"],
            "worker_id": identity["worker_id"],
            "envelope_recipient": recipient,
            "raw_size": len(raw),
            "received_at": received_at,
            "object_key": object_key,
            "acked_at": None,
            "content_digest": content_digest,
        }
        metadata = {
            "message_id": message_id,
            "provider_event_id": event["provider_event_id"],
            "identity_id": identity["identity_id"],
            "worker_id": identity["worker_id"],
            "envelope_recipient": recipient,
            "sender": (view.get("from") or [{}])[0].get("email") if view.get("from") else None,
            "subject": view.get("subject", ""),
            "received_at": received_at,
            "raw_size": len(raw),
            "raw_object_key": object_key,
            "storage_backend": self.storage_backend,
            "storage_state": "EVENT_READY",
            "chunk_count": (len(raw) + _D1_RAW_CHUNK_SIZE - 1) // _D1_RAW_CHUNK_SIZE if self.storage_backend == "d1" and raw else 0,
            "processed_at": None,
            "protected_until": None,
            "deleted_at": None,
        }
        if self.storage_backend == "d1":
            self.chunks[message_id] = [
                raw[offset:offset + _D1_RAW_CHUNK_SIZE]
                for offset in range(0, len(raw), _D1_RAW_CHUNK_SIZE)
            ]
        else:
            self.r2[object_key] = raw
        self.d1["messages"][message_id] = dict(metadata)
        self.d1["events"][event["event_id"]] = {key: value for key, value in event.items() if key not in {"content_digest"}}
        self._messages_by_id[message_id] = {"metadata": metadata, "event": event, "message": view}
        self._events_by_provider_key[provider_key] = event
        self.events.append(event)
        return {"accepted": True, "duplicate": False, "event_id": event["event_id"], "message_id": message_id}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if not self._verify(request):
            return self._response(401, {"error": "invalid mail-edge authentication"})
        parsed = urlsplit(str(request.url))
        path = parsed.path
        try:
            body = json.loads(bytes(request.content or b"{}").decode() or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._response(400, {"error": "request body is invalid"})
        if not isinstance(body, dict):
            return self._response(400, {"error": "request body is invalid"})
        try:
            if request.method == "GET" and path == "/v1/health":
                return self._response(200, {"status": "ok", "provider": "cloudflare", "storage": {"metadata": "d1", "raw": self.storage_backend}, "storage_backend": self.storage_backend})
            if request.method == "POST" and path == "/v1/recipients/register":
                return self._response(200, self._register(body, "VERIFYING"))
            if request.method == "POST" and path in {"/v1/recipients/activate", "/v1/recipients/suspend", "/v1/recipients/retire"}:
                address = normalize_email_address(str(body.get("address") or ""))
                identity = self.registry.get(address)
                if identity is None or str(identity.get("identity_id")) != str(body.get("identity_id")) or str(identity.get("provider_reference")) != str(body.get("provider_reference")):
                    return self._response(404, {"error": "recipient not found"})
                state = {"/v1/recipients/activate": "ACTIVE", "/v1/recipients/suspend": "SUSPENDED", "/v1/recipients/retire": "RETIRED"}[path]
                identity["state"] = state
                identity["updated_at"] = datetime.now(UTC).isoformat()
                self.d1["recipients"][address] = dict(identity)
                return self._response(200, {"state": state, "provider_reference": identity["provider_reference"]})
            if request.method == "POST" and path == "/v1/recipients/alias":
                alias = normalize_email_address(str(body.get("address") or ""))
                identity_id = self._safe_reference(body.get("identity_id"), "identity_id") if body.get("identity_id") else None
                reference = str(body.get("provider_reference") or "")
                owner = next((item for item in self.registry.values() if item.get("provider_reference") == reference and (identity_id is None or item.get("identity_id") == identity_id)), None)
                if owner is None or str(owner.get("state")) not in {"VERIFYING", "ACTIVE"}:
                    return self._response(404, {"error": "recipient owner not found"})
                existing_alias = self.registry.get(alias)
                if existing_alias is not None and str(existing_alias.get("identity_id")) != str(owner.get("identity_id")):
                    return self._response(409, {"error": "recipient alias is already owned"})
                self.registry[alias] = {**owner, "address": alias}
                self.d1["recipients"][alias] = dict(self.registry[alias])
                return self._response(200, {"state": "ACTIVE"})
            if request.method == "GET" and path == "/v1/events":
                params = parse_qs(parsed.query)
                after = int((params.get("after") or ["0"])[0])
                limit = min(1000, max(1, int((params.get("limit") or ["100"])[0])))
                selected = [event for event in self.events if int(event["sequence"]) > after][:limit]
                safe_events = [{key: value for key, value in event.items() if key not in {"content_digest", "acked_at"}} for event in selected]
                return self._response(200, {"cursor": after, "next_cursor": int(selected[-1]["sequence"]) if selected else after, "events": safe_events})
            if request.method == "GET" and path.startswith("/v1/messages/"):
                message_id = path.removeprefix("/v1/messages/")
                message = self._messages_by_id.get(message_id)
                if message is None or message["metadata"].get("deleted_at") is not None:
                    return self._response(404, {"error": "message not found"})
                metadata = dict(message["metadata"])
                raw = b"".join(self.chunks.get(message_id, [])) if metadata.get("storage_backend") == "d1" else self.r2.get(metadata["raw_object_key"], b"")
                return self._response(200, {**metadata, "provider_event_id": message["event"]["provider_event_id"], "raw_mime_base64": base64.b64encode(raw).decode()})
            if request.method == "POST" and path.startswith("/v1/events/") and path.endswith("/ack"):
                event_id = path.removeprefix("/v1/events/").removesuffix("/ack").rstrip("/")
                event = self.d1["events"].get(event_id)
                if event is None:
                    return self._response(404, {"error": "event not found"})
                event["acked_at"] = event.get("acked_at") or datetime.now(UTC).isoformat()
                return self._response(200, {"event_id": event_id, "acknowledged": True})
            if request.method == "POST" and path.startswith("/v1/messages/") and path.endswith("/processed"):
                message_id = path.removeprefix("/v1/messages/").removesuffix("/processed").rstrip("/")
                message = self.d1["messages"].get(message_id)
                if message is None:
                    return self._response(404, {"error": "message not found"})
                owner = self.registry.get(str(message.get("envelope_recipient")))
                if owner is None or str(body.get("provider_reference")) != str(owner.get("provider_reference")):
                    return self._response(403, {"error": "message provider binding denied"})
                message["processed_at"] = datetime.now(UTC).isoformat()
                return self._response(200, {"message_id": message_id, "processed": True})
            if request.method == "POST" and path.startswith("/v1/messages/") and path.endswith("/delete"):
                message_id = path.removeprefix("/v1/messages/").removesuffix("/delete").rstrip("/")
                message = self.d1["messages"].get(message_id)
                if message is None:
                    return self._response(404, {"error": "message not found"})
                owner = self.registry.get(str(message.get("envelope_recipient")))
                if owner is None or str(body.get("provider_reference")) != str(owner.get("provider_reference")):
                    return self._response(403, {"error": "message provider binding denied"})
                message["deleted_at"] = datetime.now(UTC).isoformat()
                object_key = str(message.get("raw_object_key") or "")
                self.r2.pop(object_key, None)
                self.chunks.pop(message_id, None)
                message["storage_state"] = "DELETED"
                return self._response(200, {"message_id": message_id, "deleted": True})
            if request.method == "POST" and path.startswith("/v1/messages/") and path.endswith("/protect"):
                message_id = path.removeprefix("/v1/messages/").removesuffix("/protect").rstrip("/")
                message = self.d1["messages"].get(message_id)
                if message is None:
                    return self._response(404, {"error": "message not found"})
                owner = self.registry.get(str(message.get("envelope_recipient")))
                if owner is None or str(body.get("provider_reference")) != str(owner.get("provider_reference")):
                    return self._response(403, {"error": "message provider binding denied"})
                until = str(body.get("until") or "")
                protected_until = datetime.fromisoformat(until.replace("Z", "+00:00"))
                message["protected_until"] = protected_until.astimezone(UTC).isoformat()
                return self._response(200, {"message_id": message_id, "protected_until": message["protected_until"]})
        except (ValueError, TypeError, KeyError):
            return self._response(422, {"error": "mail-edge request is invalid"})
        return self._response(404, {"error": "mail-edge route not found"})

    def cleanup(
        self,
        *,
        now: datetime | None = None,
        retention_days: int = 7,
        processed_retention_days: int = 1,
    ) -> int:
        """Delete expired raw bodies/chunks while retaining bounded metadata."""

        current = (now or datetime.now(UTC)).astimezone(UTC)
        raw_cutoff = current.timestamp() - retention_days * 24 * 60 * 60
        processed_cutoff = current.timestamp() - processed_retention_days * 24 * 60 * 60
        deleted = 0
        for message_id, metadata in list(self.d1["messages"].items()):
            if metadata.get("deleted_at") is not None:
                continue
            protected = metadata.get("protected_until")
            if protected:
                try:
                    if datetime.fromisoformat(str(protected).replace("Z", "+00:00")).timestamp() > current.timestamp():
                        continue
                except ValueError:
                    continue
            received = datetime.fromisoformat(str(metadata["received_at"]).replace("Z", "+00:00")).timestamp()
            processed = metadata.get("processed_at")
            processed_time = datetime.fromisoformat(str(processed).replace("Z", "+00:00")).timestamp() if processed else None
            if (processed_time is None and received <= raw_cutoff) or (processed_time is not None and processed_time <= processed_cutoff):
                metadata["deleted_at"] = current.isoformat()
                self.r2.pop(str(metadata.get("raw_object_key") or ""), None)
                self.chunks.pop(message_id, None)
                metadata["storage_state"] = "DELETED"
                deleted += 1
                if message_id in self._messages_by_id:
                    self._messages_by_id[message_id]["metadata"]["deleted_at"] = current.isoformat()
        return deleted


__all__ = [
    "CloudflareEdgeFixture",
    "CloudflareInboundAdapter",
    "CloudflareProviderError",
    "normalize_email_address",
    "normalize_raw_message",
]
