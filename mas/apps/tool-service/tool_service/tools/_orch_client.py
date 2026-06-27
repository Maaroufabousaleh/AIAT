"""Shared HTTP helpers for communicating with the orchestrator-api.

All tool modules that need to call the orchestrator should import from here
instead of duplicating the client logic.
"""

from __future__ import annotations

import os

import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator-api:8000")


async def orch_get(path: str, params: dict | None = None) -> dict | list:
    """GET request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


async def orch_post(path: str, body: dict | None = None) -> dict:
    """POST request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.post(path, json=body or {})
        resp.raise_for_status()
        return resp.json()


async def orch_delete(path: str) -> dict:
    """DELETE request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.delete(path)
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()
