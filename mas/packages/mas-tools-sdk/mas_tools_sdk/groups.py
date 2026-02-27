"""Tool group definitions — the 6 tool groups from the MAS architecture plan.

Each tool belongs to exactly one group. Groups determine:
- Rate-limit bucket (calls/min)
- Circuit-breaker scope (per tool, but grouped for rate limits)
- Monitoring labels
"""

from __future__ import annotations

from enum import Enum


class ToolGroup(str, Enum):
    """The six tool groups defined in the tool-service manifest (Phase 6)."""

    WEB = "web"                 # web_search, web_fetch
    FILE = "file"               # file_read, file_write
    MEMORY = "memory"           # shared_memory_read, shared_memory_write
    PROJECT = "project"         # project.*, document.*, review.*, approval.*, human.*
    SPRINT_KPI = "sprint_kpi"   # sprint.*, issue.*, kpi.*, velocity.*, estimation.*
    INFRA = "infra"             # infra.*, cicd.*, monitoring.*, secrets.*, blob.*


# Default rate limits (calls per minute) per group.
GROUP_RATE_LIMITS: dict[ToolGroup, int] = {
    ToolGroup.WEB: 30,
    ToolGroup.FILE: 50,
    ToolGroup.MEMORY: 50,
    ToolGroup.PROJECT: 30,
    ToolGroup.SPRINT_KPI: 20,
    ToolGroup.INFRA: 10,
}
