"""Opaque, short-lived lease tokens; raw credential material is never handled."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any


def issue_opaque_lease(*, session_id: str, scope: str, ttl_seconds: int = 300) -> dict[str, Any]:
    """Issue an opaque handle for a local broker; only its hash is persistable."""
    if ttl_seconds < 30 or ttl_seconds > 900:
        raise ValueError("credential lease TTL must be between 30 and 900 seconds")
    token = secrets.token_urlsafe(32)
    return {
        "lease_token": token,
        "lease_hash": hashlib.sha256(token.encode()).hexdigest(),
        "session_id": session_id,
        "scope": scope,
        "expires_at": datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    }
