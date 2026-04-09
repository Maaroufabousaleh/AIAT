"""Canonical tool group definitions for the MAS architecture plan.

Each tool belongs to exactly one canonical group. Groups determine:
- Rate-limit bucket (calls/min)
- Circuit-breaker scope (per tool, grouped for rate limits)
- Monitoring labels
"""

from __future__ import annotations

from enum import Enum


class ToolGroup(str, Enum):
    """The seven canonical tool groups defined in Phase 6."""

    WORKFLOW = "workflow"
    DOCUMENT = "document"
    REVIEW = "review"
    SPRINT_ISSUE = "sprint_issue"
    DEVOPS = "devops"
    CAPABILITY = "capability"
    KPI_UTILITY = "kpi_utility"


# Default rate limits (calls per minute) per group.
GROUP_RATE_LIMITS: dict[ToolGroup, int] = {
    ToolGroup.WORKFLOW: 50,
    ToolGroup.DOCUMENT: 30,
    ToolGroup.REVIEW: 30,
    ToolGroup.SPRINT_ISSUE: 20,
    ToolGroup.DEVOPS: 10,
    ToolGroup.CAPABILITY: 30,
    ToolGroup.KPI_UTILITY: 50,
}
