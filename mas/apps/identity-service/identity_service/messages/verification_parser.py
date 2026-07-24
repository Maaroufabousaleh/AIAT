"""Bounded extraction of verification codes and safe HTTP(S) links."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_CODE = re.compile(r"(?<!\d)(\d{4,10})(?!\d)")
_URL = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def extract_verification_code(text: str) -> str | None:
    match = _CODE.search(text[:100_000])
    return match.group(1) if match else None


def extract_verification_link(text: str) -> str | None:
    for raw in _URL.findall(text[:100_000]):
        parsed = urlparse(raw.rstrip(".,);]"))
        if parsed.scheme in {"https", "http"} and parsed.hostname and parsed.username is None:
            return parsed.geturl()
    return None
