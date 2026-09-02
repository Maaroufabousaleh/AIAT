"""Tests for the company-timezone clock tool."""

from __future__ import annotations

import pytest


def test_company_timezone_invalid_value_falls_back_to_utc(monkeypatch):
    from tool_service.tools.time import _company_timezone

    monkeypatch.setenv("AIAT_COMPANY_TIMEZONE", "Not/AZone")

    name, zone = _company_timezone()

    assert name == "UTC"
    assert zone.key == "UTC"


@pytest.mark.anyio
async def test_time_now_uses_current_company_timezone(monkeypatch):
    from tool_service.tools.time import TimeNowTool

    monkeypatch.setenv("AIAT_COMPANY_TIMEZONE", "America/Toronto")

    result = await TimeNowTool().execute()

    assert result["tz_name"] == "America/Toronto"
    assert result["tz_label"]
    assert result["utc_offset"].startswith("UTC")
    assert result["iso"].endswith(("-04:00", "-05:00"))
