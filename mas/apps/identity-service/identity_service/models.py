"""Public, secret-free identity-service API models and redaction helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IdentityState(StrEnum):
    HIRED_PENDING_IDENTITY = "HIRED_PENDING_IDENTITY"
    TEMPORARY_MAILBOX_APPROVAL_PENDING = "TEMPORARY_MAILBOX_APPROVAL_PENDING"
    IDENTITY_PROVISIONING = "IDENTITY_PROVISIONING"
    IDENTITY_VERIFYING = "IDENTITY_VERIFYING"
    IDENTITY_ACTIVE = "IDENTITY_ACTIVE"
    IDENTITY_PROVISIONING_FAILED = "IDENTITY_PROVISIONING_FAILED"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ExternalAccountState(StrEnum):
    REQUESTED = "REQUESTED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class UsageHoldState(StrEnum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"


SECRET_KEY_RE = re.compile(
    r"(?:password|secret|token|api[_-]?key|credential|cookie|refresh|totp|recovery|authorization)",
    re.IGNORECASE,
)


def redact(value: Any) -> Any:
    """Remove secret-bearing fields recursively before logs/events/responses."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestActor(ApiModel):
    """Actor delegated by a signed control-plane service.

    `actor_id` is accepted only inside a request whose client signature is
    verified.  The identity service still compares it with the target worker
    for worker-scoped operations.
    """

    actor_id: str = Field(min_length=1, max_length=200)
    project_id: UUID | None = None
    worker_run_id: UUID | None = None
    purpose: str = Field(min_length=3, max_length=500)


class DomainCreateRequest(ApiModel):
    domain: str
    actor: RequestActor

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if "." not in value or "@" in value or any(c.isspace() for c in value):
            raise ValueError("domain must be a DNS name")
        return value


class ProvisionIdentityRequest(ApiModel):
    company_id: UUID
    worker_id: UUID
    actor: RequestActor
    idempotency_key: str = Field(min_length=12, max_length=300)
    friendly_alias: str | None = Field(default=None, max_length=128)
    mailbox_class: str = Field(default="permanent", pattern="^(permanent|temporary)$")
    required: bool = True


class IdentityVerificationRequest(ApiModel):
    actor: RequestActor
    provider_message_id: str = Field(min_length=1, max_length=300)


class WorkerIdentityActionRequest(ApiModel):
    actor: RequestActor


class MailQueryRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    limit: int = Field(default=25, ge=1, le=100)
    query: str | None = Field(default=None, max_length=1000)
    message_id: str | None = Field(default=None, max_length=300)


class VerificationWaitRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    sender_domain: str | None = Field(default=None, max_length=253)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class OutboundRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    idempotency_key: str = Field(min_length=12, max_length=300)
    recipients: list[str] = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=250_000)
    recipient_class: str = Field(min_length=1, max_length=64)
    content_type: str = "text/plain"

    @field_validator("recipients")
    @classmethod
    def validate_recipients(cls, values: list[str]) -> list[str]:
        if len(set(item.lower() for item in values)) != len(values):
            raise ValueError("duplicate recipients are not permitted")
        if any("@" not in item or len(item) > 320 for item in values):
            raise ValueError("recipient must be a valid-sized email address")
        return values


class SendApprovedRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    outbound_request_id: UUID
    idempotency_key: str = Field(min_length=12, max_length=300)


class OutboundStatusRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    outbound_request_id: UUID


class ApprovalDecisionRequest(ApiModel):
    actor: RequestActor
    approved: bool
    reason: str = Field(default="", max_length=2000)


class ExternalAccountRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    service: str = Field(min_length=2, max_length=200)
    service_category: str = Field(min_length=2, max_length=100)
    idempotency_key: str = Field(min_length=12, max_length=300)
    email_identity_id: UUID | None = None


class ExternalAccountStatusRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor


class BrowserSessionRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    service: str = Field(min_length=2, max_length=200)
    external_account_id: UUID
    idempotency_key: str = Field(min_length=12, max_length=300)


class BrowserSessionUseRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    session_id: UUID
    lease_token: str | None = Field(default=None, min_length=32, max_length=200)


class BrowserSessionLeaseRequest(ApiModel):
    worker_id: UUID
    actor: RequestActor
    session_id: UUID


class SyncRequest(ApiModel):
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class SyncAckRequest(ApiModel):
    cursor: int = Field(ge=0)


class IdentityView(ApiModel):
    id: UUID
    company_id: UUID
    worker_id: UUID
    address: str
    alias: str | None = None
    state: IdentityState
    quota_mb: int
    outbound_enabled: bool = False
    provider_account_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AuditView(ApiModel):
    id: UUID = Field(default_factory=uuid4)
    actor_id: str
    action: str
    target_type: str
    target_id: str
    outcome: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
