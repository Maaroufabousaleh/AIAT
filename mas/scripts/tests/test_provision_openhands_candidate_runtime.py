"""Deterministic tests for run-scoped OpenHands object materialization."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "provision_openhands_candidate_runtime.py"
SPEC = importlib.util.spec_from_file_location("provision_openhands_candidate_runtime", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_run_scoped_objects_are_created_and_only_server_profile_uuid_is_retained() -> None:
    profile_id = "5e8f2b8a-9d9c-4a7f-9c82-14d8ccf9dd31"
    calls: list[tuple[str, str]] = []
    mcp_present = False
    mcp_key = MODULE.EXPECTED_MCP_KEY

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mcp_present
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/api/llm/provider-connections":
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path == "/api/llm/provider-connections":
            return httpx.Response(201, json={"id": "gateway-connection", "api_key_set": True})
        if request.method == "POST" and request.url.path == "/api/profiles/aiat-openhands-omniroute-coding":
            return httpx.Response(201, json={"name": "aiat-openhands-omniroute-coding", "message": "saved"})
        if request.method == "GET" and request.url.path == "/api/profiles/aiat-openhands-omniroute-coding":
            return httpx.Response(200, json={"name": "aiat-openhands-omniroute-coding", "llm": {"model": "omniroute-coding", "provider_connection_id": "gateway-connection"}})
        if request.method == "POST" and request.url.path == f"/api/settings/mcp/{mcp_key}":
            mcp_present = True
            return httpx.Response(201, json={})
        if request.method == "DELETE" and request.url.path == f"/api/settings/mcp/{mcp_key}":
            mcp_present = False
            # Agent Server may use an empty successful response for an
            # idempotent delete; provisioning must accept that contract.
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/api/settings":
            config = {
                mcp_key: {
                    "url": MODULE.BRIDGE_URL,
                    "transport": "streamable-http",
                    "enabled": True,
                    "headers": {"X-AIAT-OpenHands-Grant": "REDACTED"},
                }
            } if mcp_present else {}
            return httpx.Response(
                200,
                json={"mcp_config": config},
            )
        if request.method == "POST" and request.url.path == "/api/agent-profiles/aiat-openhands-v1-43-0-coding":
            return httpx.Response(201, json={"name": "aiat-openhands-v1-43-0-coding", "message": "saved"})
        if request.method == "GET" and request.url.path == "/api/agent-profiles/aiat-openhands-v1-43-0-coding":
            return httpx.Response(
                200,
                json={
                    "name": "aiat-openhands-v1-43-0-coding",
                    "profile": {
                        "id": profile_id,
                        "llm_profile_ref": "aiat-openhands-omniroute-coding",
                        "mcp_server_refs": [mcp_key],
                        "tools": [{"name": "TerminalTool"}, {"name": "FileEditorTool"}],
                        "enable_sub_agents": False,
                        "enable_switch_llm_tool": False,
                    },
                },
            )
        if request.method == "POST" and request.url.path == "/api/agent-profiles/aiat-openhands-v1-43-0-coding/materialize":
            return httpx.Response(
                200,
                json={
                    "valid": True,
                    "llm_profile_ref": "aiat-openhands-omniroute-coding",
                    "llm_profile_resolved": True,
                    "resolved_mcp_config_keys": [mcp_key],
                    "dangling_mcp_server_refs": [],
                },
            )
        raise AssertionError(request)

    client = httpx.Client(base_url="http://openhands.test", transport=httpx.MockTransport(handler))
    report = MODULE.provision(
        base_url="http://openhands.test",
        session_api_key="session",
        aiat_tool_secret="tool-secret-that-is-not-retained",
        model_id="omniroute-coding",
        gateway_url="http://litellm:4000",
        gateway_api_key="gateway-secret-that-is-not-retained",
        mcp_key=mcp_key,
        client=client,
    )
    assert report["status"] == "PASS"
    assert UUID(report["agent_profile"]["id"])
    assert report["mcp"]["grant_value_retained"] is False
    assert "tool-secret-that-is-not-retained" not in json.dumps(report)
    assert "gateway-secret-that-is-not-retained" not in json.dumps(report)
    assert report["mcp"]["preclean"] == "PASS"
    assert ("DELETE", f"/api/settings/mcp/{mcp_key}") in calls
    assert ("POST", f"/api/settings/mcp/{mcp_key}") in calls
    assert ("POST", "/api/agent-profiles/aiat-openhands-v1-43-0-coding") in calls
    client.close()


def test_provisioning_fails_closed_for_unapproved_model() -> None:
    with pytest.raises(MODULE.ProvisioningError, match="approved_omniroute_coding"):
        MODULE.provision(
            base_url="http://openhands.test",
            session_api_key="session",
            aiat_tool_secret="tool-secret",
            model_id="arbitrary-model",
            gateway_url="http://litellm:4000",
            gateway_api_key="gateway-secret",
            mcp_key=MODULE.EXPECTED_MCP_KEY,
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
        )


def test_provisioning_fails_closed_for_noncanonical_mcp_key() -> None:
    with pytest.raises(MODULE.ProvisioningError, match="governed_openhands_certification_key"):
        MODULE.provision(
            base_url="http://openhands.test",
            session_api_key="session",
            aiat_tool_secret="tool-secret",
            model_id="omniroute-coding",
            gateway_url="http://litellm:4000",
            gateway_api_key="gateway-secret",
            mcp_key="aiat-openhands-other-run",
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
        )


def test_mcp_readback_rejects_unapproved_entries() -> None:
    with pytest.raises(MODULE.ProvisioningError, match="unapproved_entries"):
        MODULE._validate_mcp_entry(
            {
                MODULE.EXPECTED_MCP_KEY: {
                    "url": MODULE.BRIDGE_URL,
                    "transport": "streamable-http",
                    "enabled": True,
                    "headers": {"X-AIAT-OpenHands-Grant": "REDACTED"},
                },
                "unexpected-external-server": {"url": "https://example.invalid/mcp"},
            },
            MODULE.EXPECTED_MCP_KEY,
        )
