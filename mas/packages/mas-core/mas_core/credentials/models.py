"""Pydantic models for the Credentials Manager."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SecretType(StrEnum):
    """Classification of the secret."""

    API_KEY = "api_key"
    TOKEN = "token"
    PASSWORD = "password"
    CERTIFICATE = "certificate"
    CONNECTION_STRING = "connection_string"
    OTHER = "other"


class SecretPolicy(BaseModel):
    """Per-secret access control policy."""

    # Which requester IDs/roles are allowed to resolve this secret
    allowed_requesters: list[str] = Field(default_factory=list)
    # Contexts in which the secret may be used (e.g. "llm-gateway", "tool-service")
    allowed_contexts: list[str] = Field(default_factory=list)
    # Maximum resolves per minute (0 = unlimited)
    rate_limit_per_minute: int = 0
    # Require step-up approval from human operator for each resolution
    require_approval: bool = False
    # Set to False to effectively disable the secret
    enabled: bool = True
    # Expiry timestamp (None = never expires)
    expires_at: datetime | None = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires = (
            self.expires_at.replace(tzinfo=UTC)
            if self.expires_at.tzinfo is None
            else self.expires_at.astimezone(UTC)
        )
        return datetime.now(UTC) > expires

    def allows(self, requester: str, context: str) -> tuple[bool, str]:
        """Check whether a requester/context pair is allowed."""
        if not self.enabled:
            return False, "secret_disabled"
        if self.is_expired():
            return False, "secret_expired"
        if self.allowed_requesters and requester not in self.allowed_requesters:
            return False, f"requester '{requester}' not in allowlist"
        if self.allowed_contexts and context not in self.allowed_contexts:
            return False, f"context '{context}' not in allowlist"
        return True, "ok"


class SecretMetadata(BaseModel):
    """Public metadata for a stored secret (never includes the real value)."""

    id: UUID
    name: str
    description: str = ""
    secret_type: SecretType = SecretType.OTHER
    policy: SecretPolicy = Field(default_factory=SecretPolicy)
    usage_count: int = 0
    last_used_at: datetime | None = None
    created_by: str = "system"
    created_at: datetime
    updated_at: datetime

    # Placeholder reference string shown to agents/LLMs
    @property
    def placeholder(self) -> str:
        return f"<{self.name}>"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "secret_type": self.secret_type,
            "policy": self.policy.model_dump(),
            "usage_count": self.usage_count,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "placeholder": self.placeholder,
        }
