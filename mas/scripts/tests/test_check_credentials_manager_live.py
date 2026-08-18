"""Tests for the secret-safe credentials-manager release checker."""

from __future__ import annotations

import os

from check_credentials_manager_live import (
    FIXTURE_VALUE,
    _metadata_is_redacted,
    _normalize_dsn,
    _run_fixture,
)


def test_normalize_dsn_accepts_sync_postgres_scheme() -> None:
    assert _normalize_dsn("postgresql://user:password@db:5432/mas") == (
        "postgresql+asyncpg://user:password@db:5432/mas"
    )


def test_normalize_dsn_rejects_unexpanded_templates() -> None:
    assert _normalize_dsn("postgresql://user:${PASSWORD}@db/mas") is None
    assert _normalize_dsn("") is None


def test_fixture_report_is_scalar_and_secret_free() -> None:
    report = _run_fixture()
    serialized = str(report)
    assert report["status"] == "pass"
    assert report["payload_free"] is True
    assert report["secret_free"] is True
    assert FIXTURE_VALUE not in serialized


def test_metadata_redaction_requires_placeholder() -> None:
    class Metadata:
        name = "NAME"
        placeholder = "<NAME>"

        @staticmethod
        def to_dict() -> dict[str, str]:
            return {"name": "NAME", "placeholder": "<NAME>"}

    assert _metadata_is_redacted(Metadata()) is True


def test_metadata_redaction_rejects_ciphertext_field() -> None:
    class Metadata:
        name = "NAME"
        placeholder = "<NAME>"

        @staticmethod
        def to_dict() -> dict[str, str]:
            return {"name": "NAME", "placeholder": "<NAME>", "encrypted_value": "x"}

    assert _metadata_is_redacted(Metadata()) is False


def test_live_mode_requires_explicit_configuration(monkeypatch) -> None:
    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    assert os.getenv("CREDENTIALS_ENCRYPTION_KEY") is None
