"""Shared HTTP helpers for communicating with the orchestrator-api.

All tool modules that need to call the orchestrator should import from here
instead of duplicating the client logic.
"""

from __future__ import annotations

import os

import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator-api:8000")


def _auth_headers() -> dict[str, str]:
    """Authenticate this internal service to the orchestrator control plane."""
    api_key = os.getenv("MAS_API_KEY") or os.getenv("GATEWAY_API_KEY")
    if not api_key:
        raise RuntimeError("MAS_API_KEY must be configured for orchestrator requests")
    return {"X-API-Key": api_key}


async def orch_get(path: str, params: dict | None = None) -> dict | list:
    """GET request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.get(path, params=params, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


async def orch_post(path: str, body: dict | None = None) -> dict:
    """POST request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.post(path, json=body or {}, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


async def orch_patch(path: str, body: dict | None = None) -> dict:
    """PATCH request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.patch(path, json=body or {}, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


async def orch_delete(path: str) -> dict:
    """DELETE request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.delete(path, headers=_auth_headers())
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()
