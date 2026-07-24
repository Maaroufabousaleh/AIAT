"""Network isolation tests for generic and governed browser sessions."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock

import pytest
from tool_service.tools.browser import BrowserSession, _chromium_args, _validate_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:password@example.com/",
        "http://10.0.0.1/",
        "http://127.0.0.2/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_browser_rejects_non_web_and_non_public_literal_targets(url: str) -> None:
    with pytest.raises(ValueError, match="URL blocked"):
        _validate_url(url)


def test_browser_rejects_private_ipv6_dns_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::25", 0, 0, 0)),
        ],
    )
    with pytest.raises(ValueError, match="non-public"):
        _validate_url("https://public-looking.example/")


def test_browser_accepts_only_public_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ],
    )
    _validate_url("https://example.com/")


@pytest.mark.anyio
async def test_browser_route_guard_blocks_redirects_to_private_network() -> None:
    blocked = AsyncMock()
    blocked.request.url = "http://10.0.0.1/admin"
    await BrowserSession._guard_request(blocked)
    blocked.abort.assert_awaited_once_with("blockedbyclient")
    blocked.continue_.assert_not_awaited()


def test_production_cannot_disable_chromium_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    monkeypatch.setenv("AIAT_BROWSER_DISABLE_CHROMIUM_SANDBOX", "true")
    with pytest.raises(PermissionError, match="sandbox cannot be disabled"):
        _chromium_args()


def test_chromium_sandbox_is_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIAT_BROWSER_DISABLE_CHROMIUM_SANDBOX", raising=False)
    assert "--no-sandbox" not in _chromium_args()
