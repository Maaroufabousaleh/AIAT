"""TIME group tools: ``time_now``.

The company manifest's timezone is passed through ``AIAT_COMPANY_TIMEZONE``.
The tool mirrors the dashboard helper at
``mas/apps/mas-dashboard/lib/datetime.ts``; internal timestamps remain UTC.

The tool is intentionally trivial (no Redis, no async machinery beyond the
BaseTool contract, no caching) because its job is to give the LLM a fresh
clock reading whenever it needs to coordinate with another agent or with
the human operator.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup


def _company_timezone() -> tuple[str, ZoneInfo]:
    """Resolve the company manifest timezone without trusting a bad env value."""
    requested = os.environ.get("AIAT_COMPANY_TIMEZONE") or os.environ.get("DISPLAY_TZ") or "UTC"
    try:
        return requested, ZoneInfo(requested)
    except (KeyError, ValueError):
        return "UTC", ZoneInfo("UTC")


DISPLAY_TZ_NAME, DISPLAY_TZ = _company_timezone()


class TimeNowTool(BaseTool):
    """Return the current time in the company's configured zone.

    Agents call this to ground any ``now``-relative statement. Without
    it, the LLM's clock is whatever was loaded into the system prompt at
    session start — which can be stale for long-running CEO / worker
    conversations.
    """

    name = "time_now"
    group = ToolGroup.KPI_UTILITY
    description = (
        "Return the current wall-clock time in the configured company timezone. "
        "Call this whenever you need a fresh 'now' reading for a "
        "coordination message, a 'wait until X' decision, or a relative "
        "phrase like '5 minutes from now'."
    )
    allowed_roles: list[AgentRole] = list(AgentRole)
    cache_ttl_seconds: int = 0  # never cache — always return a fresh reading
    idempotent: bool = True
    max_concurrency: int = 0  # unlimited — pure clock read

    async def execute(self, **kwargs: Any) -> Any:
        # Resolve at call time so a long-running tool service follows an
        # updated deployment/company policy without requiring a process restart.
        tz_name, display_tz = _company_timezone()
        now = datetime.now(display_tz)
        offset = now.strftime("%z")  # e.g. "-0400"
        offset_str = f"UTC{offset[:3]}:{offset[3:]}"
        return {
            "iso": now.isoformat(timespec="seconds"),
            "tz_name": tz_name,
            "tz_label": now.strftime("%Z"),  # "EDT" or "EST"
            "utc_offset": offset_str,
            "epoch_ms": int(now.timestamp() * 1000),
        }
