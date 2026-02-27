"""
protocols — Canonical message schema, domain models and enumerations.

Exports (Phase 1)
-----------------
MessageEnvelope   Single canonical message format used by all agents, router, controller.
BlobRef           Reference to a large payload stored in MinIO.
MessageType       Full enum of all message types (TASK, REVIEW_REQUEST, …, ACK).
AgentRole         Six-role enum: orchestrator / executive / c_suite / admin / worker / sub_agent.
TaskBudget        Per-task budget caps embedded in MessageEnvelope.
ToolRequest       Agent → tool-service request.
ToolResponse      Tool-service → agent response.

Domain models (Phase 1, §3.4)
------------------------------
ProjectDocument, ReviewComment, ReviewSummary, FeasibilityReport,
HumanDecision, Sprint, Issue, KPISnapshot, AgentProfile
"""

# Populated in Phase 1.
