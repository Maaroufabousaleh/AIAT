from __future__ import annotations

from pathlib import Path

import yaml

MAS_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_ROOT = MAS_ROOT / "infra" / "compose"


def test_cloudflare_tunnel_is_opt_in_and_empty_token_safe() -> None:
    compose = yaml.safe_load((COMPOSE_ROOT / "docker-compose.yml").read_text())
    service = compose["services"]["cloudflared"]

    assert service["profiles"] == ["cloudflare"]
    assert service["environment"]["TUNNEL_TOKEN"] == "${CLOUDFLARE_TUNNEL_TOKEN:-}"


def test_local_ca_is_only_referenced_by_the_explicit_ca_overlay() -> None:
    dev_text = (COMPOSE_ROOT / "docker-compose.dev.yml").read_text()
    ca_text = (COMPOSE_ROOT / "docker-compose.dev-ca.yml").read_text()

    assert "local-ca/" not in dev_text
    assert "AIAT_PROVIDER_CA_BUNDLE" not in dev_text
    assert "local-ca/norton-webmail-shield-root.crt" in ca_text
    assert "AIAT_PROVIDER_CA_BUNDLE" in ca_text


def test_pm_gateway_build_keeps_pip_tls_verification_enabled() -> None:
    dockerfile = (MAS_ROOT / "infra" / "docker" / "Dockerfile.pm-gateway").read_text()

    assert "PIP_TRUSTED_HOST" not in dockerfile
    assert "ca-certificates" in dockerfile


def test_omniroute_api_bridge_is_split_from_dashboard_and_litellm_uses_it() -> None:
    compose = yaml.safe_load((COMPOSE_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    dev_compose = yaml.safe_load((COMPOSE_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    omniroute = compose["services"]["omniroute"]
    assert omniroute["environment"]["PORT"] == "20128"
    assert omniroute["environment"]["DASHBOARD_PORT"] == "20128"
    assert omniroute["environment"]["API_PORT"] == "20129"
    assert omniroute["environment"]["API_HOST"] == "0.0.0.0"
    assert dev_compose["services"]["omniroute"]["ports"] == ["20128:20128", "20129:20129"]

    litellm = yaml.safe_load((COMPOSE_ROOT / "litellm_config.yaml").read_text(encoding="utf-8"))
    routes = litellm["model_list"]
    assert routes
    assert all(route["litellm_params"]["api_base"] == "http://omniroute:20129/v1" for route in routes)
    legacy = next(route for route in routes if route["model_name"] == "llama-3.3-70b-versatile")
    assert legacy["litellm_params"]["model"] == "openai/auto/coding"


def test_omniroute_bootstrap_uses_current_governed_groq_baseline() -> None:
    script = (COMPOSE_ROOT / "configure_omniroute.py").read_text(encoding="utf-8")
    assert 'ProviderSpec("GROQ_API_KEY", "groq", "AIAT Groq", "openai/gpt-oss-120b")' in script
