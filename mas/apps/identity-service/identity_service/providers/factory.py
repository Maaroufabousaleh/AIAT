"""Configuration-driven mail provider construction.

Only the selected provider is constructed.  This matters operationally: a
Cloudflare inbound deployment must not require, validate, or even initialize a
Stalwart credential boundary that it does not use.
"""

from __future__ import annotations

from ..config import IdentitySettings
from .base import InboundMailProvider, OutboundMailProvider
from .inbound.cloudflare import CloudflareInboundAdapter
from .resend import ResendRelayAdapter
from .stalwart import StalwartAdapter


def build_stalwart(settings: IdentitySettings) -> StalwartAdapter:
    return StalwartAdapter(
        base_url=settings.stalwart_public_url,
        api_key=settings.stalwart_api_key,
        jmap_service_token=settings.stalwart_jmap_service_token,
        timeout_seconds=settings.request_timeout_seconds,
    )


def build_inbound_provider(settings: IdentitySettings) -> InboundMailProvider:
    provider = settings.identity_inbound_provider
    if provider == "cloudflare":
        return CloudflareInboundAdapter(
            edge_url=settings.cloudflare_mail_edge_url,
            auth_secret=settings.cloudflare_mail_edge_auth_secret,
            timeout_seconds=settings.request_timeout_seconds,
            max_message_bytes=settings.mail_edge_max_message_bytes,
        )
    if provider == "stalwart":
        return build_stalwart(settings)
    # Settings validates this path, but retaining a hard failure here keeps
    # direct library callers fail-closed if they bypass model validation.
    raise ValueError(f"unsupported inbound identity provider: {provider}")


def build_outbound_provider(
    settings: IdentitySettings,
    *,
    stalwart: StalwartAdapter | None = None,
) -> OutboundMailProvider:
    provider = settings.identity_outbound_provider
    if provider == "resend":
        return ResendRelayAdapter(
            api_key=settings.resend_api_key,
            sending_domain=settings.agent_mail_domain,
            timeout_seconds=settings.request_timeout_seconds,
            webhook_signing_secret=settings.resend_webhook_signing_secret,
            webhook_tolerance_seconds=settings.resend_webhook_tolerance_seconds,
        )
    if provider == "stalwart":
        return stalwart or build_stalwart(settings)
    raise ValueError(f"unsupported outbound identity provider: {provider}")


def build_provider_pair(settings: IdentitySettings) -> tuple[InboundMailProvider, OutboundMailProvider]:
    inbound = build_inbound_provider(settings)
    stalwart = inbound if isinstance(inbound, StalwartAdapter) else None
    outbound = build_outbound_provider(settings, stalwart=stalwart)
    return inbound, outbound


__all__ = [
    "build_inbound_provider",
    "build_outbound_provider",
    "build_provider_pair",
    "build_stalwart",
]
