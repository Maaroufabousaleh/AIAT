"""Run-scoped, signed grants for the OpenCode MCP tool bridge.

The grant is deliberately short lived and is sent only as an HTTP header from
the OpenCode runtime to the internal tool service.  It is never an AIAT API
credential, is not accepted by the regular tool HTTP API, and carries no
secret material in its payload.
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
_PURPOSE = "aiat.opencode.mcp.v1"


class OpenCodeToolGrantError(ValueError):
    """A grant is malformed, expired, or was not signed by AIAT."""


@dataclass(frozen=True, slots=True)
class OpenCodeToolGrant:
    """The capability bound to one governed worker run."""

    worker_id: str
    run_id: UUID
    project_id: UUID | None
    tool_names: frozenset[str]
    expires_at: int
    grant_id: str


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_opencode_tool_grant(
    signing_secret: str,
    *,
    worker_id: str,
    run_id: UUID,
    project_id: UUID | None,
    tool_names: Iterable[str],
    ttl_seconds: int = 300,
    now: int | None = None,
) -> str:
    """Return a short-lived HMAC grant for the immutable MCP bridge.

    A zero-tool grant is valid and fail-closed: its holder can connect to the
    MCP server but cannot execute any tool.
    """
    if not signing_secret:
        raise OpenCodeToolGrantError("OpenCode tool bridge signing secret is not configured")
    if not worker_id.strip():
        raise OpenCodeToolGrantError("OpenCode tool bridge requires a worker id")
    if not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
        raise OpenCodeToolGrantError("OpenCode tool bridge TTL is out of bounds")
    issued_at = int(time.time()) if now is None else int(now)
    payload = {
        "purpose": _PURPOSE,
        "worker_id": worker_id.strip(),
        "run_id": str(run_id),
        "project_id": str(project_id) if project_id else None,
        "tool_names": sorted({str(name).strip() for name in tool_names if str(name).strip()}),
        "exp": issued_at + ttl_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    encoded = _b64_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_opencode_tool_grant(
    token: str,
    signing_secret: str,
    *,
    now: int | None = None,
) -> OpenCodeToolGrant:
    """Verify and decode a grant without logging its opaque bearer value."""
    if not signing_secret or not token or token.count(".") != 1:
        raise OpenCodeToolGrantError("invalid OpenCode tool bridge grant")
    encoded, signature = token.split(".", 1)
    expected = hmac.new(signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise OpenCodeToolGrantError("invalid OpenCode tool bridge grant")
    try:
        payload = json.loads(_b64_decode(encoded))
        worker_id = str(payload["worker_id"]).strip()
        run_id = UUID(str(payload["run_id"]))
        project_id = UUID(str(payload["project_id"])) if payload.get("project_id") else None
        tool_names = frozenset(str(name).strip() for name in payload["tool_names"] if str(name).strip())
        expires_at = int(payload["exp"])
        grant_id = str(payload["jti"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenCodeToolGrantError("invalid OpenCode tool bridge grant") from exc
    if payload.get("purpose") != _PURPOSE or not worker_id or not grant_id:
        raise OpenCodeToolGrantError("invalid OpenCode tool bridge grant")
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current or expires_at - current > _MAX_TTL_SECONDS:
        raise OpenCodeToolGrantError("expired OpenCode tool bridge grant")
    return OpenCodeToolGrant(
        worker_id=worker_id,
        run_id=run_id,
        project_id=project_id,
        tool_names=tool_names,
        expires_at=expires_at,
        grant_id=grant_id,
    )
