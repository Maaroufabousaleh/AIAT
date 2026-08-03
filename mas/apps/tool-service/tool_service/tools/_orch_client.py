"""Shared HTTP helpers for communicating with the orchestrator-api.

All tool modules that need to call the orchestrator should import from here
instead of duplicating the client logic.
"""

from __future__ import annotations

import os

import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator-api:8000")


def _auth_headers(
    context: dict | None = None,
    *,
    principal: str = "service",
) -> dict[str, str]:
    """Authenticate to the orchestrator with the least privileged principal.

    Actor headers carry signed tool-call attribution, but the orchestrator
    deliberately never treats them as authorization.  Canonical PM/SCM writes
    therefore opt into the separately configured operator credential while
    ordinary service calls continue to use the service credential.
    """
    if principal == "operator":
        api_key = os.getenv("AIAT_OPERATOR_API_KEY")
        if not api_key:
            raise RuntimeError(
                "AIAT_OPERATOR_API_KEY must be configured for governed orchestrator mutations"
            )
    elif principal == "service":
        api_key = os.getenv("MAS_API_KEY") or os.getenv("GATEWAY_API_KEY")
        if not api_key:
            raise RuntimeError("MAS_API_KEY must be configured for orchestrator requests")
    else:
        raise ValueError(f"unsupported orchestrator principal: {principal}")
    headers = {"X-API-Key": api_key}
    if context:
        role = str(context.get("caller_role") or "").strip()
        actor = str(context.get("caller_id") or "").strip()
        if role:
            headers["X-AIAT-Actor-Role"] = role
        if actor:
            headers["X-AIAT-Actor-ID"] = actor
    return headers


async def orch_get(path: str, params: dict | None = None, *, context: dict | None = None) -> dict | list:
    """GET request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.get(path, params=params, headers=_auth_headers(context))
        resp.raise_for_status()
        return resp.json()


async def orch_post(
    path: str,
    body: dict | None = None,
    *,
    context: dict | None = None,
    principal: str = "service",
) -> dict:
    """POST request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.post(
            path,
            json=body or {},
            headers=_auth_headers(context, principal=principal),
        )
        resp.raise_for_status()
        return resp.json()


async def orch_patch(
    path: str,
    body: dict | None = None,
    *,
    context: dict | None = None,
    principal: str = "service",
) -> dict:
    """PATCH request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.patch(
            path,
            json=body or {},
            headers=_auth_headers(context, principal=principal),
        )
        resp.raise_for_status()
        return resp.json()


async def orch_delete(path: str, *, context: dict | None = None) -> dict:
    """DELETE request to orchestrator-api."""
    async with httpx.AsyncClient(timeout=15, base_url=ORCHESTRATOR_URL) as client:
        resp = await client.delete(path, headers=_auth_headers(context))
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()
