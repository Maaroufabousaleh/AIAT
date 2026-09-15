"""Provider-neutral mail adapters and contracts."""

from .base import InboundMailProvider, MailProviderError, OutboundMailProvider
from .inbound.cloudflare import CloudflareEdgeFixture, CloudflareInboundAdapter
from .resend import ResendRelayAdapter
from .stalwart import StalwartAdapter

__all__ = [
    "CloudflareEdgeFixture",
    "CloudflareInboundAdapter",
    "InboundMailProvider",
    "MailProviderError",
    "OutboundMailProvider",
    "ResendRelayAdapter",
    "StalwartAdapter",
]
