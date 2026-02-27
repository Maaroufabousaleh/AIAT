"""BaseTool — abstract base class that every tool implementation must inherit.

Tool implementations reside in the tool-service. Each tool must:
1. Subclass ``BaseTool``.
2. Set class-level attributes: ``name``, ``group``, ``description``, etc.
3. Implement ``async execute(kwargs) -> Any``.

The tool-service registry discovers subclasses, wraps them with role-gating,
circuit breaker, rate limiting, and caching.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from mas_core.protocols.enums import AgentRole

from .groups import ToolGroup


class BaseTool(ABC):
    """Abstract base class for a tool implementation.

    Subclass this and register via the ``TOOL_REGISTRY`` in the tool-service.

    Class attributes
    ----------------
    name : str
        Dot-namespaced tool identifier (e.g. ``"web_search"``).
    group : ToolGroup
        Which rate-limit group this tool belongs to.
    description : str
        Human-readable description (exposed in GET /tools manifest).
    allowed_roles : list[AgentRole]
        Roles permitted to call this tool.
    blocked_roles : list[AgentRole]
        Roles explicitly denied regardless of allowed_roles.
    cache_ttl_seconds : int
        How long to cache results in Redis. 0 = no caching.
    idempotent : bool
        If True, identical kwargs always produce the same result (safe to cache).
    max_concurrency : int
        asyncio.Semaphore cap for this tool. 0 = unlimited.
    """

    name: ClassVar[str]
    group: ClassVar[ToolGroup]
    description: ClassVar[str] = ""
    allowed_roles: ClassVar[list[AgentRole]] = list(AgentRole)
    blocked_roles: ClassVar[list[AgentRole]] = []
    cache_ttl_seconds: ClassVar[int] = 30
    idempotent: ClassVar[bool] = True
    max_concurrency: ClassVar[int] = 5

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Run the tool with the given keyword arguments.

        Returns
        -------
        Any JSON-serialisable value on success.

        Raises
        ------
        Exception
            On failure — the tool-service translates this into a ``ToolResponse``
            with ``success=False`` and the exception message.
        """
        ...

    def to_manifest_entry(self) -> dict[str, Any]:
        """Serialise this tool's metadata for the GET /tools manifest."""
        from mas_core.protocols.tool import ToolManifestEntry

        entry = ToolManifestEntry(
            tool_name=self.name,
            tool_group=self.group.value,
            description=self.description,
            allowed_roles=list(self.allowed_roles),
            blocked_roles=list(self.blocked_roles),
            cache_ttl_seconds=self.cache_ttl_seconds,
            idempotent=self.idempotent,
        )
        return entry.model_dump(mode="json")
