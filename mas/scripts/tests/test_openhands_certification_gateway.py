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
BASELINE = _load("check_openhands_provider_baseline")
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
        if request.method == "GET" and request.url.path == "/api/providers/connection-1/models":
            assert request.url.params.get("refresh") == "true"
            return httpx.Response(
                200,
                json={
                    "provider": "groq",
                    "connectionId": "connection-1",
                    "source": "api",
                    "models": [{"id": PROVISION.PROVIDER_MODEL}],
                },
            )
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
    assert report["resolved_provider_model"] == f"groq/{PROVISION.PROVIDER_MODEL}"
    assert report["baseline_discovery"]["status"] == "PASS"
    assert report["management_endpoint"] == "http://omniroute:20128"
    assert report["openai_compatible_endpoint"] == "http://omniroute:20129/v1"
    assert report["provider_pool"]["providers"] == ["groq"]
    assert report["provider_pool"]["arbitrary_environment_enumeration"] is False
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
            payload = json.loads(request.content)
            assert payload["model"] == PROBE.AIAT_MODEL
            assert payload["max_completion_tokens"] == 64
            assert payload["reasoning_effort"] == "low"
            assert payload["include_reasoning"] is False
            assert "max_tokens" not in payload
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
    assert report["route"]["provider_attribution"] == {
        "provider": PROBE.PROVIDER,
        "baseline_model": PROBE.PROVIDER_MODEL,
        "basis": "single_governed_certification_connection",
    }
    assert report["route"]["raw_response_retained"] is False
    assert gateway_key not in serialized
    assert "secret raw response" not in serialized
    client.close()


def test_route_probe_can_write_explicit_auto_routing_evidence(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "route-probe.json"
    auto_output = tmp_path / "auto-routing.json"
    monkeypatch.setenv("OPENHANDS_MODEL_GATEWAY_API_KEY", "gateway-secret")

    # Exercise the CLI writer without contacting a live gateway by replacing
    # the probe function with a scalar-only fixture report.
    original = PROBE.probe
    monkeypatch.setattr(
        PROBE,
        "probe",
        lambda **kwargs: {
            "schema_version": PROBE.SCHEMA,
            "status": "PASS",
            "route": {"resolved_route_model": "auto/coding", "raw_response_retained": False},
            "gateway_key_retained": False,
        },
    )
    assert PROBE.main(
        [
            "--litellm-url",
            "http://litellm.test",
            "--omniroute-url",
            "http://omniroute.test",
            "--output",
            str(output),
            "--auto-routing-output",
            str(auto_output),
        ]
    ) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(auto_output.read_text(encoding="utf-8"))
    monkeypatch.setattr(PROBE, "probe", original)


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


def test_provider_baseline_uses_exact_provider_qualified_model() -> None:
    gateway_key = "gateway-secret-must-not-appear"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {gateway_key}"
        payload = json.loads(request.content)
        assert payload["model"] == f"groq/{BASELINE.CERTIFICATION_BASELINE_MODEL}"
        assert payload["max_completion_tokens"] == BASELINE.BASELINE_MAX_COMPLETION_TOKENS
        assert payload["reasoning_effort"] == BASELINE.BASELINE_REASONING_EFFORT
        assert payload["include_reasoning"] is False
        assert "max_tokens" not in payload
        return httpx.Response(
            200,
            headers={"x-omniroute-selected-connection-id": "connection-1"},
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = BASELINE.probe(
        url="http://omniroute.test",
        gateway_key=gateway_key,
        client=client,
    )
    assert report["status"] == "PASS"
    assert report["provider_model"] == BASELINE.CERTIFICATION_BASELINE_MODEL
    assert report["usage"]["total_tokens"] == 3
    assert report["attempt_count"] == 1
    assert report["attempts"] == [{"attempt": 1, "http_status": 200, "retryable": False, "status": "PASS"}]
    assert gateway_key not in json.dumps(report, sort_keys=True)
    client.close()


def test_provider_baseline_retries_transient_server_error_once() -> None:
    responses = iter(
        [
            httpx.Response(502, json={"error": {"code": "upstream_error", "type": "server_error"}}),
            httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
        ]
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return next(responses)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = BASELINE.probe(
        url="http://omniroute.test",
        gateway_key="gateway-secret",
        client=client,
        max_attempts=2,
        retry_delay_seconds=0,
        sleep=lambda _: None,
    )
    client.close()
    assert calls == 2
    assert report["status"] == "PASS"
    assert report["attempt_count"] == 2
    assert report["attempts"][0] == {
        "attempt": 1,
        "failure": "PROVIDER_SERVER_ERROR",
        "http_status": 502,
        "provider_error_code": "upstream_error",
        "provider_error_type": "server_error",
        "retryable": True,
        "status": "BLOCKED",
    }


def test_provider_baseline_persistent_server_error_is_fail_closed_with_history() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                502,
                json={"error": {"code": "upstream_error", "type": "server_error"}},
            )
        )
    )
    with pytest.raises(BASELINE.BaselineProbeError) as error:
        BASELINE.probe(
            url="http://omniroute.test",
            gateway_key="gateway-secret",
            client=client,
            max_attempts=2,
            retry_delay_seconds=0,
            sleep=lambda _: None,
        )
    client.close()
    assert error.value.http_status == 502
    assert error.value.attempt_history[0]["failure"] == "PROVIDER_SERVER_ERROR"
    assert len(error.value.attempt_history) == 2
    assert all(item["retryable"] is True for item in error.value.attempt_history)


def test_provider_baseline_does_not_retry_model_not_found() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": {"code": "model_not_found"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(BASELINE.BaselineProbeError) as error:
        BASELINE.probe(
            url="http://omniroute.test",
            gateway_key="gateway-secret",
            client=client,
            max_attempts=3,
            retry_delay_seconds=0,
            sleep=lambda _: pytest.fail("model-not-found must not retry"),
        )
    client.close()
    assert calls == 1
    assert error.value.attempt_history == [
        {
            "attempt": 1,
            "failure": "PROVIDER_MODEL_NOT_FOUND",
            "http_status": 404,
            "provider_error_code": "model_not_found",
            "retryable": False,
            "status": "BLOCKED",
        }
    ]


def test_provider_baseline_404_is_model_unavailable_not_harness_failure() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"error": "redacted"}))
    )
    with pytest.raises(BASELINE.BaselineProbeError) as error:
        BASELINE.probe(url="http://omniroute.test", gateway_key="gateway-secret", client=client)
    client.close()
    assert error.value.stage == "provider"
    assert error.value.http_status == 404
    assert str(error.value) == "baseline_model_unavailable"


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


def test_auto_route_404_is_not_reported_as_fixed_provider_model_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/monitoring/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/health/readiness":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            return httpx.Response(404, json={"error": "redacted"})
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
    assert str(error.value) == "auto_no_valid_providers"
    assert error.value.stage == "provider"


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
        "model": "openai/auto/coding",
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
