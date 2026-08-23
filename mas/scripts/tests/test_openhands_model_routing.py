"""Pure governance tests for the OpenHands model-routing boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load(name: str):
    script = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROUTING = _load("openhands_model_routing")


def test_worker_alias_is_stable_and_auto_route_is_gateway_owned() -> None:
    assert ROUTING.AIAT_MODEL_ID == "omniroute-coding"
    assert ROUTING.AUTO_ROUTER_MODEL == "auto/coding"
    assert ROUTING.auto_router_model_override_allowed("omniroute-coding") is True
    assert ROUTING.auto_router_model_override_allowed("auto/coding") is False
    assert ROUTING.auto_router_model_override_allowed("groq/openai/gpt-oss-120b") is False


def test_baseline_requires_live_discovery_and_never_falls_back() -> None:
    report = ROUTING.baseline_discovery_status(
        provider="groq",
        desired_model=ROUTING.CERTIFICATION_BASELINE_MODEL,
        discovery_payload={
            "source": "api",
            "models": [{"id": ROUTING.CERTIFICATION_BASELINE_MODEL}],
        },
    )
    assert report["status"] == "PASS"
    assert report["model_present"] is True

    absent = ROUTING.baseline_discovery_status(
        provider="groq",
        desired_model=ROUTING.CERTIFICATION_BASELINE_MODEL,
        discovery_payload={"source": "api", "models": [{"id": "other-model"}]},
    )
    assert absent["status"] == "BASELINE_MODEL_UNAVAILABLE"
    assert absent["model_present"] is False

    local_catalog = ROUTING.baseline_discovery_status(
        provider="groq",
        desired_model=ROUTING.CERTIFICATION_BASELINE_MODEL,
        discovery_payload={
            "source": "local_catalog",
            "models": [{"id": ROUTING.CERTIFICATION_BASELINE_MODEL}],
        },
    )
    assert local_catalog["status"] == "BASELINE_MODEL_UNAVAILABLE"
    assert local_catalog["live_discovery"] is False


def test_provider_secret_mapping_is_explicit_and_bounded() -> None:
    assert ROUTING.governed_provider_secret_names(["groq", "gemini"]) == {
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    with pytest.raises(ValueError, match="unsupported certification provider"):
        ROUTING.governed_provider_secret_names(["arbitrary-provider"])


def _connections() -> list[dict[str, object]]:
    return [
        {"provider": "groq", "model": "openai/gpt-oss-120b", "credential_present": True},
        {"provider": "gemini", "model": "gemini-2.5-pro", "credential_present": True},
        {"provider": "cerebras", "model": "qwen-3-32b", "credential_present": False},
    ]


def test_auto_router_fixture_covers_one_and_multiple_provider_pools() -> None:
    one = ROUTING.simulate_auto_route(_connections(), allowed_providers=["groq"])
    assert one["status"] == "PASS"
    assert one["candidate_count"] == 1
    multiple = ROUTING.simulate_auto_route(_connections(), allowed_providers=["groq", "gemini"])
    assert multiple["status"] == "PASS"
    assert multiple["candidate_count"] == 2


def test_auto_router_fixture_falls_back_and_excludes_unhealthy_or_disallowed() -> None:
    fallback = ROUTING.simulate_auto_route(
        _connections(),
        allowed_providers=["groq", "gemini"],
        failing_providers=["groq"],
    )
    assert fallback["status"] == "PASS"
    assert fallback["fallback_used"] is True
    assert fallback["selected"] == {"provider": "gemini", "model": "gemini-2.5-pro"}

    excluded = ROUTING.build_governed_auto_pool(
        [
            {"provider": "groq", "model": "m", "credential_present": True, "healthy": False},
            {"provider": "gemini", "model": "m", "credential_present": True},
        ],
        allowed_providers=["gemini"],
    )
    assert excluded == [{"provider": "gemini", "model": "m"}]


def test_auto_router_fixture_fails_closed_without_valid_providers() -> None:
    report = ROUTING.simulate_auto_route(
        _connections(),
        allowed_providers=["groq"],
        failing_providers=["groq"],
    )
    assert report["status"] == "BLOCKED_NO_VALID_PROVIDERS"
    assert report["credential_values_retained"] is False
