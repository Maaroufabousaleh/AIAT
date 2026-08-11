"""Deterministic transport tests for the secret-safe ``mas-ctl`` client."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mas_ctl  # noqa: E402


class _Response:
    def __init__(self, status: int, payload: object):
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


def test_status_uses_operator_key_and_normalizes_base_url(monkeypatch):
    seen: dict[str, object] = {}

    def fake_urlopen(request, *, timeout):
        seen.update(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "api_key": request.headers.get("X-api-key"),
                "timeout": timeout,
            }
        )
        return _Response(200, {"state": "RUNNING"})

    monkeypatch.setattr(mas_ctl, "urlopen", fake_urlopen)

    exit_code, record = mas_ctl.run(
        "status",
        base_url="http://orchestrator/",
        api_key="operator-secret",
        timeout=2.5,
    )

    assert exit_code == 0
    assert record["payload"] == {"state": "RUNNING"}
    assert seen == {
        "url": "http://orchestrator/system/status",
        "method": "GET",
        "api_key": "operator-secret",
        "timeout": 2.5,
    }


def test_bootstrap_requires_healthy_diagnostics(monkeypatch):
    responses = {
        "/health": _Response(200, {"status": "ok"}),
        "/system/diagnostics": _Response(
            200,
            {"status": "degraded", "dependencies": {"database": {"status": "error"}}},
        ),
    }

    monkeypatch.setattr(
        mas_ctl,
        "urlopen",
        lambda request, **_kwargs: responses[request.full_url.removeprefix("http://api")],
    )

    exit_code, record = mas_ctl.run("bootstrap", base_url="http://api")

    assert exit_code == 1
    assert record["status"] == "degraded"
    assert record["diagnostics"]["payload"]["status"] == "degraded"


def test_bootstrap_ready_requires_both_health_surfaces(monkeypatch):
    responses = {
        "/health": _Response(200, {"status": "ok"}),
        "/system/diagnostics": _Response(200, {"status": "ok"}),
    }
    monkeypatch.setattr(
        mas_ctl,
        "urlopen",
        lambda request, **_kwargs: responses[request.full_url.removeprefix("http://api")],
    )

    exit_code, record = mas_ctl.run("bootstrap", base_url="http://api")

    assert exit_code == 0
    assert record["status"] == "ready"


def test_http_error_does_not_return_upstream_body(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        raise HTTPError(
            "http://api/system/status",
            500,
            "upstream failure",
            {},
            io.BytesIO(b"password=do-not-return"),
        )

    monkeypatch.setattr(mas_ctl, "urlopen", fake_urlopen)

    exit_code, record = mas_ctl.run("status", base_url="http://api")

    assert exit_code == 1
    assert record == {
        "command": "status",
        "http_status": 500,
        "error_type": "HTTPError",
    }
    assert "do-not-return" not in json.dumps(record)


@pytest.mark.parametrize("command", ["resume", "shutdown"])
def test_explicit_state_commands_use_post(monkeypatch, command):
    seen: list[tuple[str, str]] = []

    def fake_urlopen(request, **_kwargs):
        seen.append((request.full_url, request.get_method()))
        return _Response(202, {"status": "accepted"})

    monkeypatch.setattr(mas_ctl, "urlopen", fake_urlopen)

    exit_code, record = mas_ctl.run(command, base_url="http://api")

    assert exit_code == 0
    assert record["payload"] == {"status": "accepted"}
    assert seen == [(f"http://api/system/{command}", "POST")]
