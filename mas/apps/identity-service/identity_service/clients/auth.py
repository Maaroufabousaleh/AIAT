"""Signed request authentication for laptop/control-plane clients.

The signature covers the method, path, body digest, timestamp and nonce.  A
client id alone is never authority; it selects a registered Ed25519 public key.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIGNATURE_VERSION = "aiat.identity.v1"


class ReplayStore(Protocol):
    async def consume_client_nonce(self, client_id: str, nonce: str, expires_at: int) -> bool: ...


@dataclass(frozen=True)
class SignedClient:
    client_id: str
    private_key: Ed25519PrivateKey

    @classmethod
    def from_base64(cls, client_id: str, encoded_private_key: str) -> SignedClient:
        raw = base64.b64decode(encoded_private_key, validate=True)
        if len(raw) != 32:
            raise ValueError("AIAT identity client private key must be a 32-byte Ed25519 raw key")
        return cls(client_id=client_id, private_key=Ed25519PrivateKey.from_private_bytes(raw))

    def public_key_base64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return base64.b64encode(raw).decode("ascii")

    def sign_headers(self, method: str, path: str, body: bytes, *, now: int | None = None) -> dict[str, str]:
        issued_at = now if now is not None else int(time.time())
        nonce = str(uuid4())
        payload = canonical_payload(method, path, body, issued_at, nonce)
        signature = self.private_key.sign(payload)
        return {
            "X-AIAT-Signature-Version": SIGNATURE_VERSION,
            "X-AIAT-Client-ID": self.client_id,
            "X-AIAT-Timestamp": str(issued_at),
            "X-AIAT-Nonce": nonce,
            "X-AIAT-Signature": base64.b64encode(signature).decode("ascii"),
        }


def canonical_payload(method: str, path: str, body: bytes, issued_at: int, nonce: str) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{SIGNATURE_VERSION}\n{method.upper()}\n{path}\n{issued_at}\n{nonce}\n{digest}".encode()


def decode_public_key(encoded: str) -> Ed25519PublicKey:
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) != 32:
        raise ValueError("registered identity client public key is invalid")
    return Ed25519PublicKey.from_public_bytes(raw)


async def verify_request(
    *,
    client_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    method: str,
    path: str,
    body: bytes,
    public_keys: dict[str, str],
    replay_store: ReplayStore,
    max_clock_skew_seconds: int = 300,
) -> None:
    """Verify signature and consume the nonce before dispatching an operation."""
    if client_id not in public_keys:
        raise PermissionError("unregistered identity client")
    try:
        issued_at = int(timestamp)
        signature_bytes = base64.b64decode(signature, validate=True)
    except (TypeError, ValueError) as exc:
        raise PermissionError("malformed signed identity request") from exc
    now = int(time.time())
    if abs(now - issued_at) > max_clock_skew_seconds:
        raise PermissionError("expired signed identity request")
    if not nonce or len(nonce) > 200:
        raise PermissionError("invalid signed identity request nonce")
    try:
        decode_public_key(public_keys[client_id]).verify(
            signature_bytes, canonical_payload(method, path, body, issued_at, nonce)
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("invalid identity request signature") from exc
    # Consuming after verification avoids allowing an attacker to burn arbitrary
    # nonces.  A replay is rejected even if the first request later fails.
    # Keep a verified nonce until the *later* of the signer/server clocks plus
    # the full acceptance window. A request signed near the past-skew boundary
    # must not become replayable immediately after its first acceptance.
    nonce_expires_at = max(now, issued_at) + max_clock_skew_seconds
    if not await replay_store.consume_client_nonce(client_id, nonce, nonce_expires_at):
        raise PermissionError("replayed signed identity request")
