"""Offline tests for the disposable OmniRoute API authentication boundary."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx


def _load():
    path = Path(__file__).resolve().parents[1] / "check_openhands_omniroute_auth.py"
    spec = importlib.util.spec_from_file_location("check_openhands_omniroute_auth", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_auth_contract_requires_missing_and_wrong_keys_to_be_denied():
    module = _load()
    report = module.evaluate_statuses(
        unauthenticated_status=401,
        wrong_key_status=403,
        correct_key_status=200,
        endpoint="http://127.0.0.1:20129/v1/models",
    )
    assert report["status"] == "PASS"
    assert report["unauthenticated_provider_route_denied"] is True
    assert report["wrong_gateway_key_denied"] is True
    assert report["correct_gateway_key_accepted"] is True
    assert report["credentials_retained"] is False


def test_auth_contract_failure_is_scalar_and_secret_free():
    module = _load()
    report = module.evaluate_statuses(
        unauthenticated_status=200,
        wrong_key_status=200,
        correct_key_status=401,
        endpoint="http://127.0.0.1:20129/v1/models",
    )
    serialized = json.dumps(report, sort_keys=True)
    assert report["status"] == "BLOCKED"
    assert "Bearer" not in serialized
    assert report["raw_response_retained"] is False


def test_live_helper_sends_only_bearer_auth_and_keeps_responses_out():
    module = _load()
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization")))
        auth = request.headers.get("authorization")
        if auth is None:
            return httpx.Response(401)
        if auth == "Bearer wrong":
            return httpx.Response(403)
        return httpx.Response(200, json={"data": [{"id": "redacted"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = module.check(
        url="http://omniroute.test/v1/models",
        gateway_key="correct",
        client=client,
    )
    client.close()
    assert report["status"] == "BLOCKED"
    assert len(seen) == 3
    assert seen[0][1] is None
    assert seen[1][1] == "Bearer aiat-openhands-invalid-gateway-key"
    assert seen[2][1] == "Bearer correct"
    assert "redacted" not in json.dumps(report)


def test_live_helper_retries_cold_api_bridge_before_evaluating_auth():
    module = _load()
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        calls.append(auth)
        if len(calls) == 1:
            raise httpx.ConnectError("API bridge still starting", request=request)
        if auth is None or auth == "Bearer aiat-openhands-invalid-gateway-key":
            return httpx.Response(401)
        return httpx.Response(200, json={"data": [{"id": "redacted"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = module.check(
        url="http://omniroute.test/v1/models",
        gateway_key="correct",
        client=client,
        attempts=3,
        interval_seconds=0,
        sleep=lambda _seconds: None,
    )
    client.close()
    assert report["status"] == "PASS"
    assert report["probe_attempts"]["unauthenticated"] == 2
    assert report["probe_attempts"]["wrong_key"] == 1
    assert report["probe_attempts"]["correct_key"] == 1
    assert report["transport_retry_window_seconds"] == 0
    assert "API bridge" not in json.dumps(report)


def test_live_helper_keeps_auth_blocked_when_transport_never_becomes_ready():
    module = _load()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("still starting", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = module.check(
        url="http://omniroute.test/v1/models",
        gateway_key="correct",
        client=client,
        attempts=2,
        interval_seconds=0,
        sleep=lambda _seconds: None,
    )
    client.close()
    assert report["status"] == "BLOCKED"
    assert report["failure_class"] == "MODEL_GATEWAY_TRANSPORT_FAILURE"
    assert report["probe_attempts"] == {
        "unauthenticated": 2,
        "wrong_key": 2,
        "correct_key": 2,
    }
    assert report["unauthenticated_http_status"] is None
    assert report["raw_response_retained"] is False
