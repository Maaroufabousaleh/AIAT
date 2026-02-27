"""WEB group tools: web_search, web_fetch."""

from __future__ import annotations

from typing import Any

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup


class WebSearchTool(BaseTool):
    name = "web_search"
    group = ToolGroup.WEB
    description = "Search the web via a search API and return top results."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 60
    idempotent = True
    max_concurrency = 3

    async def execute(self, **kwargs: Any) -> Any:
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 5)
        # Stub — return placeholder results
        return {
            "query": query,
            "results": [
                {"title": f"Result {i+1} for '{query}'", "url": f"https://example.com/{i+1}", "snippet": "..."}
                for i in range(min(max_results, 5))
            ],
        }


class WebFetchTool(BaseTool):
    name = "web_fetch"
    group = ToolGroup.WEB
    description = "Fetch the contents of a URL and return text/HTML."
    allowed_roles = [
        AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE,
        AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER,
    ]
    cache_ttl_seconds = 60
    idempotent = True
    max_concurrency = 3

    async def execute(self, **kwargs: Any) -> Any:
        url = kwargs.get("url", "")
        return {"url": url, "content": f"[stub] Fetched content from {url}", "content_type": "text/html"}
