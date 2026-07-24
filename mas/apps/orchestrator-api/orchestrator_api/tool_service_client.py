"""Signed orchestrator transport for tool-service dispatch and grant changes."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_VERSION = "aiat.tool.v1"


@dataclass(frozen=True)
class ToolServiceClientConfig:
    url: str
    secret: str
    client_id: str
    private_key_b64: str

    @classmethod
    def from_environment(cls) -> ToolServiceClientConfig:
        return cls(
            url=os.getenv("TOOL_SERVICE_URL", "http://tool-service:8002").rstrip("/"),
            secret=os.getenv("TOOL_SECRET", ""),
            client_id=os.getenv("AIAT_TOOL_CLIENT_ID", "orchestrator-api"),
            private_key_b64=os.getenv("AIAT_TOOL_CLIENT_PRIVATE_KEY", ""),
        )


class SignedToolServiceClient:
    def __init__(self, config: ToolServiceClientConfig, *, client: httpx.AsyncClient | None = None) -> None:
        if not config.secret:
            raise RuntimeError("TOOL_SECRET must be configured for tool-service operations")
        self.config = config
        self._client = client
        self._key: Ed25519PrivateKey | None = None
        if config.private_key_b64:
            try:
                raw = base64.b64decode(config.private_key_b64, validate=True)
                if len(raw) != 32:
                    raise ValueError
                self._key = Ed25519PrivateKey.from_private_bytes(raw)
            except Exception as exc:
                raise ValueError("AIAT_TOOL_CLIENT_PRIVATE_KEY must be a 32-byte base64 Ed25519 key") from exc
        if os.getenv("MAS_ENVIRONMENT", "development").lower() in {"production", "prod", "staging"} and self._key is None:
            raise RuntimeError("AIAT_TOOL_CLIENT_PRIVATE_KEY is required for production tool-service operations")

    def _headers(self, method: str, path: str, raw_body: bytes) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.config.secret}", "Content-Type": "application/json"}
        if self._key is None:
            return headers
        timestamp = int(time.time())
        nonce = str(uuid4())
        canonical = "\n".join((
            _VERSION, method.upper(), path, str(timestamp), nonce,
            hashlib.sha256(raw_body).hexdigest(),
        )).encode()
        headers.update({
            "X-AIAT-Signature-Version": _VERSION,
            "X-AIAT-Client-ID": self.config.client_id,
            "X-AIAT-Timestamp": str(timestamp),
            "X-AIAT-Nonce": nonce,
            "X-AIAT-Signature": base64.b64encode(self._key.sign(canonical)).decode(),
        })
        return headers

    async def request(self, method: str, path: str, body: dict[str, Any], *, timeout: float = 120) -> dict[str, Any]:
        raw_body = json.dumps(body, separators=(",", ":"), sort_keys=True, default=str).encode()
        if self._client is not None:
            response = await self._client.request(method, f"{self.config.url}{path}", content=raw_body, headers=self._headers(method, path, raw_body), timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, f"{self.config.url}{path}", content=raw_body, headers=self._headers(method, path, raw_body))
        if response.status_code >= 400:
            raise RuntimeError(f"tool service rejected operation ({response.status_code})")
        return response.json()
