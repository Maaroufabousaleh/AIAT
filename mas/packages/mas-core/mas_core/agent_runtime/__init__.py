"""
agent_runtime — Base classes for all agent types.

Exports (Phase 4 + 8)
---------------------
AgentBase       Abstract base: WS connection to router, think() loop,
                consume-side idempotency (LRU), checkpoint save/restore,
                budget tracking, structured logging.
WorkerAgent     Executes tasks; fan-out to sub-agents.
AdminAgent      Department PM; manages workers within a team.
SubAgent        Lightweight; parent-scoped.
ExecutiveAgent  COO: extends AdminAgent with cross-department routing,
                review fan-out/fan-in, revision loops.
CSuiteAgent     CFO/CIO/CHRM/CSO/CTO: review handling, CSO veto,
                CTO DevOps coordination.
AgentConfig     Pydantic settings per agent (id, role, team_id, budget_defaults, …).
"""

from .base import AgentBase
from .budget import BudgetExhausted, BudgetTracker
from .config import AgentConfig
from .router_client import RouterClient, RouterDuplicateMessage, RouterError

__all__ = [
    "AgentBase",
    "AgentConfig",
    "BudgetExhausted",
    "BudgetTracker",
    "RouterClient",
    "RouterDuplicateMessage",
    "RouterError",
]

# WorkerAgent, AdminAgent, SubAgent, ExecutiveAgent, CSuiteAgent — populated in Phase 8.
