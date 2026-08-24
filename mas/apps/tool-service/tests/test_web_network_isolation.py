"""Network-boundary tests for the generic web-fetch tool."""

from __future__ import annotations

import socket

import pytest
from tool_service.tools.web import _validate_public_url


def test_web_fetch_rejects_dns_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unresolved(*_args, **_kwargs):
        raise socket.gaierror("temporary resolver failure")

    monkeypatch.setattr(socket, "getaddrinfo", unresolved)
    with pytest.raises(ValueError, match="could not be resolved"):
        _validate_public_url("https://example.com/")


def test_web_fetch_accepts_a_public_dns_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ],
    )
    _validate_public_url("https://example.com/")
