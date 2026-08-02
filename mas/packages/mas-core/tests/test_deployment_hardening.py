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
