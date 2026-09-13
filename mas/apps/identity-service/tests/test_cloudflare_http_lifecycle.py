from __future__ import annotations

import json

import httpx
import pytest
from identity_service.config import IdentitySettings
from identity_service.main import create_app
from identity_service.providers.factory import build_inbound_provider
from identity_service.providers.inbound.cloudflare import (
    CloudflareInboundAdapter,
    CloudflareProviderError,
)
from identity_service.store import InMemoryIdentityStore


def _health_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"status": "ok"},
        request=request,
    )


@pytest.mark.anyio
async def test_cloudflare_adapter_reuses_injected_client_and_regenerates_auth_headers() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _health_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = CloudflareInboundAdapter(
            edge_url="https://mail-edge.example",
            auth_secret="fixture-cloudflare-secret-012345",
            client=client,
        )
        assert adapter._owns_client is False
        await adapter.health_check()
        await adapter.health_check()
        await adapter.aclose()
        assert client.is_closed is False

    assert len(requests) == 2
    nonces = [request.headers["X-AIAT-Mail-Edge-Nonce"] for request in requests]
    signatures = [request.headers["X-AIAT-Mail-Edge-Signature"] for request in requests]
    assert len(set(nonces)) == 2
    assert len(set(signatures)) == 2


@pytest.mark.anyio
async def test_cloudflare_adapter_owned_client_has_bounded_http2_pool_and_closes_idempotently() -> (
    None
):
    adapter = CloudflareInboundAdapter(
        edge_url="https://mail-edge.example",
        auth_secret="fixture-cloudflare-secret-012345",
    )
    assert adapter._owns_client is True
    assert adapter._client is None
    client = adapter._ensure_client()
    pool = client._transport._pool  # type: ignore[attr-defined]
    assert pool._max_connections == 20
    assert pool._max_keepalive_connections == 10
    assert pool._http2 is True
    try:
        assert client.is_closed is False
        await adapter.aclose()
        await adapter.aclose()
        assert client.is_closed is True
    finally:
        await adapter.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("connect", "CLOUDFLARE_MAIL_EDGE_UNAVAILABLE"),
        ("timeout", "CLOUDFLARE_MAIL_EDGE_TIMEOUT"),
    ],
)
async def test_cloudflare_transient_transport_failures_are_classified_without_retry(
    failure: str,
    expected_code: str,
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "connect":
            raise httpx.ConnectError("fixture DNS failure", request=request)
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = CloudflareInboundAdapter(
            edge_url="https://mail-edge.example",
            auth_secret="fixture-cloudflare-secret-012345",
            client=client,
        )
        with pytest.raises(CloudflareProviderError) as error:
            await adapter.health_check()

    assert calls == 1
    assert error.value.code == expected_code
    assert error.value.transient is True


@pytest.mark.anyio
async def test_cloudflare_permanent_4xx_is_not_retried_or_exposed() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            403,
            json={"error": "provider secret and response detail must not escape"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = CloudflareInboundAdapter(
            edge_url="https://mail-edge.example",
            auth_secret="fixture-cloudflare-secret-012345",
            client=client,
        )
        with pytest.raises(CloudflareProviderError) as error:
            await adapter.health_check()

    assert calls == 1
    assert error.value.code == "CLOUDFLARE_MAIL_EDGE_REJECTED"
    assert error.value.transient is False
    assert "provider secret" not in str(error.value)


@pytest.mark.anyio
async def test_provider_factory_owned_client_is_closed_by_fastapi_lifespan() -> None:
    settings = IdentitySettings(outbound_relay_provider="disabled")
    factory_adapter = build_inbound_provider(settings)
    assert isinstance(factory_adapter, CloudflareInboundAdapter)
    factory_client = factory_adapter._ensure_client()
    await factory_adapter.aclose()
    assert factory_client.is_closed is True

    app = create_app(settings=settings, store=InMemoryIdentityStore())
    async with app.router.lifespan_context(app):
        provider = app.state.identity_service.inbound_provider
        assert isinstance(provider, CloudflareInboundAdapter)
        app_client = provider._ensure_client()
        assert app_client.is_closed is False
    assert app_client.is_closed is True


@pytest.mark.anyio
async def test_fastapi_startup_failure_still_closes_owned_cloudflare_client() -> None:
    captured: dict[str, CloudflareInboundAdapter] = {}
    captured_client: dict[str, httpx.AsyncClient] = {}

    class FailingStore(InMemoryIdentityStore):
        async def ensure_client_registration(self, **kwargs):
            captured["provider"] = app.state.identity_service.inbound_provider
            captured_client["client"] = captured["provider"]._ensure_client()
            return await super().ensure_client_registration(**kwargs)

    settings = IdentitySettings(
        identity_client_public_keys_json=json.dumps({"operator": "configured-key"}),
        identity_client_scopes_json=json.dumps({"operator": []}),
        outbound_relay_provider="disabled",
    )
    store = FailingStore()
    await InMemoryIdentityStore.ensure_client_registration(
        store,
        client_id="operator",
        public_key="registered-key",
        scopes=[],
    )
    app = create_app(settings=settings, store=store)

    with pytest.raises(RuntimeError, match="registration mismatch"):
        async with app.router.lifespan_context(app):
            pass

    provider = captured["provider"]
    client = captured_client["client"]
    assert provider._closed is True
    assert client.is_closed is True
