"""Deterministic success-path coverage for the governed web fetch tool."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import httpx
import pytest
from tool_service.tools import web as web_tools

if TYPE_CHECKING:
    from collections.abc import Callable


# The repository's autouse fixture replaces execute on the class per-test.
# Keep the real implementation before that fixture runs, then give it only an
# in-memory transport below so these tests can never reach the public network.
_REAL_WEB_FETCH_EXECUTE = web_tools.WebFetchTool.execute
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_local_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(handle)

    def local_client(*args, **kwargs) -> httpx.AsyncClient:
        assert "transport" not in kwargs
        return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

    monkeypatch.setattr(web_tools.httpx, "AsyncClient", local_client)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ],
    )
    return requests


@pytest.mark.anyio
async def test_web_fetch_extracts_html_without_script_or_style_and_follows_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """<!doctype html>
    <html><head><style>private-style-text</style>
    <script>private-script-text()</script></head>
    <body><h1>Expense report</h1><p>Totals <strong>ready</strong> today</p></body></html>"""

    def response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"location": "/article"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html.encode(),
            request=request,
        )

    requests = _install_local_http(monkeypatch, response)
    result = await _REAL_WEB_FETCH_EXECUTE(
        web_tools.WebFetchTool(),
        url="https://example.com/start",
    )

    assert [request.url.path for request in requests] == ["/start", "/article"]
    assert result == {
        "url": "https://example.com/article",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "content": "Expense report Totals ready today",
        "truncated": False,
    }
    assert "private-script-text" not in result["content"]
    assert "private-style-text" not in result["content"]


@pytest.mark.anyio
async def test_web_fetch_truncates_extracted_html_to_10000_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "x" * 10_025

    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=f"<p>{content}</p>".encode(),
            request=request,
        )

    _install_local_http(monkeypatch, response)
    result = await _REAL_WEB_FETCH_EXECUTE(
        web_tools.WebFetchTool(),
        url="https://example.com/long",
    )

    assert result["content"] == "x" * 10_000
    assert result["truncated"] is True


@pytest.mark.anyio
async def test_web_fetch_revalidates_redirect_targets_before_requesting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=request,
        )

    requests = _install_local_http(monkeypatch, response)

    with pytest.raises(ValueError, match="not allowed"):
        await _REAL_WEB_FETCH_EXECUTE(
            web_tools.WebFetchTool(),
            url="https://example.com/redirect",
        )

    # Only the first, public URL reaches the local MockTransport. The blocked
    # redirect target is rejected before any request is made to it.
    assert [request.url.host for request in requests] == ["example.com"]
