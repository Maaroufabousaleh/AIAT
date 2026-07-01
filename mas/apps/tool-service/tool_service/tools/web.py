"""WEB group tools: web_search, web_fetch."""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}
_BLOCKED_SUFFIXES = (".local", ".internal", ".corp", ".lan", ".home", ".intranet")


def _is_non_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not ip.is_global


def _validate_public_url(url: str) -> None:
    if not url:
        raise ValueError("url is required")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must use http or https")

    hostname = parsed.hostname or ""
    lowered = hostname.lower()
    if lowered in _BLOCKED_HOSTS:
        raise ValueError(f"URL blocked: {hostname} is not allowed")
    if any(lowered.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        raise ValueError(f"URL blocked: {hostname} matches a blocked internal domain suffix")

    try:
        if _is_non_public_ip(hostname):
            raise ValueError(f"URL blocked: {hostname} is a private/internal address")
    except ValueError as exc:
        if "URL blocked" in str(exc):
            raise

    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
            if _is_non_public_ip(info[4][0]):
                raise ValueError(
                    f"URL blocked: {hostname} resolves to a private address ({info[4][0]})"
                )
    except socket.gaierror:
        pass


class WebSearchTool(BaseTool):
    name = "web_search"
    group = ToolGroup.KPI_UTILITY
    description = "Search the web via a search API and return top results."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 60
    idempotent = True
    max_concurrency = 3

    async def execute(self, **kwargs: Any) -> Any:
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 5)

        if not query:
            raise ValueError("query is required")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                ddg_url = "https://api.duckduckgo.com/"
                params = {
                    "q": query,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                }
                resp = await client.get(ddg_url, params=params)
                resp.raise_for_status()
                data = resp.json()

                results = []
                if "RelatedTopics" in data:
                    for item in data["RelatedTopics"][:max_results]:
                        results.append(
                            {
                                "title": item.get("Text", "").split(" - ")[0]
                                if " - " in item.get("Text", "")
                                else item.get("Text", ""),
                                "url": item.get("FirstURL", ""),
                                "snippet": item.get("Text", ""),
                            }
                        )

                return {
                    "query": query,
                    "results": results,
                    "count": len(results),
                }
        except httpx.HTTPError as e:
            logger.error("web_search_error", extra={"query": query, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Web search failed: {e}") from e


class WebFetchTool(BaseTool):
    name = "web_fetch"
    group = ToolGroup.KPI_UTILITY
    description = "Fetch the contents of a URL and return text/HTML."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 60
    idempotent = True
    max_concurrency = 3

    async def execute(self, **kwargs: Any) -> Any:
        url = kwargs.get("url", "")
        extract_text = kwargs.get("extract_text", True)

        if not url:
            raise ValueError("url is required")
        _validate_public_url(url)

        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AIAT/1.0)"},
            ) as client:
                current_url = url
                for _redirect in range(6):
                    _validate_public_url(current_url)
                    resp = await client.get(current_url)
                    if not resp.is_redirect:
                        break
                    location = resp.headers.get("location")
                    if not location:
                        raise RuntimeError("Redirect response did not include a location")
                    current_url = str(resp.url.join(location))
                else:
                    raise RuntimeError("Too many redirects")
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                content = resp.text

                result = {
                    "url": str(resp.url),
                    "status_code": resp.status_code,
                    "content_type": content_type,
                }

                if "text/html" in content_type and extract_text:
                    import re

                    clean = re.sub(
                        r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE
                    )
                    clean = re.sub(
                        r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.IGNORECASE
                    )
                    clean = re.sub(r"<[^>]+>", " ", clean)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    result["content"] = clean[:10000]
                    result["truncated"] = len(clean) > 10000
                else:
                    result["content"] = content[:50000]
                    result["truncated"] = len(content) > 50000

                return result
        except httpx.HTTPError as e:
            logger.error("web_fetch_error", extra={"url": url, "error": str(e)}, exc_info=True)
            raise RuntimeError(f"Web fetch failed: {e}") from e
