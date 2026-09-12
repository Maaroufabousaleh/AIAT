from __future__ import annotations

import base64
import json

import pytest

from identity_service.config import IdentitySettings
from identity_service.providers.factory import build_provider_pair
from identity_service.providers.inbound.cloudflare import CloudflareInboundAdapter
from identity_service.providers.resend import ResendRelayAdapter
from identity_service.providers.stalwart import StalwartAdapter


def _production_cloudflare_settings() -> IdentitySettings:
    return IdentitySettings(
        MAS_ENVIRONMENT="production",
        IDENTITY_PROFILE="production",
        identity_database_password="fixture-database-password",
        identity_service_secret="s" * 40,
        identity_content_encryption_key=base64.urlsafe_b64encode(b"e" * 32).decode(),
        identity_client_public_keys_json=json.dumps({"operator": base64.b64encode(b"p" * 32).decode()}),
        identity_client_scopes_json=json.dumps({"operator": ["identity:admin"]}),
        cloudflare_mail_edge_auth_secret="c" * 24,
        resend_api_key="r" * 24,
        resend_webhook_signing_secret="w" * 24,
        stalwart_api_key="",
        stalwart_jmap_service_token="",
    )


def test_cloudflare_default_does_not_construct_or_require_stalwart() -> None:
    settings = _production_cloudflare_settings()
    inbound, outbound = build_provider_pair(settings)

    assert isinstance(inbound, CloudflareInboundAdapter)
    assert isinstance(outbound, ResendRelayAdapter)
    assert settings.stalwart_api_key == ""
    assert settings.stalwart_jmap_service_token == ""


def test_stalwart_remains_selectable_as_an_explicit_profile() -> None:
    settings = IdentitySettings(
        identity_inbound_provider="stalwart",
        identity_outbound_provider="stalwart",
        stalwart_api_key="s" * 24,
        stalwart_jmap_service_token="j" * 24,
    )
    inbound, outbound = build_provider_pair(settings)

    assert isinstance(inbound, StalwartAdapter)
    assert inbound is outbound


def test_invalid_provider_name_fails_closed() -> None:
    with pytest.raises(ValueError, match="IDENTITY_INBOUND_PROVIDER"):
        IdentitySettings(identity_inbound_provider="unknown")
