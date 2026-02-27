"""
policy — Role-based communication and tool-access policy engine.

Exports (Phase 2)
-----------------
CommunicationPolicy   Stateless rules engine.
                      can(sender_role, sender_team, recipient_id, recipient_team, msg_type)
                        → bool | deny_reason
                      can_use_tool(sender_role, tool_name) → bool | deny_reason

POLICY_RULES          Declarative config dict (loaded from YAML or inline Python).

Six roles (extended corporate hierarchy):
  orchestrator  — CEO; interfaces with Human; unrestricted routing.
  executive     — COO; cross-department + C-Suite routing.
  c_suite       — CFO, CIO, CHRM, CSO, CTO; peer messaging only during reviews.
  admin         — Department PMs; own-team routing only.
  worker        — Execution agents; own-team routing only.
  sub_agent     — Spawned sub-tasks; parent-only routing.
"""

# Populated in Phase 2.
