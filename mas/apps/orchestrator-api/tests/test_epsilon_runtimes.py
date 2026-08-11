"""Epsilon tests — advanced runtime evaluation endpoints."""

import anyio
import pytest


@pytest.mark.anyio
async def test_langgraph_runtime_status_reported(client):
    resp = await client.get("/runtimes")
    assert resp.status_code == 200
    data = resp.json()
    langgraph = next(r for r in data["runtimes"] if r["id"] == "langgraph")
    assert langgraph["tier"] == "departmental"
    assert "status" in langgraph
    assert langgraph["policy"]["inner_runtime"] is True
    assert langgraph["policy"]["sandbox_required"] == "gvisor"


@pytest.mark.anyio
async def test_crewai_runtime_status_reported(client):
    resp = await client.get("/runtimes")
    assert resp.status_code == 200
    data = resp.json()
    crewai = next(r for r in data["runtimes"] if r["id"] == "crewai")
    assert crewai["tier"] == "departmental"
    assert crewai["policy"]["requires_approval"] is True


@pytest.mark.anyio
async def test_autogen_requires_firecracker_sandbox(client):
    resp = await client.get("/runtimes")
    assert resp.status_code == 200
    data = resp.json()
    autogen = next(r for r in data["runtimes"] if r["id"] == "autogen")
    assert autogen["optional"] is True
    assert autogen["policy"]["sandbox_required"] == "firecracker"
    assert autogen["policy"]["max_instances"] == 1
    assert autogen["policy"]["inner_runtime"] is False


@pytest.mark.anyio
async def test_letta_is_read_only_by_default(client):
    resp = await client.get("/runtimes")
    assert resp.status_code == 200
    data = resp.json()
    letta = next(r for r in data["runtimes"] if r["id"] == "letta")
    assert letta["policy"]["read_only_by_default"] is True
    assert letta["policy"]["memory_audit"] is True
    assert letta["tier"] == "specialist"


@pytest.mark.anyio
async def test_vault_evaluation_returns_deferred_status(client):
    resp = await client.get("/evaluations/vault")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deferred"
    assert data["technology"] == "HashiCorp Vault"
    assert "effort_weeks" in data


@pytest.mark.anyio
async def test_zitadel_evaluation_returns_deferred_status(client):
    resp = await client.get("/evaluations/zitadel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deferred"
    assert data["technology"] == "ZITADEL"


@pytest.mark.anyio
async def test_temporal_evaluation_returns_deferred_status(client):
    resp = await client.get("/evaluations/temporal")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deferred"
    assert data["technology"] == "Temporal"


@pytest.mark.anyio
async def test_garage_evaluation_returns_deferred_status(client):
    resp = await client.get("/evaluations/garage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deferred"
    assert data["technology"] == "Garage"


@pytest.mark.anyio
async def test_firecracker_evaluation_returns_deferred_status(client):
    resp = await client.get("/evaluations/firecracker")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deferred"
    assert data["technology"] == "Firecracker"


@pytest.mark.anyio
async def test_runtime_validate_langgraph_passes_with_valid_config(client):
    resp = await client.post(
        "/runtimes/validate",
        json={
            "runtime_tier": "langgraph",
            "runtime_config": {
                "state_schema": {"messages": []},
                "checkpointer": "memory",
                "interrupt_before": [],
                "interrupt_after": [],
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_tier"] == "langgraph"
    assert data["dry_run"] is True
    assert data["mode"] == "validation_only"


@pytest.mark.anyio
async def test_runtime_validate_autogen_passes_with_valid_config(client):
    resp = await client.post(
        "/runtimes/validate",
        json={
            "runtime_tier": "autogen",
            "runtime_config": {
                "termination_strategy": {"type": "max_messages", "max": 20},
                "max_round": 20,
                "allowed_speakers": [],
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_tier"] == "autogen"


@pytest.mark.anyio
async def test_runtime_validate_letta_passes_with_valid_config(client):
    resp = await client.post(
        "/runtimes/validate",
        json={
            "runtime_tier": "letta",
            "runtime_config": {
                "persona": "You are a research assistant.",
                "embedding_model": "text-embedding-ada-002",
                "persistence_store": "postgres",
                "memory_block_types": ["human", "persona", "archival"],
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_tier"] == "letta"


@pytest.mark.anyio
async def test_runtime_validate_unknown_tier_returns_error(client):
    resp = await client.post(
        "/runtimes/validate",
        json={"runtime_tier": "unknown_runtime", "runtime_config": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is False
    assert "Unknown runtime tier" in data["blocked_reason"]


@pytest.mark.anyio
async def test_runtime_benchmark_skipped_when_validation_fails(client):
    resp = await client.post(
        "/runtimes/benchmark",
        json={"runtime_tier": "langgraph", "runtime_config": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "skipped"
    assert "Validation failed" in data["reason"]


@pytest.mark.anyio
async def test_runtime_benchmark_langgraph_with_valid_config(client):
    resp = await client.post(
        "/runtimes/benchmark",
        json={
            "runtime_tier": "langgraph",
            "runtime_config": {
                "state_schema": {"messages": []},
                "checkpointer": "memory",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_tier"] == "langgraph"
    assert data["status"] in {"package_unavailable", "dry_run_completed"}
    assert "benchmark_results" in data
    if data["status"] == "package_unavailable":
        assert "langgraph" in data["missing_packages"]
        assert data["benchmark_results"]["tasks_run"] == 0
    else:
        assert data["benchmark_results"]["tasks_run"] == 1
        assert data["benchmark_results"]["tasks_passed"] == 1


@pytest.mark.anyio
async def test_runtime_benchmark_timeout_is_bounded(client, monkeypatch):
    from orchestrator_api import main

    async def slow_probe(_runtime_tier, _runtime_config):
        await anyio.sleep(0.5)
        return {"tasks_run": 1, "tasks_passed": 1}

    monkeypatch.setenv("AIAT_RUNTIME_BENCHMARK_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(main, "_missing_runtime_packages", lambda _runtime_tier: [])
    monkeypatch.setattr(main, "_runtime_dry_run", slow_probe)
    response = await client.post(
        "/runtimes/benchmark",
        json={
            "runtime_tier": "crewai",
            "runtime_config": {
                "crew_config": {
                    "agents": [{"role": "runtime-smoke-agent"}],
                    "tasks": [{"description": "bounded runtime smoke task"}],
                },
                "process": "sequential",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "benchmark_timeout"
    assert data["benchmark_results"]["tasks_run"] == 0
    assert data["benchmark_results"]["timeout_seconds"] == 0.1


@pytest.mark.anyio
async def test_crewai_requires_approval_policy(client):
    resp = await client.get("/runtimes")
    assert resp.status_code == 200
    data = resp.json()
    crewai = next(r for r in data["runtimes"] if r["id"] == "crewai")
    assert crewai["policy"]["requires_approval"] is True
    assert crewai["policy"]["inner_runtime"] is True
