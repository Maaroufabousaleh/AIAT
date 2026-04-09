"""
policy — Role-based communication and tool-access policy engine.

Exports
-------
CommunicationPolicy   Stateless rules engine.
                      can(sender_role, sender_team, recipient_id, recipient_team, msg_type)
                        → True | deny_reason_str
                      can_use_tool(sender_role, tool_name, *, sender_team=None)
                        → True | deny_reason_str

POLICY_RULES          Declarative config dict (mirrors org-architecture §4.2).

Team constants        ORCHESTRATOR_TEAM, EXECUTIVE_TEAM, C_SUITE_TEAMS,
                      CTO_TEAM, DEPT_TEAMS, DEVOPS_TEAM, TEAM_TIERS

Six roles (extended corporate hierarchy):
  orchestrator  — CEO; interfaces with Human; unrestricted routing + tools.
  executive     — COO; cross-department + C-Suite routing.
  c_suite       — CFO, CIO, CHRM, CSO, CTO; peer messaging only during reviews.
  admin         — Department PMs; own-team routing + upward reporting.
  worker        — Execution agents; own-team only; restricted tools.
  sub_agent     — Spawned sub-tasks; parent-team only.
"""

from .engine import CommunicationPolicy
from .rules import (
    ADMIN_BASE_TOOLS,
    ADMIN_MSG_TYPES,
    C_SUITE_BASE_TOOLS,
    C_SUITE_CROSS_TEAM_TYPES,
    C_SUITE_MSG_TYPES,
    C_SUITE_TEAMS,
    CTO_EXTRA_TOOLS,
    CTO_TEAM,
    DEPT_TEAMS,
    DEVOPS_PM_EXTRA_TOOLS,
    DEVOPS_TEAM,
    EXECUTIVE_MSG_TYPES,
    EXECUTIVE_TEAM,
    EXECUTIVE_TOOLS,
    ORCHESTRATOR_TEAM,
    ORCHESTRATOR_TOOLS,
    POLICY_RULES,
    SUB_AGENT_MSG_TYPES,
    SUB_AGENT_TOOLS,
    TEAM_TIERS,
    WORKER_BLOCKED_TOOLS,
    WORKER_MSG_TYPES,
    WORKER_TOOLS,
)

__all__ = [
    # Engine
    "CommunicationPolicy",
    # Rules / config
    "POLICY_RULES",
    "TEAM_TIERS",
    # Team constants
    "ORCHESTRATOR_TEAM",
    "EXECUTIVE_TEAM",
    "C_SUITE_TEAMS",
    "CTO_TEAM",
    "DEPT_TEAMS",
    "DEVOPS_TEAM",
    # Message-type sets
    "EXECUTIVE_MSG_TYPES",
    "C_SUITE_MSG_TYPES",
    "C_SUITE_CROSS_TEAM_TYPES",
    "ADMIN_MSG_TYPES",
    "WORKER_MSG_TYPES",
    "SUB_AGENT_MSG_TYPES",
    # Tool pattern tuples
    "ORCHESTRATOR_TOOLS",
    "EXECUTIVE_TOOLS",
    "C_SUITE_BASE_TOOLS",
    "CTO_EXTRA_TOOLS",
    "ADMIN_BASE_TOOLS",
    "DEVOPS_PM_EXTRA_TOOLS",
    "WORKER_TOOLS",
    "WORKER_BLOCKED_TOOLS",
    "SUB_AGENT_TOOLS",
]
