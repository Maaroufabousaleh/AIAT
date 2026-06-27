"""Dynamic tool catalog helpers for agent runtimes.

The tool-service is the enforcement point. This module only decides which
tools are shown to an agent's LLM loop and prompt from the canonical SDK
manifest plus the communication policy.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

from ..llm_gateway.models import ToolDefinition, ToolFunction
from ..policy.tool_access import can_use_tool_with_metadata

if TYPE_CHECKING:
    from ..protocols.enums import AgentRole


_GENERIC_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


_OPTIONAL_TOOL_MODULES: dict[str, str] = {
    "browser_": "playwright",
}


def _optional_dependency_available(tool_name: str) -> bool:
    for prefix, module_name in _OPTIONAL_TOOL_MODULES.items():
        if tool_name.startswith(prefix):
            return find_spec(module_name) is not None
    return True


def _manifest_entries(runtime_tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return canonical tool manifest entries, or an empty list if unavailable."""
    if runtime_tools is not None:
        return [dict(entry) for entry in runtime_tools]

    try:
        from mas_tools_sdk.manifest import all_manifest_entries
    except Exception:
        return []
    return [
        entry
        for entry in all_manifest_entries(include_aliases=False)
        if _optional_dependency_available(str(entry.get("tool_name") or ""))
    ]


def _is_allowed_tool(
    entry: dict[str, Any],
    *,
    role: AgentRole,
    team_id: str | None,
    configured_tools: set[str] | None,
) -> bool:
    name = str(entry.get("tool_name") or "")
    if not name:
        return False

    result = can_use_tool_with_metadata(
        role=role,
        tool_name=name,
        sender_team=team_id,
        allowed_roles=entry.get("allowed_roles") or (),
        blocked_roles=entry.get("blocked_roles") or (),
    )
    return result is True


def tool_definitions_for_agent(
    *,
    role: AgentRole,
    team_id: str | None = None,
    configured_tools: list[str] | None = None,
    runtime_tools: list[dict[str, Any]] | None = None,
) -> list[ToolDefinition]:
    """Build OpenAI-compatible tool definitions for one agent.

    ``runtime_tools`` should be the running tool-service ``/tools`` manifest
    when available. ``configured_tools`` is additive. Policy/manifest-allowed
    tools remain visible so YAML files can document local defaults without
    becoming a static blocker when new tools are added to the manifest.
    """
    configured = set(configured_tools) if configured_tools is not None else None
    definitions: list[ToolDefinition] = []
    seen: set[str] = set()

    for entry in _manifest_entries(runtime_tools):
        name = str(entry.get("tool_name") or "")
        if name in seen:
            continue
        if not _is_allowed_tool(entry, role=role, team_id=team_id, configured_tools=configured):
            continue
        seen.add(name)
        definitions.append(
            ToolDefinition(
                function=ToolFunction(
                    name=name,
                    description=str(entry.get("description") or ""),
                    parameters=dict(entry.get("parameters") or _GENERIC_PARAMETERS),
                )
            )
        )

    return definitions


def tool_catalog_prompt(definitions: list[ToolDefinition], *, limit: int = 80) -> str:
    """Render a compact prompt block listing runtime-discovered tools."""
    if not definitions:
        return ""

    lines = [
        "## Runtime Tool Catalog",
        "These tools are discovered from the canonical tool manifest and policy at startup.",
        "Use only tools exposed through structured tool calls.",
    ]
    for definition in definitions[:limit]:
        desc = definition.function.description.strip()
        if desc:
            lines.append(f"- `{definition.function.name}`: {desc}")
        else:
            lines.append(f"- `{definition.function.name}`")
    if len(definitions) > limit:
        lines.append(f"- ... {len(definitions) - limit} additional tool(s) omitted from prompt.")
    return "\n".join(lines)
