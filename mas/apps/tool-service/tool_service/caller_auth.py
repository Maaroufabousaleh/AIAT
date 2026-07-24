"""Ed25519 verification for the identity attached to a tool request."""

from __future__ import annotations

import base64
import hashlib
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException, Request

_VERSION = "aiat.tool.v1"
_MAX_SKEW_SECONDS = 300


async def verify_signed_caller(
    request: Request, public_keys: dict[str, str], replay_store=None  # noqa: ANN001
) -> str:
    """Verify a short-lived signed request and reject nonce replays."""
    if request.headers.get("X-AIAT-Signature-Version") != _VERSION:
        raise HTTPException(401, "Tool request signature version is required")
    client_id = request.headers.get("X-AIAT-Client-ID", "")
    encoded_key = public_keys.get(client_id)
    if not encoded_key:
        raise HTTPException(403, "Unknown signed tool caller")
    try:
        timestamp = int(request.headers.get("X-AIAT-Timestamp", ""))
        nonce = request.headers["X-AIAT-Nonce"]
        signature = base64.b64decode(request.headers["X-AIAT-Signature"], validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key, validate=True))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "Malformed signed tool request") from exc
    if abs(time.time() - timestamp) > _MAX_SKEW_SECONDS:
        raise HTTPException(401, "Expired signed tool request")
    body = request.scope.get("aiat.tool.raw_body")
    if body is None:
        body = await request.body()
    canonical = "\n".join((
        _VERSION, request.method.upper(), request.url.path, str(timestamp), nonce,
        hashlib.sha256(body).hexdigest(),
    )).encode()
    try:
        key.verify(signature, canonical)
    except Exception as exc:
        raise HTTPException(403, "Invalid signed tool request") from exc
    if not nonce or len(nonce) > 200:
        raise HTTPException(401, "Invalid signed tool request nonce")
    now_epoch = int(time.time())
    if replay_store is not None:
        expires_at = max(now_epoch, timestamp) + _MAX_SKEW_SECONDS
        if not await replay_store.consume_signature_nonce(
            client_id, nonce, expires_at
        ):
            raise HTTPException(409, "Replayed signed tool request")
        return client_id

    now = time.monotonic()
    nonces: dict[str, float] = getattr(request.app.state, "tool_signature_nonces", {})
    for stale, expires_at in list(nonces.items()):
        if expires_at <= now:
            nonces.pop(stale, None)
    nonce_key = f"{client_id}:{nonce}"
    if nonce_key in nonces:
        raise HTTPException(409, "Replayed signed tool request")
    nonces[nonce_key] = now + _MAX_SKEW_SECONDS
    request.app.state.tool_signature_nonces = nonces
    return client_id
