"""Fail-closed transport checks for the signed identity gateway."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tool_service.identity_client import IdentityGatewayClient


def test_production_identity_gateway_rejects_plaintext_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    monkeypatch.setenv("IDENTITY_SERVICE_URL", "http://identity.aiat.ca")
    monkeypatch.setenv(
        "AIAT_IDENTITY_TOOL_PRIVATE_KEY",
        base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode(),
    )
    with pytest.raises(RuntimeError, match="HTTPS"):
        IdentityGatewayClient()


def test_identity_gateway_rejects_url_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAS_ENVIRONMENT", "development")
    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://user:password@identity.aiat.ca")
    monkeypatch.setenv(
        "AIAT_IDENTITY_TOOL_PRIVATE_KEY",
        base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode(),
    )
    with pytest.raises(RuntimeError, match="without credentials"):
        IdentityGatewayClient()
