"""Deterministic tests for the disposable OpenHands gateway helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx


def _load(name: str):
    script = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROVISION = _load("provision_openhands_certification_gateway")
PROBE = _load("check_openhands_certification_gateway")
PINS = _load("verify_openhands_gateway_pins")


def test_provider_route_is_single_exact_and_never_retains_credentials() -> None:
    provider_key = "provider-secret-must-not-appear"
    management_key = "gateway-secret-must-not-appear"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/providers":
            return httpx.Response(200, json={"connections": []})
        if request.method == "POST" and request.url.path == "/api/providers":
            payload = json.loads(request.content)
            assert payload == {
                "provider": "groq",
                "apiKey": provider_key,
                "name": PROVISION.PROVIDER_NAME,
                "defaultModel": PROVISION.PROVIDER_MODEL,
                "priority": 1,
            }
            return httpx.Response(
                201,
                json={
                    "connection": {
                        "id": "connection-1",
                        "provider": "groq",
                        "name": PROVISION.PROVIDER_NAME,
                        "defaultModel": PROVISION.PROVIDER_MODEL,
                    }
                },
            )
        if request.method == "POST" and request.url.path == "/api/providers/connection-1/test":
            return httpx.Response(200, json={"valid": True, "diagnosis": {}})
        if request.method == "GET" and request.url.path == "/api/providers":
            return httpx.Response(200, json={"connections": []})
        raise AssertionError(request)

    # The handler needs a stateful readback after creation.
    calls = 0

    def stateful(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "GET" and request.url.path == "/api/providers":
            calls += 1
            connections = [] if calls == 1 else [
                {
                    "id": "connection-1",
                    "provider": "groq",
                    "name": PROVISION.PROVIDER_NAME,
                    "defaultModel": PROVISION.PROVIDER_MODEL,
                    "apiKey": "••••••",
                }
            ]
            return httpx.Response(200, json={"connections": connections})
        return handler(request)

    client = httpx.Client(base_url="http://omniroute.test", transport=httpx.MockTransport(stateful))
    report = PROVISION.provision(
        base_url="http://omniroute.test",
        management_key=management_key,
        provider_key=provider_key,
        client=client,
    )
    serialized = json.dumps(report, sort_keys=True)
    assert report["status"] == "PASS"
    assert report["resolved_provider_model"] == "groq/llama-3.3-70b-versatile"
    assert provider_key not in serialized
    assert management_key not in serialized
    client.close()


def test_route_probe_retains_only_scalar_usage() -> None:
    gateway_key = "gateway-secret-must-not-appear"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/health/ping":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/health/readiness":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            assert request.headers["authorization"] == f"Bearer {gateway_key}"
            return httpx.Response(
                200,
                headers={"x-omniroute-selected-connection-id": "connection-1"},
                json={
                    "choices": [{"message": {"content": "secret raw response"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                },
            )
        raise AssertionError(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = PROBE.probe(
        litellm_url="http://litellm.test",
        omniroute_url="http://omniroute.test",
        gateway_key=gateway_key,
        client=client,
    )
    serialized = json.dumps(report, sort_keys=True)
    assert report["status"] == "PASS"
    assert report["route"]["usage"]["total_tokens"] == 5
    assert report["route"]["raw_response_retained"] is False
    assert gateway_key not in serialized
    assert "secret raw response" not in serialized
    client.close()


def test_pin_verification_requires_the_exact_repo_digest() -> None:
    def runner(command, **kwargs):
        image = command[-1]
        expected = PINS.LITELLM if "litellm" in image else PINS.OMNIROUTE
        return type("Result", (), {
            "stdout": json.dumps([
                {
                    "RepoDigests": [expected["image"]],
                    "Config": {"Labels": {"org.opencontainers.image.revision": expected["source_commit"]}},
                }
            ])
        })()

    report = PINS.verify(runner=runner)
    assert report["status"] == "PASS"
    assert report["floating_tags_used"] is False
    assert all(item["repo_digest_verified"] for item in report["components"])
