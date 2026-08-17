"""Bounded mail-edge and provider-event observations.

The identity service owns mail state and provider credentials.  This module is
the shared, payload-free read-model contract used when that service projects a
delivery attempt or a verified provider webhook into AIAT observability.  It
does not send mail, select a provider, or turn provenance/licence metadata
into an execution predicate.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from mas_core.observability.tracing import is_safe_span_id, is_safe_trace_id

MAIL_EDGE_OBSERVATION_SCHEMA = "aiat.mail-edge-observation.v1"
MAIL_EDGE_COVERAGE_SCHEMA = "aiat.mail-edge-coverage.v1"

MailEdgeSource = Literal["delivery_attempt", "provider_webhook", "provider_poll"]
MailEdgeEventType = Literal[
    "queued",
    "sent",
    "delivered",
    "deferred",
    "bounced",
    "complained",
    "failed",
    "unknown",
]
MailEdgeOutcome = Literal["success", "failure", "unknown"]
MailEdgeFailureClass = Literal["transient", "permanent"]

_SAFE_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_SAFE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:body|content|cookie|credential|header|html|password|payload|query|recipient|"
    r"secret|subject|token|authorization|api[_-]?key)",
    re.IGNORECASE,
)
_SAFE_METADATA_KEY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_EVENT_ALIASES = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.delivery_delayed": "deferred",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.failed": "failed",
    "delivered": "delivered",
    "delivery": "delivered",
    "delivery_delayed": "deferred",
    "deferred": "deferred",
    "bounce": "bounced",
    "bounced": "bounced",
    "complaint": "complained",
    "complained": "complained",
    "failed": "failed",
    "queued": "queued",
    "accepted": "queued",
    "sent": "sent",
}
_SUCCESS_EVENTS = frozenset({"queued", "sent", "delivered"})
_TRANSIENT_EVENTS = frozenset({"deferred"})
_PERMANENT_EVENTS = frozenset({"bounced", "complained", "failed"})
_SAFE_METADATA_KEYS = frozenset(
    {
        "attempt_number",
        "provider_event_type",
        "provider_reason_code",
        "provider_status",
        "retry_after_seconds",
        "webhook_version",
    }
)


class MailEdgeObservation(BaseModel):
    """One secret-free provider or identity-service mail observation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = MAIL_EDGE_OBSERVATION_SCHEMA
    id: UUID = Field(default_factory=uuid4)
    provider: str = Field(min_length=1, max_length=64)
    source: MailEdgeSource
    event_id: str = Field(min_length=1, max_length=200)
    event_type: MailEdgeEventType
    outcome: MailEdgeOutcome
    failure_class: MailEdgeFailureClass | None = None
    worker_id: str | None = Field(default=None, max_length=200)
    outbound_request_id: str | None = Field(default=None, max_length=200)
    provider_message_ref: str | None = Field(default=None, max_length=200)
    trace_id: str | None = Field(default=None, max_length=128)
    span_id: str | None = Field(default=None, max_length=128)
    occurred_at: datetime
    signature_verified: bool = False
    metadata: dict[str, bool | float | int | str] = Field(default_factory=dict)


def _text(value: Any, *, label: str, max_length: int = 200) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length or not _SAFE_REFERENCE_RE.fullmatch(text):
        raise ValueError(f"{label} must be a bounded safe reference")
    return text


def _timestamp(value: Any) -> datetime:
    candidate: datetime | None
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, str) and value.strip():
        try:
            candidate = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("mail-edge occurred_at must be an ISO timestamp") from exc
    else:
        candidate = None
    if candidate is None:
        return datetime.now(UTC)
    return candidate.astimezone(UTC) if candidate.tzinfo else candidate.replace(tzinfo=UTC)


def normalize_event_type(value: Any) -> MailEdgeEventType:
    """Map common provider event names into the bounded AIAT enum."""

    key = str(value or "").strip().lower().replace("/", ".")
    key = key.replace(" ", "_")
    return _EVENT_ALIASES.get(key, "unknown")  # type: ignore[return-value]


def _metadata(values: Mapping[str, Any] | None) -> dict[str, bool | float | int | str]:
    """Keep only explicitly safe scalar provider metadata."""

    result: dict[str, bool | float | int | str] = {}
    for key, value in (values or {}).items():
        normalized_key = str(key).strip().lower()
        if normalized_key not in _SAFE_METADATA_KEYS:
            continue
        if not _SAFE_METADATA_KEY_RE.fullmatch(normalized_key):
            continue
        if _SENSITIVE_KEY_RE.search(normalized_key):
            continue
        if isinstance(value, bool):
            result[normalized_key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            result[normalized_key] = max(-1_000_000, min(value, 1_000_000))
        elif isinstance(value, float):
            if value == value and abs(value) <= 1_000_000:
                result[normalized_key] = value
        elif isinstance(value, str) and len(value) <= 200:
            result[normalized_key] = value
    return result


def _outcome_and_failure(event_type: MailEdgeEventType) -> tuple[MailEdgeOutcome, MailEdgeFailureClass | None]:
    if event_type in _SUCCESS_EVENTS:
        return "success", None
    if event_type in _TRANSIENT_EVENTS:
        return "failure", "transient"
    if event_type in _PERMANENT_EVENTS:
        return "failure", "permanent"
    return "unknown", None


def build_mail_edge_observation(
    *,
    provider: str,
    source: MailEdgeSource,
    event_id: str,
    event_type: Any,
    occurred_at: Any = None,
    worker_id: str | None = None,
    outbound_request_id: str | None = None,
    provider_message_ref: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    signature_verified: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> MailEdgeObservation:
    """Build one normalized observation from already-authorized provider data."""

    normalized_provider = str(provider or "").strip().lower()
    if not _SAFE_PROVIDER_RE.fullmatch(normalized_provider):
        raise ValueError("mail-edge provider must be a bounded lowercase identifier")
    normalized_event_id = _text(event_id, label="mail-edge event_id")
    if normalized_event_id is None:
        raise ValueError("mail-edge event_id is required")
    normalized_type = normalize_event_type(event_type)
    outcome, failure_class = _outcome_and_failure(normalized_type)
    safe_trace = str(trace_id or "").strip() or None
    if safe_trace and not is_safe_trace_id(safe_trace):
        safe_trace = None
    safe_span = str(span_id or "").strip() or None
    if safe_span and not is_safe_span_id(safe_span):
        safe_span = None
    return MailEdgeObservation(
        provider=normalized_provider,
        source=source,
        event_id=normalized_event_id,
        event_type=normalized_type,
        outcome=outcome,
        failure_class=failure_class,
        worker_id=_text(worker_id, label="mail-edge worker_id"),
        outbound_request_id=_text(outbound_request_id, label="mail-edge outbound_request_id"),
        provider_message_ref=_text(provider_message_ref, label="mail-edge provider_message_ref"),
        trace_id=safe_trace,
        span_id=safe_span,
        occurred_at=_timestamp(occurred_at),
        signature_verified=bool(signature_verified),
        metadata=_metadata(metadata),
    )


def normalize_provider_webhook(
    provider: str,
    payload: Mapping[str, Any],
    *,
    event_id: str | None = None,
    signature_verified: bool = False,
    worker_id: str | None = None,
    outbound_request_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> MailEdgeObservation:
    """Normalize a provider webhook without retaining its payload.

    Provider adapters may pass the event body directly or under ``data``.
    Only opaque IDs, an event enum, a timestamp, and an allow-listed scalar
    metadata subset survive this boundary.
    """

    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    assert isinstance(data, Mapping)
    raw_event_type = payload.get("type") or payload.get("event_type") or data.get("type")
    raw_event_id = event_id or payload.get("id") or payload.get("event_id") or data.get("id")
    message_ref = (
        data.get("email_id")
        or data.get("emailId")
        or data.get("message_id")
        or data.get("messageId")
        or data.get("id")
    )
    metadata = {
        "provider_event_type": raw_event_type,
        "provider_status": data.get("status") or data.get("delivery_status"),
        "provider_reason_code": data.get("reason_code") or data.get("reasonCode") or data.get("code"),
        "webhook_version": payload.get("version") or payload.get("api_version"),
        "attempt_number": data.get("attempt") or data.get("attempt_number"),
        "retry_after_seconds": data.get("retry_after_seconds"),
    }
    return build_mail_edge_observation(
        provider=provider,
        source="provider_webhook",
        event_id=str(raw_event_id or ""),
        event_type=raw_event_type,
        occurred_at=payload.get("created_at") or payload.get("occurred_at") or data.get("created_at"),
        worker_id=worker_id,
        outbound_request_id=outbound_request_id,
        provider_message_ref=str(message_ref) if message_ref is not None else None,
        trace_id=trace_id,
        span_id=span_id,
        signature_verified=signature_verified,
        metadata=metadata,
    )


def evaluate_mail_edge_coverage(
    observations: Iterable[MailEdgeObservation | Mapping[str, Any]],
    *,
    trace_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate provider/webhook/bounce coverage without selecting a worker."""

    normalized: list[MailEdgeObservation] = []
    conflicts: list[str] = []
    seen: dict[str, MailEdgeObservation] = {}
    for value in observations:
        try:
            item = value if isinstance(value, MailEdgeObservation) else MailEdgeObservation.model_validate(value)
        except Exception:
            conflicts.append("invalid_observation")
            continue
        previous = seen.get(item.event_id)
        if previous is not None:
            if previous.model_dump(mode="json") != item.model_dump(mode="json"):
                conflicts.append(item.event_id)
            continue
        seen[item.event_id] = item
        normalized.append(item)

    requested_trace = str(trace_id or "").strip() or None
    requested_worker = str(worker_id or "").strip() or None
    webhook = [item for item in normalized if item.source == "provider_webhook"]
    signed_webhook = [item for item in webhook if item.signature_verified]
    bounce = [item for item in normalized if item.event_type in {"bounced", "complained", "failed"}]
    correlated = [
        item
        for item in normalized
        if (requested_trace is None or item.trace_id == requested_trace)
        and (requested_worker is None or item.worker_id == requested_worker)
    ]
    missing: list[str] = []
    if not signed_webhook:
        missing.append("verified_provider_webhook")
    if not bounce:
        missing.append("bounce_or_failure_event")
    if requested_trace and not any(item.trace_id == requested_trace for item in normalized):
        missing.append("trace_correlation")
    if requested_worker and not any(item.worker_id == requested_worker for item in normalized):
        missing.append("worker_correlation")
    if conflicts:
        missing.append("conflicting_event_id")
    status = "pass" if not missing else "attention"
    return {
        "schema_version": MAIL_EDGE_COVERAGE_SCHEMA,
        "status": status,
        "licence_metadata_is_gate": False,
        "observation_count": len(normalized),
        "source_counts": {
            source: sum(item.source == source for item in normalized)
            for source in ("delivery_attempt", "provider_webhook", "provider_poll")
        },
        "event_counts": {
            event_type: sum(item.event_type == event_type for item in normalized)
            for event_type in ("queued", "sent", "delivered", "deferred", "bounced", "complained", "failed", "unknown")
        },
        "providers": sorted({item.provider for item in normalized}),
        "signed_webhook_count": len(signed_webhook),
        "bounce_or_failure_count": len(bounce),
        "correlated_count": len(correlated),
        "missing": missing,
        "conflict_event_ids": sorted(set(conflicts)),
        "scope": "payload-free identity/provider mail-edge observations; live provider and worker execution remain separate",
    }


__all__ = [
    "MAIL_EDGE_COVERAGE_SCHEMA",
    "MAIL_EDGE_OBSERVATION_SCHEMA",
    "MailEdgeObservation",
    "build_mail_edge_observation",
    "evaluate_mail_edge_coverage",
    "normalize_event_type",
    "normalize_provider_webhook",
]
