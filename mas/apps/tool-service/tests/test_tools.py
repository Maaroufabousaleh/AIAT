"""Smoke tests for tool-service."""
import pytest


@pytest.mark.anyio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "tools_registered" in data
    assert data["tools_registered"] > 0


@pytest.mark.anyio
async def test_tools_endpoint_returns_list(client):
    response = await client.get("/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert data["count"] > 0


@pytest.mark.anyio
async def test_capability_tools_read_orchestrator_registry(monkeypatch):
    import tool_service.tools.capability as capability_mod
    from tool_service.tools.capability import CapabilityListWorkersTool, CapabilitySearchTool

    async def fake_get(path, params=None):
        assert path == "/capabilities/workers"
        assert params == {"team_id": "office_cto"}
        return [{"name": "new-agent", "team_id": "office_cto", "capability_names": ["test.run"]}]

    async def fake_post(path, body=None):
        assert path == "/capabilities/search"
        assert body == {"name": "test.run"}
        return [{"name": "new-agent", "team_id": "office_cto", "capability_names": ["test.run"]}]

    monkeypatch.setattr(capability_mod, "orch_get", fake_get)
    monkeypatch.setattr(capability_mod, "orch_post", fake_post)

    listed = await CapabilityListWorkersTool().execute(team_id="office_cto")
    searched = await CapabilitySearchTool().execute(name="test.run")

    assert listed["count"] == 1
    assert searched["workers"][0]["name"] == "new-agent"


@pytest.mark.anyio
async def test_capability_register_sends_role_to_orchestrator(monkeypatch):
    import tool_service.tools.capability as capability_mod
    from tool_service.tools.capability import CapabilityRegisterTool

    calls = []

    async def fake_post(path, body=None):
        calls.append((path, body))
        return {"id": "worker-uuid", "name": body["name"], "capability_names": body["capability_names"]}

    monkeypatch.setattr(capability_mod, "orch_post", fake_post)

    result = await CapabilityRegisterTool().execute(
        worker_id="logical-worker",
        role="worker",
        capabilities=["test.run"],
    )

    assert result["registered"] is True
    assert calls[0][0] == "/capabilities/workers"
    assert calls[0][1]["role"] == "worker"
    assert calls[0][1]["adapter_config"]["worker_id"] == "logical-worker"


@pytest.mark.anyio
async def test_capability_deregister_deletes_orchestrator_worker(monkeypatch):
    import tool_service.tools.capability as capability_mod
    from tool_service.tools.capability import CapabilityDeregisterTool

    calls = []

    async def fake_delete(path):
        calls.append(path)
        return {"status": "deregistered"}

    monkeypatch.setattr(capability_mod, "orch_delete", fake_delete)

    result = await CapabilityDeregisterTool().execute(
        worker_id="4fb2c3e5-5a7c-43f0-a805-78a498989b2b"
    )

    assert calls == ["/capabilities/workers/4fb2c3e5-5a7c-43f0-a805-78a498989b2b"]
    assert result["deregistered"] is True
    assert result["result"]["status"] == "deregistered"


@pytest.mark.anyio
async def test_capability_deregister_resolves_logical_worker_id(monkeypatch):
    import tool_service.tools.capability as capability_mod
    from tool_service.tools.capability import CapabilityDeregisterTool

    calls = []

    async def fake_get(path, params=None):
        assert path == "/capabilities/workers"
        return [
            {
                "id": "4fb2c3e5-5a7c-43f0-a805-78a498989b2b",
                "name": "logical-worker",
                "adapter_config": {"worker_id": "logical-worker"},
            }
        ]

    async def fake_delete(path):
        calls.append(path)
        return {"status": "deregistered"}

    monkeypatch.setattr(capability_mod, "orch_get", fake_get)
    monkeypatch.setattr(capability_mod, "orch_delete", fake_delete)

    result = await CapabilityDeregisterTool().execute(worker_id="logical-worker")

    assert calls == ["/capabilities/workers/4fb2c3e5-5a7c-43f0-a805-78a498989b2b"]
    assert result["deregistered"] is True
    assert result["orchestrator_worker_id"] == "4fb2c3e5-5a7c-43f0-a805-78a498989b2b"

