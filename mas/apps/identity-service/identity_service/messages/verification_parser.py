"""Bounded extraction of verification codes and safe HTTP(S) links."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_CODE = re.compile(r"(?<!\d)(\d{4,10})(?!\d)")
_URL = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def message_text(message: Any) -> str:
    """Extract human message content before scanning structured metadata.

    Provider responses contain dates and IDs that can look like verification
    codes.  Prefer decoded body parts, then the preview/subject, and only fall
    back to a generic representation for unstructured providers.
    """
    if not isinstance(message, dict):
        return str(message)
    result = message.get("result")
    if isinstance(result, dict):
        listed = result.get("list")
        if isinstance(listed, list) and listed and isinstance(listed[0], dict):
            return message_text(listed[0])
    parts: list[str] = []
    body_values = message.get("bodyValues")
    values = body_values.values() if isinstance(body_values, dict) else body_values if isinstance(body_values, list) else []
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("value"), str):
            parts.append(value["value"])
    for key in ("preview", "subject"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    return "\n".join(parts) if parts else str(message)


def extract_verification_code(text: str) -> str | None:
    match = _CODE.search(text[:100_000])
    return match.group(1) if match else None


def extract_verification_link(text: str) -> str | None:
    for raw in _URL.findall(text[:100_000]):
        parsed = urlparse(raw.rstrip(".,);]"))
        if parsed.scheme in {"https", "http"} and parsed.hostname and parsed.username is None:
            return parsed.geturl()
    return None
