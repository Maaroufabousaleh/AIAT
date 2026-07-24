"""Signed gateway client for the remote identity-service.

Only the tool-service calls this client.  It submits an already-authenticated
worker context to the identity service; it cannot resolve or export any raw
mail, relay, external-account, or browser credentials.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class IdentityGatewayClient:
    def __init__(self) -> None:
        self.url = os.getenv("IDENTITY_SERVICE_URL", "").rstrip("/")
        self.client_id = os.getenv("AIAT_IDENTITY_TOOL_CLIENT_ID", "tool-service")
        encoded = os.getenv("AIAT_IDENTITY_TOOL_PRIVATE_KEY", "")
        if not self.url or not encoded:
            raise RuntimeError("identity service client is not configured")
        parsed_url = urlsplit(self.url)
        if (
            not parsed_url.hostname
            or parsed_url.scheme not in {"http", "https"}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise RuntimeError("IDENTITY_SERVICE_URL must be an origin without credentials, query, or fragment")
        environment = os.getenv("MAS_ENVIRONMENT", "development").strip().lower()
        if environment in {"production", "prod", "staging"} and parsed_url.scheme != "https":
            raise RuntimeError("IDENTITY_SERVICE_URL must use HTTPS in production")
        try:
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) != 32:
                raise ValueError
            self.key = Ed25519PrivateKey.from_private_bytes(raw)
        except Exception as exc:
            raise RuntimeError("AIAT_IDENTITY_TOOL_PRIVATE_KEY is invalid") from exc

    async def post(self, path: str, body: dict) -> dict:
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
        timestamp = int(time.time())
        nonce = str(uuid4())
        payload = f"aiat.identity.v1\nPOST\n{path}\n{timestamp}\n{nonce}\n{hashlib.sha256(raw).hexdigest()}".encode()
        headers = {
            "Content-Type": "application/json",
            "X-AIAT-Signature-Version": "aiat.identity.v1",
            "X-AIAT-Client-ID": self.client_id,
            "X-AIAT-Timestamp": str(timestamp),
            "X-AIAT-Nonce": nonce,
            "X-AIAT-Signature": base64.b64encode(self.key.sign(payload)).decode(),
        }
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.post(f"{self.url}{path}", content=raw, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError("identity service unavailable") from exc
        if response.status_code >= 400:
            raise PermissionError(f"identity operation rejected ({response.status_code})")
        return response.json()

    async def use_browser_session(
        self, *, worker_id: str, actor: dict, session_id: str
    ) -> dict:
        """Perform the broker-only one-use lease handshake internally.

        The lease token exists only between two signed service calls and is
        never returned from a worker-visible tool response.
        """
        lease = await self.post(
            "/v1/sessions/lease",
            {"worker_id": worker_id, "actor": actor, "session_id": session_id},
        )
        token = lease.get("lease_token")
        if not isinstance(token, str) or not token:
            raise PermissionError("identity service did not issue a browser lease")
        return await self.post(
            "/v1/sessions/use",
            {
                "worker_id": worker_id,
                "actor": actor,
                "session_id": session_id,
                "lease_token": token,
            },
        )
