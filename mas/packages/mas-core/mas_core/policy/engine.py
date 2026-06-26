"""
CommunicationPolicy — stateless rules engine for the MAS.

Public API
----------
policy = CommunicationPolicy()

# Routing check
result = policy.can(sender_role, sender_team, recipient_id, recipient_team, msg_type)
# → True if allowed, str deny-reason if denied.

# Tool access check
result = policy.can_use_tool(sender_role, tool_name, sender_team=sender_team)
# → True if allowed, str deny-reason if denied.

Both methods are **pure** (no I/O, no state mutation), safe to call from any async
context without await.

Plan reference: org-architecture §4.1–§4.3 + mas-architecture-upgrade Phase 2 §8–§9.
"""

from __future__ import annotations

import fnmatch
from typing import Union

from mas_core.protocols.enums import AgentRole, MessageType

from .rules import (
    ADMIN_BASE_TOOLS,
    ADMIN_MSG_TYPES,
    C_SUITE_BASE_TOOLS,
    C_SUITE_CROSS_TEAM_TYPES,
    C_SUITE_MSG_TYPES,
    CTO_EXTRA_TOOLS,
    CTO_TEAM,
    DEVOPS_TEAM,
    EXECUTIVE_MSG_TYPES,
    EXECUTIVE_TEAM,
    EXECUTIVE_TOOLS,
    SUB_AGENT_MSG_TYPES,
    SUB_AGENT_TOOLS,
    SUPPLEMENTAL_ADMIN_TOOLS,
    TEAM_TIERS,
    WORKER_BLOCKED_TOOLS,
    WORKER_MSG_TYPES,
    WORKER_TOOLS,
)

# Return type alias: True = allowed; str = human-readable deny reason.
PolicyResult = Union[bool, str]


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    """Return True if *name* matches at least one fnmatch *pattern*."""
    return any(fnmatch.fnmatch(name, p) for p in patterns)


class CommunicationPolicy:
    """Stateless rules engine that enforces the MAS corporate hierarchy.

    Instantiate once at startup; share across threads/tasks freely — the
    object carries no mutable state.

    Chain-of-command summary
    ------------------------
    orchestrator (CEO)  → anywhere, any type
    executive    (COO)  → CEO + any C-Suite + any dept PM
    c_suite             → CEO, COO, peer C-Suite (review types only cross-team),
                          own-team workers; CTO may also address dept PMs via
                          SPRINT_PLAN / DIRECTIVE
    admin        (PM)   → COO, CTO + own-team workers
                          with INFRA_READY; with ESCALATION may also reach CEO
    worker              → own team only; with ESCALATION may reach COO directly
    sub_agent           → own team only (parent-team constraint)
    """

    # ------------------------------------------------------------------
    # Routing check
    # ------------------------------------------------------------------

    def can(
        self,
        sender_role: AgentRole,
        sender_team: str,
        recipient_id: str | None,
        recipient_team: str | None,
        msg_type: MessageType,
    ) -> PolicyResult:
        """Return True if the message is allowed, or a deny-reason string.

        Parameters
        ----------
        sender_role:    Role of the sending agent.
        sender_team:    Team ID the sender belongs to (e.g. "exec_ceo").
        recipient_id:   Optional specific agent_id of the intended recipient.
                        Unused for routing decisions currently (reserved for
                        future fine-grained ACL); pass None when not known.
        recipient_team: Team ID of the destination stream.
                        If None the message is treated as intra-team/broadcast.
        msg_type:       MessageType of the proposed message.
        """
        # ── 0. Orchestrator (CEO) is fully unrestricted ─────────────────────
        if sender_role == AgentRole.ORCHESTRATOR:
            return True

        # ── 1. Resolve effective recipient tier ─────────────────────────────
        # None recipient_team → intra-team (always allowed with correct type)
        is_own_team = (recipient_team is None) or (recipient_team == sender_team)
        recipient_tier: AgentRole | None = (
            None
            if recipient_team is None
            else TEAM_TIERS.get(recipient_team)
        )

        # ── 2. System-only types ─────────────────────────────────────────────
        if msg_type in (MessageType.BROADCAST, MessageType.SHUTDOWN):
            if sender_role == AgentRole.EXECUTIVE:
                return True
            return (
                f"Only orchestrator/executive may send {msg_type.value}; "
                f"sender is {sender_role.value}"
            )

        if msg_type in (MessageType.HEARTBEAT, MessageType.SYSTEM_EVENT):
            if sender_role in (AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE):
                return True
            return (
                f"Only orchestrator/executive may send {msg_type.value}; "
                f"sender is {sender_role.value}"
            )

        # SHUTDOWN_ACK: any role may send to orchestrator (confirming shutdown)
        if msg_type == MessageType.SHUTDOWN_ACK:
            if recipient_tier == AgentRole.ORCHESTRATOR or is_own_team:
                return True
            return (
                f"SHUTDOWN_ACK may only be sent to the orchestrator; "
                f"got recipient_team={recipient_team!r}"
            )

        # ── 3. Role-specific checks ──────────────────────────────────────────
        if sender_role == AgentRole.EXECUTIVE:
            return self._executive_can(
                sender_team, recipient_team, recipient_tier, is_own_team, msg_type
            )

        if sender_role == AgentRole.C_SUITE:
            return self._c_suite_can(
                sender_team, recipient_team, recipient_tier, is_own_team, msg_type
            )

        if sender_role == AgentRole.ADMIN:
            return self._admin_can(
                sender_team, recipient_team, recipient_tier, is_own_team, msg_type
            )

        if sender_role == AgentRole.WORKER:
            return self._worker_can(
                sender_team, recipient_team, is_own_team, msg_type
            )

        if sender_role == AgentRole.SUB_AGENT:
            return self._sub_agent_can(is_own_team, msg_type)

        return f"Unknown sender role: {sender_role!r}"

    # ------------------------------------------------------------------
    # Tool access check
    # ------------------------------------------------------------------

    def can_use_tool(
        self,
        sender_role: AgentRole,
        tool_name: str,
        *,
        sender_team: str | None = None,
    ) -> PolicyResult:
        """Return True if the role/team combination may invoke *tool_name*.

        Parameters
        ----------
        sender_role:  Role of the requesting agent.
        tool_name:    Fully-qualified tool name (e.g. "project.transition").
        sender_team:  Optional team ID — enables CTO extra tools.
        """
        if sender_role == AgentRole.ORCHESTRATOR:
            return True  # CEO has full tool access

        if sender_role == AgentRole.EXECUTIVE:
            if _matches_any(tool_name, EXECUTIVE_TOOLS):
                return True
            return (
                f"executive role does not have access to tool '{tool_name}'"
            )

        if sender_role == AgentRole.C_SUITE:
            allowed = C_SUITE_BASE_TOOLS
            if sender_team == CTO_TEAM:
                allowed = C_SUITE_BASE_TOOLS + CTO_EXTRA_TOOLS
            if _matches_any(tool_name, allowed):
                return True
            return (
                f"c_suite role (team={sender_team!r}) does not have access "
                f"to tool '{tool_name}'"
            )

        if sender_role == AgentRole.ADMIN:
            allowed = ADMIN_BASE_TOOLS
            if sender_team == DEVOPS_TEAM:
                allowed = ADMIN_BASE_TOOLS + SUPPLEMENTAL_ADMIN_TOOLS
            if _matches_any(tool_name, allowed):
                return True
            return (
                f"admin role (team={sender_team!r}) does not have access "
                f"to tool '{tool_name}'"
            )

        if sender_role == AgentRole.WORKER:
            # Hard-blocked first
            if _matches_any(tool_name, WORKER_BLOCKED_TOOLS):
                return (
                    f"tool '{tool_name}' is blocked for worker role"
                )
            if _matches_any(tool_name, WORKER_TOOLS):
                return True
            return f"worker role does not have access to tool '{tool_name}'"

        if sender_role == AgentRole.SUB_AGENT:
            if _matches_any(tool_name, SUB_AGENT_TOOLS):
                return True
            return f"sub_agent role does not have access to tool '{tool_name}'"

        return f"Unknown sender role: {sender_role!r}"

    # ------------------------------------------------------------------
    # Private per-role routing helpers
    # ------------------------------------------------------------------

    def _executive_can(
        self,
        sender_team: str,
        recipient_team: str | None,
        recipient_tier: AgentRole | None,
        is_own_team: bool,
        msg_type: MessageType,
    ) -> PolicyResult:
        # Check message type first
        if msg_type not in EXECUTIVE_MSG_TYPES:
            return (
                f"executive role may not send message type '{msg_type.value}'"
            )
        # Own-team or targeting orchestrator / c_suite / admin → allowed
        if is_own_team:
            return True
        if recipient_tier in (
            AgentRole.ORCHESTRATOR,
            AgentRole.C_SUITE,
            AgentRole.ADMIN,
        ):
            return True
        return (
            f"executive may only address orchestrator, c_suite, or admin teams; "
            f"recipient_team={recipient_team!r} has tier {recipient_tier!r}"
        )

    def _c_suite_can(
        self,
        sender_team: str,
        recipient_team: str | None,
        recipient_tier: AgentRole | None,
        is_own_team: bool,
        msg_type: MessageType,
    ) -> PolicyResult:
        if msg_type not in C_SUITE_MSG_TYPES:
            return f"c_suite role may not send message type '{msg_type.value}'"

        if is_own_team:
            return True

        # Allowed cross-team targets:
        # → orchestrator (CEO) or executive (COO) — any allowed type
        if recipient_tier in (AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE):
            return True

        # → peer c_suite — only cross-team types
        if recipient_tier == AgentRole.C_SUITE:
            if msg_type in C_SUITE_CROSS_TEAM_TYPES:
                return True
            return (
                f"c_suite may only send cross-team to peers using "
                f"{[t.value for t in C_SUITE_CROSS_TEAM_TYPES]}; "
                f"got '{msg_type.value}'"
            )

        # → admin (dept PMs) — only cross-team types (e.g. SPRINT_PLAN from CTO)
        if recipient_tier == AgentRole.ADMIN:
            if msg_type in C_SUITE_CROSS_TEAM_TYPES:
                return True
            return (
                f"c_suite may only address admin teams with cross-team message "
                f"types; got '{msg_type.value}'"
            )

        return (
            f"c_suite may not send to recipient_team={recipient_team!r} "
            f"(tier={recipient_tier!r})"
        )

    def _admin_can(
        self,
        sender_team: str,
        recipient_team: str | None,
        recipient_tier: AgentRole | None,
        is_own_team: bool,
        msg_type: MessageType,
    ) -> PolicyResult:
        if msg_type not in ADMIN_MSG_TYPES:
            return f"admin role may not send message type '{msg_type.value}'"

        if is_own_team:
            return True

        # → executive (COO) — always allowed for admin
        if recipient_tier == AgentRole.EXECUTIVE:
            return True

        # → CTO specifically — allowed (sprint reports, INFRA_READY)
        if recipient_team == CTO_TEAM:
            return True

        # ESCALATION exception: admin can reach CEO directly (skip COO)
        if msg_type == MessageType.ESCALATION and recipient_tier == AgentRole.ORCHESTRATOR:
            return True

        return (
            f"admin may only address executive (COO), CTO, or own team; "
            f"got recipient_team={recipient_team!r} (tier={recipient_tier!r})"
        )

    def _worker_can(
        self,
        sender_team: str,
        recipient_team: str | None,
        is_own_team: bool,
        msg_type: MessageType,
    ) -> PolicyResult:
        if msg_type not in WORKER_MSG_TYPES:
            return f"worker role may not send message type '{msg_type.value}'"

        if is_own_team:
            return True

        # ESCALATION exception: worker can skip PM and reach COO directly
        if msg_type == MessageType.ESCALATION and recipient_team == EXECUTIVE_TEAM:
            return True

        return (
            f"worker may only send within own team ({sender_team!r}); "
            f"got recipient_team={recipient_team!r}"
        )

    def _sub_agent_can(
        self,
        is_own_team: bool,
        msg_type: MessageType,
    ) -> PolicyResult:
        if msg_type not in SUB_AGENT_MSG_TYPES:
            return f"sub_agent role may not send message type '{msg_type.value}'"

        if is_own_team:
            return True

        return "sub_agent may only communicate within its parent team"
