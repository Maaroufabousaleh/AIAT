"""Deterministic tests for the disposable OpenHands gateway helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import yaml


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
CONFIG = Path(__file__).resolve().parents[2] / "infra" / "compose" / "litellm_openhands_certification.yaml"


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
    assert report["provider_count"] == 1
    assert report["resolved_provider_model"] == "groq/llama-3.3-70b-versatile"
    assert report["management_endpoint"] == "http://omniroute:20128"
    assert report["openai_compatible_endpoint"] == "http://omniroute:20129/v1"
    assert provider_key not in serialized
    assert management_key not in serialized
    client.close()


def test_route_probe_retains_only_scalar_usage() -> None:
    gateway_key = "gateway-secret-must-not-appear"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/monitoring/health":
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


def test_route_probe_distinguishes_internal_gateway_auth_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/monitoring/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/health/readiness":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            return httpx.Response(401, json={"error": "redacted"})
        raise AssertionError(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(PROBE.GatewayProbeError) as error:
        PROBE.probe(
            litellm_url="http://litellm.test",
            omniroute_url="http://omniroute.test",
            gateway_key="gateway-secret",
            client=client,
        )
    client.close()
    assert error.value.stage == "gateway_auth"
    assert error.value.http_status == 401


def test_route_probe_preserves_omniroute_health_failure_stage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/monitoring/health":
            return httpx.Response(503, json={"status": "redacted"})
        raise AssertionError(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(PROBE.GatewayProbeError) as error:
        PROBE.probe(
            litellm_url="http://litellm.test",
            omniroute_url="http://omniroute.test",
            gateway_key="gateway-secret",
            client=client,
        )
    client.close()
    assert error.value.stage == "omniroute_health"
    assert error.value.http_status == 503


def test_route_probe_preserves_litellm_health_failure_stage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/monitoring/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/health/readiness":
            return httpx.Response(503, json={"status": "redacted"})
        raise AssertionError(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(PROBE.GatewayProbeError) as error:
        PROBE.probe(
            litellm_url="http://litellm.test",
            omniroute_url="http://omniroute.test",
            gateway_key="gateway-secret",
            client=client,
        )
    client.close()
    assert error.value.stage == "litellm_health"
    assert error.value.http_status == 503


def test_route_probe_rejects_missing_internal_endpoint_before_network() -> None:
    with pytest.raises(PROBE.GatewayProbeError) as error:
        PROBE.probe(litellm_url="", omniroute_url="http://omniroute.test", gateway_key="gateway-secret")
    assert error.value.stage == "gateway_response"


def test_pin_verification_requires_the_exact_repo_digest() -> None:
    def runner(command, **kwargs):
        image = command[-1]
        expected = PINS.LITELLM if "litellm" in image else PINS.OMNIROUTE
        return type("Result", (), {
            "stdout": json.dumps([
                {
                    "RepoDigests": [expected["image"]],
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {"Labels": {"org.opencontainers.image.revision": expected["source_commit"]}},
                }
            ])
        })()

    report = PINS.verify(runner=runner)
    assert report["status"] == "PASS"
    assert report["floating_tags_used"] is False
    assert all(item["repo_digest_verified"] for item in report["components"])
    assert {item["source_archive_sha256"] for item in report["components"]} == {
        "3e6474f2d7f507b124158291e327f995886756573d90dc641c04d73afea45ede",
        "e81fc85f47204ffe09cd283a56cfce92f109a6f13de7d3bef3f4057f7f43d2e6",
    }


def test_disposable_litellm_config_has_one_governed_omniroute_route() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    models = config.get("model_list")
    assert isinstance(models, list)
    assert len(models) == 1
    route = models[0]
    assert route["model_name"] == "omniroute-coding"
    params = route["litellm_params"]
    assert params == {
        "model": "openai/groq/llama-3.3-70b-versatile",
        "api_base": "http://omniroute:20129/v1",
        "api_key": "os.environ/OMNIROUTE_API_KEY",
    }
    assert config["general_settings"] == {"master_key": "os.environ/LITELLM_MASTER_KEY"}
    serialized = CONFIG.read_text(encoding="utf-8").lower()
    assert "localhost" not in serialized
    assert "host.docker.internal" not in serialized
    assert "latest" not in serialized


def test_disposable_route_cannot_add_fallback_provider_or_model() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    models = config["model_list"]
    assert [item["model_name"] for item in models] == ["omniroute-coding"]
    assert all(item["litellm_params"]["api_base"] == "http://omniroute:20129/v1" for item in models)


def test_omniroute_control_transport_failure_is_not_classified_as_provider_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("disposable control endpoint unavailable", request=request)

    client = httpx.Client(base_url="http://omniroute.test", transport=httpx.MockTransport(handler))
    with pytest.raises(PROVISION.GatewayProvisioningError) as error:
        PROVISION.provision(
            base_url="http://omniroute.test",
            management_key="gateway-secret",
            provider_key="provider-secret",
            client=client,
        )
    client.close()
    assert error.value.stage == "omniroute_health"
    assert error.value.exception_type == "ConnectError"
