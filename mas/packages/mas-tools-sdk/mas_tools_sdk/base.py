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
    transport : str
        Execution transport for this tool: internal | http | mcp | process.
    input_model / output_model : type[pydantic.BaseModel] | None
        Optional typed contracts.  Legacy tools keep a permissive object
        schema until their implementation is migrated.
    """

    name: ClassVar[str]
    group: ClassVar[ToolGroup]
    description: ClassVar[str] = ""
    allowed_roles: ClassVar[list[AgentRole]] = list(AgentRole)
    blocked_roles: ClassVar[list[AgentRole]] = []
    cache_ttl_seconds: ClassVar[int] = 30
    idempotent: ClassVar[bool] = True
    max_concurrency: ClassVar[int] = 5
    transport: ClassVar[str] = "internal"
    schema_version: ClassVar[str] = "1"
    input_model: ClassVar[Any | None] = None
    output_model: ClassVar[Any | None] = None
    risk_tier: ClassVar[str] = "standard"
    approval_policy: ClassVar[str] = "role"
    credential_requirements: ClassVar[list[str]] = []
    side_effect: ClassVar[bool] = True

    def validate_input(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize typed kwargs while preserving AIAT context."""
        if self.input_model is None:
            return kwargs
        context = kwargs.get("_aiat_context")
        reserved = {
            "project_id",
            "worker_run_id",
            "caller_id",
            "caller_role",
            "caller_team",
            "permission_scope",
            "budget_snapshot",
            "audit_context",
            "idempotency_key",
        }
        payload = {
            key: value
            for key, value in kwargs.items()
            if key != "_aiat_context" and key not in reserved
        }
        validated = self.input_model.model_validate(payload)
        normalized = validated.model_dump(mode="json", exclude_none=False)
        for key in reserved:
            if key in kwargs and key not in normalized:
                normalized[key] = kwargs[key]
        if context is not None:
            normalized["_aiat_context"] = context
        return normalized

    def validate_output(self, value: Any) -> Any:
        """Validate a typed result when the tool declares an output model."""
        if self.output_model is None:
            return value
        return self.output_model.model_validate(value).model_dump(mode="json")

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

        input_schema = (
            self.input_model.model_json_schema(mode="serialization")
            if self.input_model is not None
            else {"type": "object", "additionalProperties": True}
        )
        output_schema = (
            self.output_model.model_json_schema(mode="serialization")
            if self.output_model is not None
            else {}
        )
        entry = ToolManifestEntry(
            tool_name=self.name,
            tool_group=self.group.value,
            description=self.description,
            allowed_roles=list(self.allowed_roles),
            blocked_roles=list(self.blocked_roles),
            cache_ttl_seconds=self.cache_ttl_seconds,
            idempotent=self.idempotent,
            transport=self.transport,
            schema_version=self.schema_version,
            input_schema=input_schema,
            output_schema=output_schema,
            schema_status="declared" if self.input_model is not None or self.output_model is not None else "legacy",
            risk_tier=self.risk_tier,
            approval_policy=self.approval_policy,
            credential_requirements=list(self.credential_requirements),
            side_effect=self.side_effect,
        )
        return entry.model_dump(mode="json")
