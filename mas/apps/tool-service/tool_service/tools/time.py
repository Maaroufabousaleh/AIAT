"""TIME group tools: ``time_now``.

Single source of truth for the dashboard's human-facing timezone lives here
in Python. It mirrors the helper at
``mas/apps/mas-dashboard/lib/datetime.ts`` and the
``TZ=America/New_York`` env var baked into the runtime Dockerfiles, so
when any of those move, this file moves with them.

The tool is intentionally trivial (no Redis, no async machinery beyond the
BaseTool contract, no caching) because its job is to give the LLM a fresh
clock reading whenever it needs to coordinate with another agent or with
the human operator.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

# IANA zone — auto-switches between EDT (summer, UTC-4) and EST
# (winter, UTC-5) with daylight saving. June = EDT.
DISPLAY_TZ: ZoneInfo = ZoneInfo("America/New_York")


class TimeNowTool(BaseTool):
    """Return the current time in the dashboard's canonical zone.

    Agents call this to ground any ``now``-relative statement. Without
    it, the LLM's clock is whatever was loaded into the system prompt at
    session start — which can be stale for long-running CEO / worker
    conversations.
    """

    name = "time_now"
    group = ToolGroup.KPI_UTILITY
    description = (
        "Return the current wall-clock time in America/New_York (EDT in "
        "summer, EST in winter; auto-switches with daylight saving). "
        "Call this whenever you need a fresh 'now' reading for a "
        "coordination message, a 'wait until X' decision, or a relative "
        "phrase like '5 minutes from now'."
    )
    allowed_roles: list[AgentRole] = list(AgentRole)
    cache_ttl_seconds: int = 0  # never cache — always return a fresh reading
    idempotent: bool = True
    max_concurrency: int = 0  # unlimited — pure clock read

    async def execute(self, **kwargs: Any) -> Any:
        now = datetime.now(DISPLAY_TZ)
        offset = now.strftime("%z")  # e.g. "-0400"
        offset_str = f"UTC{offset[:3]}:{offset[3:]}"
        return {
            "iso": now.isoformat(timespec="seconds"),
            "tz_name": "America/New_York",
            "tz_label": now.strftime("%Z"),  # "EDT" or "EST"
            "utc_offset": offset_str,
            "epoch_ms": int(now.timestamp() * 1000),
        }
