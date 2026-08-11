"""Small HTTP/credential helpers shared by built-in adapters."""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from ..contracts import ProviderConnection

CredentialResolver = Callable[[str], str | Awaitable[str]]
ProviderTokenResolver = Callable[["ProviderConnection"], str | Awaitable[str]]


def provider_ssl_context() -> ssl.SSLContext:
    """Build a certificate-verifying context for provider HTTPS calls.

    ``httpx`` normally loads certifi's bundle.  The deployed containers also
    maintain the operating-system trust bundle, which is the correct place for
    an explicitly installed enterprise inspection root.  Loading that bundle
    preserves normal certificate and hostname verification while allowing the
    container's managed trust store to be honored.  A deployment may override
    it with ``AIAT_PROVIDER_CA_BUNDLE``; verification is never disabled.
    """
    context = ssl.create_default_context()
    bundle = os.getenv("AIAT_PROVIDER_CA_BUNDLE") or "/etc/ssl/certs/ca-certificates.crt"
    if os.path.isfile(bundle):
        context.load_verify_locations(cafile=bundle)
    return context


class ProviderRequestError(RuntimeError):
    """Provider HTTP failure with retry metadata retained for the outbox."""

    def __init__(self, method: str, path: str, status_code: int, detail: str, retry_after: int | None = None) -> None:
        super().__init__(f"provider request {method} {path} failed ({status_code}): {detail}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.retry_after = retry_after


# Provider adapters share one retry classification so the outbox, reconciliation
# loop, and conformance fixtures do not drift by provider.  Conflicts and
# precondition failures (409/412) are retryable only after the caller refreshes
# canonical/provider state; they are not permission approvals.
RETRYABLE_PROVIDER_STATUS_CODES = frozenset({408, 409, 412, 425, 429, 500, 502, 503, 504})


def provider_failure_is_permanent(status_code: object) -> bool:
    """Return whether an HTTP provider failure should not be blindly retried."""

    return (
        isinstance(status_code, int)
        and 400 <= status_code < 500
        and status_code not in RETRYABLE_PROVIDER_STATUS_CODES
    )


def provider_failure_disposition(status_code: object) -> str:
    """Return the stable fixture vocabulary for a provider HTTP status."""

    if not isinstance(status_code, int) or status_code < 400:
        return "not_an_error"
    if provider_failure_is_permanent(status_code):
        return "permanent"
    if status_code in RETRYABLE_PROVIDER_STATUS_CODES or status_code >= 500:
        return "retryable"
    return "permanent"


def validate_provider_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("provider URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider URL must not contain credentials, query, or fragment")
    return value.rstrip("/")


async def resolve_secret(connection: ProviderConnection, resolver: CredentialResolver | None, ref: str | None = None) -> str:
    secret_ref = ref or connection.credential_ref
    if resolver is None:
        raise RuntimeError(f"credential resolver is required for {secret_ref}")
    value = resolver(secret_ref)
    if hasattr(value, "__await__"):
        value = await value
    if not value:
        raise RuntimeError(f"credential resolver returned an empty secret for {secret_ref}")
    return str(value)


class ProviderHTTP:
    def __init__(
        self,
        resolver: CredentialResolver | None = None,
        *,
        timeout: float = 20.0,
        token_resolver: ProviderTokenResolver | None = None,
    ) -> None:
        self.resolver = resolver
        self.timeout = timeout
        self.token_resolver = token_resolver

    async def request(
        self,
        connection: ProviderConnection,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        token_ref: str | None = None,
    ) -> httpx.Response:
        base = validate_provider_url(connection.base_url)
        if not path.startswith("/"):
            path = "/" + path
        if token_ref is not None or self.token_resolver is None:
            token = await resolve_secret(connection, self.resolver, token_ref)
        else:
            token = self.token_resolver(connection)
            if hasattr(token, "__await__"):
                token = await token
            if not token:
                raise RuntimeError("provider token resolver returned an empty token")
            token = str(token)
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        request_headers.update(headers or {})
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            verify=provider_ssl_context(),
        ) as client:
            response = await client.request(
                method,
                base + path,
                headers=request_headers,
                json=json_body,
                params=params,
            )
        if response.status_code >= 400:
            detail = response.text[:500]
            retry_after: int | None = None
            try:
                retry_after = max(0, int(response.headers.get("Retry-After", "")))
            except (TypeError, ValueError):
                retry_after = None
            raise ProviderRequestError(method, path, response.status_code, detail, retry_after)
        return response


def response_json(response: httpx.Response) -> dict[str, Any] | list[Any]:
    try:
        value = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("provider returned invalid JSON") from exc
    if not isinstance(value, (dict, list)):
        raise RuntimeError("provider returned an unsupported JSON shape")
    return value
