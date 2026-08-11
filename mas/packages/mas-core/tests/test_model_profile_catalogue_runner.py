"""Tests for the fail-closed model-profile catalogue runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_model_profile_catalogue.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("model_profile_catalogue_runner", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_live_catalogue_normalizes_status_and_partial_coverage(monkeypatch) -> None:
    runner = _load_runner()
    payload = {
        "schema_version": "aiat.model-profile-catalogue.v1",
        "registry_model_count": 3,
        "profile_count": 4,
        "profile_version_count": 4,
        "covered_profile_version_count": 3,
        "profile_pending_model_count": 1,
        "findings": [{"code": "PROFILE_MODEL_NOT_REGISTERED"}],
        "entries": [],
    }
    monkeypatch.setattr(runner.httpx, "get", lambda *args, **kwargs: _Response(payload))

    report = runner._live_report(url="http://127.0.0.1:8000", api_key="secret", timeout=1)

    assert report["mode"] == "live"
    assert report["status"] == "pass_with_profile_findings"
    assert report["profile_coverage"] == "pending_persisted_profile_bindings"
    assert "secret" not in str(report)


def test_require_approved_blocks_when_no_approved_entry_exists() -> None:
    runner = _load_runner()

    report = runner._apply_approval_requirement(
        {
            "schema_version": "aiat.model-profile-catalogue.v1",
            "status": "pass",
            "entries": [{"profile_state": "profile_pending"}],
        },
        require_approved=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "no approved persisted model-profile coverage"


def test_live_catalogue_blocks_malformed_count_fields(monkeypatch) -> None:
    runner = _load_runner()
    payload = {
        "schema_version": "aiat.model-profile-catalogue.v1",
        "registry_model_count": "three",
        "profile_count": 0,
        "profile_version_count": 0,
        "covered_profile_version_count": 0,
        "profile_pending_model_count": 0,
        "findings": [],
        "entries": [],
    }
    monkeypatch.setattr(runner.httpx, "get", lambda *args, **kwargs: _Response(payload))

    report = runner._live_report(url="http://127.0.0.1:8000", api_key="secret", timeout=1)

    assert report["status"] == "blocked"
    assert report["reason"] == "orchestrator returned malformed model-profile catalogue fields"
