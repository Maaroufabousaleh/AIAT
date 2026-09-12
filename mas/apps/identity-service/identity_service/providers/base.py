"""Provider-neutral mail contracts used by the identity service.

The application service owns identity state, authorization, approvals, usage,
and audit.  Providers only implement bounded transport operations.  In
particular, the inbound contract deliberately talks about an identity and a
message rather than a mailbox, JMAP account, or SMTP credential.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable


class MailProviderError(RuntimeError):
    """Sanitized provider failure shared by adapters.

    Provider response bodies and credentials must never cross this exception
    boundary.  ``code`` and ``correlation_id`` are safe operational metadata;
    ``transient`` controls retry/reconciliation policy.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        transient: bool = False,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient
        self.correlation_id = correlation_id


@runtime_checkable
class InboundMailProvider(Protocol):
    """Minimum inbound capability required by AIAT."""

    provider_name: str

    async def health_check(self) -> dict[str, Any]: ...

    async def provision_identity(
        self,
        address: str,
        *,
        identity_id: str,
        worker_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def activate_identity(
        self, provider_reference: str, *, identity_id: str, address: str
    ) -> dict[str, Any]: ...

    async def suspend_identity(
        self, provider_reference: str, *, identity_id: str, address: str
    ) -> dict[str, Any]: ...

    async def retire_identity(
        self, provider_reference: str, *, identity_id: str, address: str
    ) -> dict[str, Any]: ...

    async def list_events(self, *, after: int, limit: int) -> dict[str, Any]: ...

    async def fetch_message(self, message_id: str) -> dict[str, Any]: ...

    async def verify_delivery(
        self,
        provider_reference: str,
        provider_message_id: str,
        *,
        identity_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def acknowledge_event(self, event_id: str) -> dict[str, Any]: ...

    async def delete_message(
        self, provider_reference: str, message_id: str
    ) -> dict[str, Any]: ...



@runtime_checkable
class InboundMailRetentionProvider(Protocol):
    """Optional message-state/retention capabilities.

    A full-mailbox provider may expose processed state but not edge retention;
    a routing provider may expose both.  Neither capability is required for
    the core inbound transport contract.
    """

    async def mark_processed(
        self, provider_reference: str, message_id: str
    ) -> dict[str, Any]: ...

    async def protect_message(
        self, provider_reference: str, message_id: str, *, until: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class OutboundMailProvider(Protocol):
    """Minimum outbound capability required by AIAT."""

    provider_name: str

    async def health_check(self) -> dict[str, Any]: ...

    async def validate_sending_domain(self) -> dict[str, Any]: ...

    async def send_message(
        self,
        *,
        sender: str,
        recipients: list[str],
        subject: str,
        body: str,
        idempotency_key: str,
        content_type: str = "text/plain",
        provider_reference: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_delivery_status(self, provider_message_id: str) -> dict[str, Any]: ...

    async def cancel_message(self, provider_message_id: str) -> dict[str, Any]: ...


def provider_call(provider: Any, method: str, *args: Any, **kwargs: Any) -> Awaitable[Any]:
    """Return a provider operation while keeping duck-typed test doubles valid."""

    operation: Callable[..., Awaitable[Any]] | None = getattr(provider, method, None)
    if operation is None or not callable(operation):
        raise AttributeError(f"provider does not implement {method}")
    return operation(*args, **kwargs)


__all__ = [
    "InboundMailProvider",
    "InboundMailRetentionProvider",
    "MailProviderError",
    "OutboundMailProvider",
    "provider_call",
]
