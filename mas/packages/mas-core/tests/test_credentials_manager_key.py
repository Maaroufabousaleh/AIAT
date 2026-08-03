"""Credential-key compatibility and production fail-closed tests."""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet

from mas_core.credentials.manager import _configured_fernet, _get_fernet


def test_configured_base64_material_is_normalized_without_exposing_it() -> None:
    configured = base64.b64encode(b"high-entropy-material" * 3).decode()
    fernet = _configured_fernet(configured)

    token = fernet.encrypt(b"managed value")
    assert fernet.decrypt(token) == b"managed value"


def test_missing_production_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="required"):
        _get_fernet()


def test_canonical_fernet_key_remains_supported() -> None:
    configured = Fernet.generate_key().decode()
    fernet = _configured_fernet(configured)

    assert fernet.decrypt(fernet.encrypt(b"value")) == b"value"
