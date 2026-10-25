"""Run-scoped signed grants for the OpenHands AIAT MCP bridge.

This is intentionally a separate contract from the OpenCode bridge.  The
grant is a short-lived capability token issued by AIAT and carried only in an
internal MCP request header.  It contains no credentials and cannot be used
against the normal tool HTTP API or the OpenCode bridge.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Iterable


_MAX_TTL_SECONDS = 15 * 60
_PURPOSE = "aiat.openhands.mcp.v1"


class OpenHandsToolGrantError(ValueError):
    """A grant is malformed, expired, or was not signed by AIAT."""


@dataclass(frozen=True, slots=True)
class OpenHandsToolGrant:
    """The capability bound to one AIAT worker run."""

    worker_id: str
    run_id: UUID
    project_id: UUID | None
    tool_names: frozenset[str]
    claim_owner: str | None
    attempt_count: int
    expires_at: int
    grant_id: str


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_openhands_tool_grant(
    signing_secret: str,
    *,
    worker_id: str,
    run_id: UUID,
    project_id: UUID | None,
    tool_names: Iterable[str],
    claim_owner: str | None = None,
    attempt_count: int = 0,
    ttl_seconds: int = 300,
    now: int | None = None,
) -> str:
    """Issue a bounded OpenHands-only run capability."""

    if not signing_secret:
        raise OpenHandsToolGrantError("OpenHands tool bridge signing secret is not configured")
    if not worker_id.strip():
        raise OpenHandsToolGrantError("OpenHands tool bridge requires a worker id")
    if not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
        raise OpenHandsToolGrantError("OpenHands tool bridge TTL is out of bounds")
    normalized_claim_owner = str(claim_owner or "").strip() or None
    if (
        isinstance(attempt_count, bool)
        or not isinstance(attempt_count, int)
        or attempt_count < 0
        or (normalized_claim_owner is None) != (attempt_count == 0)
    ):
        raise OpenHandsToolGrantError("OpenHands tool bridge claim fence is invalid")
    issued_at = int(time.time()) if now is None else int(now)
    payload = {
        "purpose": _PURPOSE,
        "worker_id": worker_id.strip(),
        "run_id": str(run_id),
        "project_id": str(project_id) if project_id else None,
        "tool_names": sorted({str(name).strip() for name in tool_names if str(name).strip()}),
        "claim_owner": normalized_claim_owner,
        "attempt_count": attempt_count,
        "exp": issued_at + ttl_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    encoded = _b64_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_openhands_tool_grant(
    token: str,
    signing_secret: str,
    *,
    now: int | None = None,
) -> OpenHandsToolGrant:
    """Verify and decode a bearer grant without logging its opaque value."""

    if not signing_secret or not token or token.count(".") != 1:
        raise OpenHandsToolGrantError("invalid OpenHands tool bridge grant")
    encoded, signature = token.split(".", 1)
    expected = hmac.new(signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise OpenHandsToolGrantError("invalid OpenHands tool bridge grant")
    try:
        payload = json.loads(_b64_decode(encoded))
        worker_id = str(payload["worker_id"]).strip()
        run_id = UUID(str(payload["run_id"]))
        project_id = UUID(str(payload["project_id"])) if payload.get("project_id") else None
        raw_tool_names = payload["tool_names"]
        if not isinstance(raw_tool_names, list) or any(
            not isinstance(name, str) for name in raw_tool_names
        ):
            raise OpenHandsToolGrantError("invalid OpenHands tool bridge grant")
        tool_names = frozenset(name.strip() for name in raw_tool_names if name.strip())
        raw_claim_owner = payload.get("claim_owner")
        if raw_claim_owner is not None and not isinstance(raw_claim_owner, str):
            raise OpenHandsToolGrantError("invalid OpenHands tool bridge grant")
        claim_owner = str(raw_claim_owner or "").strip() or None
        raw_attempt_count = payload.get("attempt_count", 0)
        if (
            isinstance(raw_attempt_count, bool)
            or not isinstance(raw_attempt_count, int)
            or raw_attempt_count < 0
            or (claim_owner is None) != (raw_attempt_count == 0)
        ):
            raise OpenHandsToolGrantError("invalid OpenHands tool bridge grant")
        attempt_count = raw_attempt_count
        expires_at = int(payload["exp"])
        grant_id = str(payload["jti"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenHandsToolGrantError("invalid OpenHands tool bridge grant") from exc
    if payload.get("purpose") != _PURPOSE or not worker_id or not grant_id:
        raise OpenHandsToolGrantError("invalid OpenHands tool bridge grant")
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current or expires_at - current > _MAX_TTL_SECONDS:
        raise OpenHandsToolGrantError("expired OpenHands tool bridge grant")
    return OpenHandsToolGrant(
        worker_id=worker_id,
        run_id=run_id,
        project_id=project_id,
        tool_names=tool_names,
        claim_owner=claim_owner,
        attempt_count=attempt_count,
        expires_at=expires_at,
        grant_id=grant_id,
    )
