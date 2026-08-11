"""Regression coverage for the control-plane authentication boundary."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_non_health_endpoints_require_an_api_key() -> None:
    from orchestrator_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/metrics")).status_code == 401
        assert (await client.get("/metrics", headers={"X-API-Key": "test-mas-key"})).status_code == 200


@pytest.mark.anyio
async def test_dashboard_section_acl_separates_human_ceo_service_and_worker(
    client, monkeypatch
) -> None:
    """Dashboard section context is enforced by authenticated principal, not role headers."""

    monkeypatch.setenv("AIAT_CEO_API_KEY", "test-ceo-key")
    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")

    human = {"X-API-Key": "test-operator-key", "X-AIAT-Dashboard-Section": "credentials"}
    ceo = {"X-API-Key": "test-ceo-key", "X-AIAT-Dashboard-Section": "ceo"}
    ceo_credentials = {"X-API-Key": "test-ceo-key", "X-AIAT-Dashboard-Section": "credentials"}
    service_credentials = {"X-API-Key": "test-mas-key", "X-AIAT-Dashboard-Section": "credentials"}
    worker_projects = {"X-API-Key": "test-worker-key", "X-AIAT-Dashboard-Section": "projects"}
    worker_credentials = {"X-API-Key": "test-worker-key", "X-AIAT-Dashboard-Section": "credentials"}

    assert (await client.get("/dashboard/sections/credentials", headers=human)).status_code == 200
    assert (await client.get("/dashboard/sections/ceo", headers=ceo)).status_code == 200
    assert (await client.get("/dashboard/sections/credentials", headers=ceo_credentials)).status_code == 403
    assert (await client.get("/dashboard/sections/credentials", headers=service_credentials)).status_code == 403
    assert (await client.get("/dashboard/sections/projects", headers=worker_projects)).status_code == 200
    assert (await client.get("/dashboard/sections/credentials", headers=worker_credentials)).status_code == 403


@pytest.mark.anyio
async def test_dashboard_section_acl_is_persisted_by_operator_only(client, monkeypatch) -> None:
    """ACL edits are durable system config and cannot be made by automation principals."""

    from orchestrator_api import main
    from mas_core.policy.dashboard_access import DASHBOARD_SECTION_ACL_CONFIG_KEY

    monkeypatch.setenv("AIAT_CEO_API_KEY", "test-ceo-key")
    saved: dict[str, str] = {}

    class Storage:
        async def set_config(self, key: str, value: str) -> None:
            saved[key] = value

    previous_storage = main.app.state.storage
    previous_acl = main.app.state.dashboard_acl
    main.app.state.storage = Storage()
    try:
        denied = await client.put(
            "/dashboard/sections/credentials/acl",
            headers={"X-API-Key": "test-ceo-key"},
            json={"principals": ["operator", "ceo"]},
        )
        assert denied.status_code == 403

        updated = await client.put(
            "/dashboard/sections/credentials/acl",
            headers={"X-API-Key": "test-operator-key"},
            json={"principals": ["operator", "ceo"]},
        )
        assert updated.status_code == 200
        assert updated.json()["persisted"] is True
        assert DASHBOARD_SECTION_ACL_CONFIG_KEY in saved
        allowed = await client.get(
            "/dashboard/sections/credentials",
            headers={"X-API-Key": "test-ceo-key"},
        )
        assert allowed.status_code == 200
    finally:
        main.app.state.storage = previous_storage
        main.app.state.dashboard_acl = previous_acl
