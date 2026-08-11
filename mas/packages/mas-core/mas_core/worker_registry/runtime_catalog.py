"""Canonical runtime catalogue shared by worker validation and the API.

The catalogue describes the *adapter contract* for a runtime.  It does not
make a runtime an authority: the AIAT worker shell, tool service, permissions,
and approval paths remain the control plane.  Package availability is reported
at runtime and is deliberately not treated as provenance or licence policy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeDefinition:
    """Static contract metadata for one worker runtime tier."""

    runtime_id: str
    required_imports: tuple[str, ...] = ()
    optional: bool = False
    supported_transports: tuple[str, ...] = ()
    supported_isolation_modes: tuple[str, ...] = ()


RUNTIME_CATALOG: dict[str, RuntimeDefinition] = {
    "builtin": RuntimeDefinition(
        "builtin",
        supported_transports=("native", "process", "human"),
        supported_isolation_modes=("native",),
    ),
    "langgraph": RuntimeDefinition(
        "langgraph",
        required_imports=("langgraph",),
        supported_transports=("native", "process", "http", "oci", "mcp"),
        supported_isolation_modes=("langgraph",),
    ),
    "crewai": RuntimeDefinition(
        "crewai",
        required_imports=("crewai",),
        supported_transports=("native", "process", "http", "oci", "mcp"),
        supported_isolation_modes=("crewai",),
    ),
    "microsoft_agent_framework": RuntimeDefinition(
        "microsoft_agent_framework",
        required_imports=("agent_framework",),
        supported_transports=("native", "process", "http", "oci", "mcp"),
        supported_isolation_modes=("microsoft_agent_framework",),
    ),
    "autogen": RuntimeDefinition(
        "autogen",
        required_imports=("autogen_agentchat", "autogen_core"),
        optional=True,
        supported_transports=("native", "process", "http", "oci", "mcp"),
        supported_isolation_modes=("autogen",),
    ),
    "letta": RuntimeDefinition(
        "letta",
        required_imports=("letta",),
        optional=True,
        supported_transports=("native", "process", "http", "oci", "mcp"),
        supported_isolation_modes=("letta",),
    ),
    "external": RuntimeDefinition(
        "external",
        supported_transports=("process", "http", "oci", "mcp", "opencode"),
        supported_isolation_modes=("wrapper", "fork", "opencode"),
    ),
}

RUNTIME_REQUIRED_PACKAGES: dict[str, tuple[str, ...]] = {
    runtime_id: definition.required_imports
    for runtime_id, definition in RUNTIME_CATALOG.items()
    if definition.required_imports
}

OPTIONAL_RUNTIME_IDS = frozenset(
    runtime_id for runtime_id, definition in RUNTIME_CATALOG.items() if definition.optional
)

RUNTIME_CATALOG_IDS = frozenset(RUNTIME_CATALOG)
