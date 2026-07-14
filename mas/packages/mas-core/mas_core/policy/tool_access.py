"""Shared tool-access helpers for static policy plus dynamic tool metadata."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Iterable, Any

from mas_core.protocols.enums import AgentRole

from .engine import CommunicationPolicy
from .rules import (
    CSO_TEAM,
    CTO_EXTRA_TOOLS,
    CTO_TEAM,
    DEVOPS_PM_EXTRA_TOOLS,
    DEVOPS_TEAM,
    SUPPLEMENTAL_ADMIN_TOOLS,
    WORKER_BLOCKED_TOOLS,
)


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in patterns)


def _role_value(role: AgentRole | str | Any) -> str:
    value = getattr(role, "value", role)
    return str(value)


def _roles_include(roles: Iterable[AgentRole | str | Any], role: AgentRole) -> bool:
    role_value = _role_value(role)
    return any(_role_value(candidate) == role_value for candidate in roles)


def tool_metadata_allows_role(
    *,
    role: AgentRole,
    allowed_roles: Iterable[AgentRole | str | Any],
    blocked_roles: Iterable[AgentRole | str | Any] = (),
) -> bool:
    """Return whether a tool manifest/BaseTool role list permits ``role``."""
    if _roles_include(blocked_roles, role):
        return False
    return _roles_include(allowed_roles, role)


def has_authoritative_tool_denial(
    *,
    role: AgentRole,
    tool_name: str,
    sender_team: str | None = None,
) -> bool:
    """Return True when static policy must override metadata fallback.

    Dynamic tool metadata is intentionally malleable for newly registered tools,
    but some policy namespaces are reserved to a team or blocked from a role.
    Those denials remain authoritative even if a broad BaseTool role list would
    otherwise allow the tool.
    """
    if role == AgentRole.WORKER and _matches_any(tool_name, WORKER_BLOCKED_TOOLS):
        return True

    if tool_name == "approval.override_cso" and role != AgentRole.ORCHESTRATOR:
        return True
    if (
        tool_name == "review.submit_veto"
        and role == AgentRole.C_SUITE
        and sender_team != CSO_TEAM
    ):
        return True

    if role != AgentRole.C_SUITE and _matches_any(tool_name, CTO_EXTRA_TOOLS):
        return True
    if role == AgentRole.C_SUITE and sender_team != CTO_TEAM and _matches_any(
        tool_name, CTO_EXTRA_TOOLS
    ):
        return True

    devops_patterns = SUPPLEMENTAL_ADMIN_TOOLS or DEVOPS_PM_EXTRA_TOOLS
    if role != AgentRole.ADMIN and _matches_any(tool_name, devops_patterns):
        return True
    if role == AgentRole.ADMIN and sender_team != DEVOPS_TEAM and _matches_any(
        tool_name, devops_patterns
    ):
        return True

    return False


def can_use_tool_with_metadata(
    *,
    role: AgentRole,
    tool_name: str,
    sender_team: str | None = None,
    allowed_roles: Iterable[AgentRole | str | Any],
    blocked_roles: Iterable[AgentRole | str | Any] = (),
    policy: CommunicationPolicy | None = None,
) -> bool | str:
    """Authorize a tool via static policy, then dynamic metadata fallback."""
    policy_result = (policy or CommunicationPolicy()).can_use_tool(
        role,
        tool_name,
        sender_team=sender_team,
    )
    if policy_result is True:
        return True

    if has_authoritative_tool_denial(
        role=role,
        tool_name=tool_name,
        sender_team=sender_team,
    ):
        return policy_result

    if tool_metadata_allows_role(
        role=role,
        allowed_roles=allowed_roles,
        blocked_roles=blocked_roles,
    ):
        return True

    return policy_result
