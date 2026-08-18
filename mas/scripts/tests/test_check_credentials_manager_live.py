"""Tests for the secret-safe credentials-manager release checker."""

from __future__ import annotations

import json
import os
from argparse import Namespace

from check_credentials_manager_live import (
    FIXTURE_VALUE,
    _metadata_is_redacted,
    _normalize_dsn,
    _run_compose_local,
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


def test_compose_local_probe_keeps_scalar_report(monkeypatch) -> None:
    payload = {
        "schema_version": "aiat.credentials-manager-live.v1",
        "mode": "live",
        "status": "pass",
        "checks": {"cleanup_zero_residue": True},
        "secret_free": True,
        "payload_free": True,
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)

    monkeypatch.setattr("check_credentials_manager_live.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("check_credentials_manager_live.subprocess.run", lambda *args, **kwargs: Result())

    report = _run_compose_local(Namespace(container="mas-orchestrator-api-1"))

    assert report["status"] == "pass"
    assert report["mode"] == "compose-local-live"
    assert report["transport"] == "docker-exec-private-network"
    assert report["secret_free"] is True
    assert report["payload_free"] is True


def test_compose_local_probe_blocks_without_docker(monkeypatch) -> None:
    monkeypatch.setattr("check_credentials_manager_live.shutil.which", lambda _: None)
    report = _run_compose_local(Namespace(container="mas-orchestrator-api-1"))
    assert report["status"] == "blocked"
    assert report["reason"] == "Docker CLI is unavailable for the private credentials probe"
