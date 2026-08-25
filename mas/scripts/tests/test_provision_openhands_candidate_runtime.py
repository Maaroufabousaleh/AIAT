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
    provider_connection_present = False
    profile_disabled_skills: list[str] = []
    mcp_grant = ""
    mcp_key = MODULE.EXPECTED_MCP_KEY

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mcp_present, provider_connection_present, mcp_grant
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/api/llm/provider-connections":
            connections = []
            if provider_connection_present:
                connections.append(
                    {
                        "id": "gateway-connection",
                        "provider": MODULE.GATEWAY_PROVIDER,
                        "display_name": MODULE.GATEWAY_DISPLAY_NAME,
                        "base_url": "http://litellm:4000",
                        "api_key_set": True,
                    }
                )
            return httpx.Response(200, json=connections)
        if request.method == "POST" and request.url.path == "/api/llm/provider-connections":
            provider_connection_present = True
            return httpx.Response(
                201,
                json={
                    "id": "gateway-connection",
                    "provider": MODULE.GATEWAY_PROVIDER,
                    "display_name": MODULE.GATEWAY_DISPLAY_NAME,
                    "base_url": "http://litellm:4000",
                    "api_key_set": True,
                },
            )
        if request.method == "POST" and request.url.path == "/api/profiles/aiat-openhands-omniroute-coding":
            return httpx.Response(201, json={"name": "aiat-openhands-omniroute-coding", "message": "saved"})
        if request.method == "GET" and request.url.path == "/api/profiles/aiat-openhands-omniroute-coding":
            # This is the pinned Agent Server v1.43.0 readback envelope.
            return httpx.Response(200, json={"name": "aiat-openhands-omniroute-coding", "config": {"model": "omniroute-coding", "provider_connection_id": "gateway-connection"}})
        if request.method == "POST" and request.url.path == f"/api/settings/mcp/{mcp_key}":
            mcp_present = True
            mcp_grant = json.loads(request.content.decode())["headers"]["X-AIAT-OpenHands-Grant"]
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
                    # Agent Server v1.43.0 masks secret-bearing headers on
                    # settings readback; provisioning may prove presence and
                    # shape, while the trusted certification adapter rotates
                    # the grant before use.
                    "headers": {"X-AIAT-OpenHands-Grant": "**********"},
                }
            } if mcp_present else {}
            return httpx.Response(
                200,
                # This is the pinned Agent Server v1.43.0 settings envelope.
                json={"agent_settings": {"mcp_config": config}},
            )
        if request.method == "POST" and request.url.path == "/api/agent-profiles/aiat-openhands-v1-43-0-coding":
            nonlocal profile_disabled_skills
            payload = json.loads(request.content.decode())
            profile_disabled_skills = list(payload.get("disabled_skills") or [])
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
                        "tools": [{"name": "terminal"}, {"name": "file_editor"}],
                        "enable_sub_agents": False,
                        "enable_switch_llm_tool": False,
                        "disabled_skills": profile_disabled_skills,
                    },
                },
            )
        if request.method == "POST" and request.url.path == "/api/agent-profiles/aiat-openhands-v1-43-0-coding/materialize":
            resolved_skills = [] if profile_disabled_skills else ["synthetic-public-skill"]
            return httpx.Response(
                200,
                json={
                    "valid": True,
                    "llm_profile_ref": "aiat-openhands-omniroute-coding",
                    "llm_profile_resolved": True,
                    "resolved_mcp_config_keys": [mcp_key],
                    "dangling_mcp_server_refs": [],
                    "resolved_skills": resolved_skills,
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
    assert report["mcp"]["grant_readback"] == "REDACTED_BY_AGENT_SERVER"
    assert "tool-secret-that-is-not-retained" not in json.dumps(report)
    assert "gateway-secret-that-is-not-retained" not in json.dumps(report)
    assert report["mcp"]["preclean"] == "PASS"
    assert report["agent_profile"]["disabled_skills_count"] == 1
    assert report["agent_profile"]["resolved_skills_count"] == 0
    assert ("DELETE", f"/api/settings/mcp/{mcp_key}") in calls
    assert ("POST", f"/api/settings/mcp/{mcp_key}") in calls
    assert ("POST", "/api/agent-profiles/aiat-openhands-v1-43-0-coding") in calls
    client.close()


def test_provisioning_rejects_gateway_connection_readback_mismatch() -> None:
    provider_connection_present = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal provider_connection_present
        if request.method == "GET" and request.url.path == "/api/llm/provider-connections":
            if not provider_connection_present:
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "gateway-connection",
                        "provider": MODULE.GATEWAY_PROVIDER,
                        "display_name": MODULE.GATEWAY_DISPLAY_NAME,
                        "base_url": "http://operator-host.invalid:4000",
                        "api_key_set": True,
                    }
                ],
            )
        if request.method == "POST" and request.url.path == "/api/llm/provider-connections":
            provider_connection_present = True
            return httpx.Response(
                201,
                json={
                    "id": "gateway-connection",
                    "provider": MODULE.GATEWAY_PROVIDER,
                    "display_name": MODULE.GATEWAY_DISPLAY_NAME,
                    "base_url": "http://litellm:4000",
                    "api_key_set": True,
                },
            )
        raise AssertionError(request)

    client = httpx.Client(
        base_url="http://openhands.test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MODULE.ProvisioningError, match="base_url_readback_mismatch"):
        MODULE.provision(
            base_url="http://openhands.test",
            session_api_key="session",
            aiat_tool_secret="tool-secret",
            model_id="omniroute-coding",
            gateway_url="http://litellm:4000",
            gateway_api_key="gateway-secret",
            mcp_key=MODULE.EXPECTED_MCP_KEY,
            client=client,
        )
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


def test_provisioning_rejects_preexisting_provider_connection_without_secret_readback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/llm/provider-connections":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "stale-gateway-connection",
                        "provider": "aiat-gateway",
                        "base_url": "http://litellm:4000",
                        "api_key_set": True,
                    }
                ],
            )
        raise AssertionError(request)

    client = httpx.Client(
        base_url="http://openhands.test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MODULE.ProvisioningError, match="provider_connection_store_not_empty"):
        MODULE.provision(
            base_url="http://openhands.test",
            session_api_key="session",
            aiat_tool_secret="tool-secret",
            model_id="omniroute-coding",
            gateway_url="http://litellm:4000",
            gateway_api_key="gateway-secret",
            mcp_key=MODULE.EXPECTED_MCP_KEY,
            client=client,
        )
    client.close()


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


def test_mcp_readback_merges_direct_and_v143_nested_envelopes() -> None:
    key = MODULE.EXPECTED_MCP_KEY
    assert MODULE._mcp_config(
        {
            "mcp_config": {},
            "agent_settings": {"mcp_config": {key: {"url": MODULE.BRIDGE_URL}}},
        }
    ) == {key: {"url": MODULE.BRIDGE_URL}}


def test_agent_profile_payload_uses_deny_list_for_server_discovered_skills() -> None:
    payload = MODULE._agent_profile_payload(
        mcp_key=MODULE.EXPECTED_MCP_KEY,
        disabled_skills=["public-skill", "project-skill"],
    )
    assert payload["disabled_skills"] == ["public-skill", "project-skill"]
    assert payload["mcp_server_refs"] == [MODULE.EXPECTED_MCP_KEY]
    assert {item["name"] for item in payload["tools"]} == MODULE.EXPECTED_AGENT_TOOLS


def test_agent_profile_uses_v143_registered_tool_wire_names() -> None:
    payload = MODULE._agent_profile_payload(mcp_key=MODULE.EXPECTED_MCP_KEY, disabled_skills=[])
    assert [item["name"] for item in payload["tools"]] == ["terminal", "file_editor"]
    assert "TerminalTool" not in {item["name"] for item in payload["tools"]}
    assert "FileEditorTool" not in {item["name"] for item in payload["tools"]}


def test_skill_readback_rejects_malformed_or_unresolved_catalog() -> None:
    with pytest.raises(MODULE.ProvisioningError, match="disabled_skills_readback_invalid"):
        MODULE._validate_skill_readback({"disabled_skills": [None]}, [])
    with pytest.raises(MODULE.ProvisioningError, match="disabled_skills_readback_mismatch"):
        MODULE._validate_skill_readback({"disabled_skills": []}, ["public-skill"])
