"""Materialize the governed OpenHands runtime objects for one Agent Server run.

The Agent Server persistence directory is disposable in CI.  This command
therefore creates deterministic named LLM/agent profiles and one run-scoped
MCP settings entry after the server starts, then emits only the server-created
agent profile UUID and other non-secret correlation values.  It never prints or
persists provider keys or the signed bridge grant.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from mas_core.worker_contract.openhands_bridge import issue_openhands_tool_grant

SCHEMA = "aiat.openhands-run-scoped-runtime-provisioning.v1"
CANDIDATE_RELEASE = "v1.43.0"
CANDIDATE_COMMIT = "4c1237f391fe394e9f67505fe3a0bd2d81f84188"
CANDIDATE_IMAGE_DIGEST = "sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"
EXPECTED_MODEL_ID = "omniroute-coding"
EXPECTED_GATEWAY_URL = "http://litellm:4000"
LLM_PROFILE_NAME = "aiat-openhands-omniroute-coding"
AGENT_PROFILE_NAME = "aiat-openhands-v1-43-0-coding"
MCP_KEY_PREFIX = "aiat-openhands-"
MCP_KEY_PATTERN = re.compile(r"aiat-openhands-[a-z0-9][a-z0-9._-]*\Z")
BRIDGE_URL = "http://tool-service:8002/openhands/mcp"
WORKER_ID = "coding-worker-openhands-candidate"
TOOL_GRANTS = ("aiat.repository.read", "aiat.repository.write", "aiat.tests.execute")
EXPECTED_AGENT_TOOLS = {"TerminalTool", "FileEditorTool"}


class ProvisioningError(RuntimeError):
    """A required governed object could not be materialized or verified."""


def _json_body(response: httpx.Response, *, expected: set[int] | None = None) -> Any:
    if expected is not None and response.status_code not in expected:
        raise ProvisioningError(f"agent_server_http_{response.status_code}")
    if response.status_code >= 400:
        raise ProvisioningError(f"agent_server_http_{response.status_code}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise ProvisioningError("agent_server_invalid_json") from exc


def _profile_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvisioningError("agent_profile_readback_not_an_object")
    profile = value.get("profile")
    if isinstance(profile, dict):
        return profile
    return value


def _mcp_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvisioningError("agent_settings_readback_not_an_object")
    config = value.get("mcp_config")
    if isinstance(config, dict):
        return config
    config = value.get("mcp_servers")
    if isinstance(config, dict):
        return config
    raise ProvisioningError("agent_settings_readback_has_no_mcp_configuration")


def _write_github_output(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _validate_mcp_entry(config: dict[str, Any], key: str) -> None:
    entry = config.get(key)
    if not isinstance(entry, dict):
        raise ProvisioningError("run_scoped_mcp_entry_missing_after_create")
    if entry.get("url") != BRIDGE_URL:
        raise ProvisioningError("run_scoped_mcp_bridge_url_mismatch")
    if entry.get("transport") != "streamable-http" or entry.get("enabled") is not True:
        raise ProvisioningError("run_scoped_mcp_transport_or_enabled_mismatch")
    headers = entry.get("headers")
    if not isinstance(headers, dict) or "X-AIAT-OpenHands-Grant" not in headers:
        raise ProvisioningError("run_scoped_mcp_grant_header_missing")


def provision(
    *,
    base_url: str,
    session_api_key: str,
    aiat_tool_secret: str,
    model_id: str,
    gateway_url: str,
    gateway_api_key: str,
    mcp_key: str,
    candidate_commit: str = CANDIDATE_COMMIT,
    image_digest: str = CANDIDATE_IMAGE_DIGEST,
    run_id: UUID | None = None,
    project_id: UUID | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Create/read/validate the governed objects on one Agent Server instance."""

    if not session_api_key:
        raise ProvisioningError("session_api_key_missing")
    if model_id != EXPECTED_MODEL_ID:
        raise ProvisioningError("model_id_is_not_the_approved_omniroute_coding_alias")
    if gateway_url != EXPECTED_GATEWAY_URL:
        raise ProvisioningError("model_gateway_url_must_equal_http://litellm:4000")
    if not gateway_api_key:
        raise ProvisioningError("model_gateway_credential_missing")
    if not aiat_tool_secret:
        raise ProvisioningError("aiat_tool_secret_missing")
    if not MCP_KEY_PATTERN.fullmatch(mcp_key):
        raise ProvisioningError("mcp_key_must_use_lowercase_aiat_openhands_key_chars")
    if candidate_commit != CANDIDATE_COMMIT:
        raise ProvisioningError("candidate_commit_mismatch")
    if image_digest != CANDIDATE_IMAGE_DIGEST:
        raise ProvisioningError("candidate_image_digest_mismatch")

    run_id = run_id or uuid4()
    project_id = project_id or uuid4()
    created_client = client is None
    client = client or httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={"X-Session-API-Key": session_api_key, "Accept": "application/json"},
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=False,
    )
    try:
        connections = _json_body(client.get("/api/llm/provider-connections"))
        if not isinstance(connections, list):
            raise ProvisioningError("provider_connection_readback_not_a_list")
        connection = next(
            (
                item
                for item in connections
                if isinstance(item, dict)
                and item.get("provider") == "aiat-gateway"
                and item.get("base_url") == gateway_url
                and item.get("api_key_set") is True
            ),
            None,
        )
        if connection is None:
            connection = _json_body(
                client.post(
                    "/api/llm/provider-connections",
                    json={
                        "display_name": "AIAT governed model gateway (OpenHands certification)",
                        "provider": "aiat-gateway",
                        "api_key": gateway_api_key,
                        "base_url": gateway_url,
                    },
                ),
                expected={201},
            )
        connection_id = str(connection.get("id") or "") if isinstance(connection, dict) else ""
        if not connection_id:
            raise ProvisioningError("provider_connection_id_missing")

        llm_response = client.post(
            f"/api/profiles/{LLM_PROFILE_NAME}",
            json={
                "llm": {
                    "model": model_id,
                    "provider_connection_id": connection_id,
                    "auth_type": "api_key",
                    "timeout": 300,
                    "num_retries": 3,
                    "temperature": 0.0,
                },
                "include_secrets": False,
            },
        )
        _json_body(llm_response, expected={200, 201})
        llm_readback = _json_body(client.get(f"/api/profiles/{LLM_PROFILE_NAME}"))
        llm = llm_readback.get("llm") if isinstance(llm_readback, dict) else None
        if not isinstance(llm, dict) or llm.get("model") != model_id:
            raise ProvisioningError("llm_profile_model_readback_mismatch")
        if llm.get("provider_connection_id") != connection_id:
            raise ProvisioningError("llm_profile_provider_connection_readback_mismatch")

        grant = issue_openhands_tool_grant(
            aiat_tool_secret,
            worker_id=WORKER_ID,
            run_id=run_id,
            project_id=project_id,
            tool_names=TOOL_GRANTS,
            ttl_seconds=300,
        )
        # Certification MCP settings are disposable and must never silently
        # overwrite an existing operator entry.  Delete the run-scoped key
        # through the authenticated Agent Server API, then prove it is absent
        # before creating the fresh grant-bearing configuration.
        preclean_response = client.delete(f"/api/settings/mcp/{mcp_key}")
        if preclean_response.status_code not in {200, 404}:
            raise ProvisioningError("run_scoped_mcp_preclean_delete_failed")
        preclean_settings = _json_body(client.get("/api/settings"))
        preclean_config = _mcp_config(preclean_settings)
        if mcp_key in preclean_config:
            raise ProvisioningError("run_scoped_mcp_entry_present_after_preclean")
        mcp_response = client.post(
            f"/api/settings/mcp/{mcp_key}",
            json={
                "url": BRIDGE_URL,
                "transport": "streamable-http",
                "headers": {"X-AIAT-OpenHands-Grant": grant},
                "enabled": True,
                "timeout": 60.0,
            },
        )
        _json_body(mcp_response, expected={201})
        settings = _json_body(client.get("/api/settings"))
        config = _mcp_config(settings)
        _validate_mcp_entry(config, mcp_key)

        agent_response = client.post(
            f"/api/agent-profiles/{AGENT_PROFILE_NAME}",
            json={
                "agent_kind": "openhands",
                "agent": "CodeActAgent",
                "llm_profile_ref": LLM_PROFILE_NAME,
                "mcp_server_refs": [mcp_key],
                "tools": [
                    {"name": "TerminalTool", "params": {}},
                    {"name": "FileEditorTool", "params": {}},
                ],
                "enable_sub_agents": False,
                "enable_switch_llm_tool": False,
                "disabled_skills": [],
                "tool_concurrency_limit": 1,
            },
        )
        _json_body(agent_response, expected={201})
        agent_readback = _json_body(client.get(f"/api/agent-profiles/{AGENT_PROFILE_NAME}"))
        profile = _profile_object(agent_readback)
        profile_id = str(profile.get("id") or "")
        try:
            UUID(profile_id)
        except (TypeError, ValueError) as exc:
            raise ProvisioningError("agent_profile_readback_has_no_server_generated_uuid") from exc
        if profile.get("llm_profile_ref") != LLM_PROFILE_NAME:
            raise ProvisioningError("agent_profile_llm_binding_readback_mismatch")
        if profile.get("mcp_server_refs") != [mcp_key]:
            raise ProvisioningError("agent_profile_mcp_binding_readback_mismatch")
        tools = profile.get("tools")
        names = {str(item.get("name")) for item in tools if isinstance(item, dict)} if isinstance(tools, list) else set()
        if names != EXPECTED_AGENT_TOOLS:
            raise ProvisioningError("agent_profile_tool_surface_readback_mismatch")
        if profile.get("enable_sub_agents") is not False or profile.get("enable_switch_llm_tool") is not False:
            raise ProvisioningError("agent_profile_disabled_controls_readback_mismatch")

        materialized = _json_body(client.post(f"/api/agent-profiles/{AGENT_PROFILE_NAME}/materialize"))
        if not isinstance(materialized, dict) or materialized.get("valid") is not True:
            raise ProvisioningError("agent_profile_materialize_invalid")
        if materialized.get("llm_profile_resolved") is not True or materialized.get("llm_profile_ref") != LLM_PROFILE_NAME:
            raise ProvisioningError("agent_profile_materialize_llm_unresolved")
        resolved_keys = materialized.get("resolved_mcp_config_keys") or []
        if resolved_keys != [mcp_key] or materialized.get("dangling_mcp_server_refs"):
            raise ProvisioningError("agent_profile_materialize_mcp_unresolved")

        result = {
            "schema_version": SCHEMA,
            "status": "PASS",
            "candidate": {
                "release": CANDIDATE_RELEASE,
                "source_commit": candidate_commit,
                "image_digest": image_digest,
            },
            "store_authority": {
                "profile_store": "this authenticated Agent Server instance",
                "certification_store": "this fresh workflow Agent Server instance",
                "same_authoritative_store": True,
                "profile_id_portable": False,
                "profile_id_server_generated": True,
                "profile_materialization_supported": True,
            },
            "agent_profile": {
                "name": AGENT_PROFILE_NAME,
                "id": profile_id,
                "llm_profile_ref": LLM_PROFILE_NAME,
                "model_id": model_id,
                "mcp_settings_key": mcp_key,
                "tools": sorted(EXPECTED_AGENT_TOOLS),
                "subagents": False,
                "llm_switching": False,
            },
            "mcp": {
                "logical_key": mcp_key,
                "preclean": "PASS",
                "created": True,
                "url": BRIDGE_URL,
                "transport": "streamable-http",
                "grant_header": "X-AIAT-OpenHands-Grant",
                "grant_value_retained": False,
                "arbitrary_external_servers": False,
                "cleanup_owner": "workflow-always-cleanup",
            },
            "materialize": {
                "valid": True,
                "llm_profile_resolved": True,
                "model_id": model_id,
                "resolved_mcp_config_keys": [mcp_key],
                "dangling_mcp_server_refs": [],
            },
            "governance": {
                "authority": "AIAT control plane",
                "model_id": model_id,
                "workspace_policy": "assigned isolated workspace only",
                "sandbox_profile": "gvisor",
                "network_policy": "AIAT egress allowlist",
                "budget_policy": {"timeout_seconds": 300, "max_iterations": 20},
                "cancellation_policy": {"graceful": "pause", "immediate": "interrupt", "resume": "run"},
                "audit_policy": "durable scalar events; payloads and credentials excluded",
                "disabled_capabilities": [
                    "public_skills_marketplace",
                    "arbitrary_plugins",
                    "arbitrary_external_mcp",
                    "browser",
                    "desktop",
                    "vscode",
                    "subagents",
                    "direct_provider_credentials",
                    "direct_cloud_or_deployment_authority",
                ],
            },
            "run": {
                "run_id": str(run_id),
                "project_id": str(project_id),
                "worker_id": WORKER_ID,
            },
            "cleanup": {
                "profile": "container_disposal",
                "mcp": "workflow-always-cleanup-then-verify-absent",
                "secrets_retained": False,
            },
        }
        return result
    finally:
        if created_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OPENHANDS_AGENT_SERVER_URL", ""))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--candidate-commit", default=CANDIDATE_COMMIT)
    parser.add_argument("--image-digest", default=CANDIDATE_IMAGE_DIGEST)
    args = parser.parse_args(argv)
    values = {
        "session_api_key": os.getenv("OPENHANDS_SESSION_API_KEY", ""),
        "aiat_tool_secret": os.getenv("AIAT_TOOL_SECRET", ""),
        "model_id": os.getenv("OPENHANDS_MODEL_ID", ""),
        "gateway_url": os.getenv("OPENHANDS_MODEL_GATEWAY_URL", ""),
        "gateway_api_key": os.getenv("OPENHANDS_MODEL_GATEWAY_API_KEY", ""),
        "mcp_key": os.getenv("OPENHANDS_MCP_SETTINGS_KEY", ""),
    }
    try:
        if not args.base_url:
            raise ProvisioningError("agent_server_url_missing")
        report = provision(base_url=args.base_url, candidate_commit=args.candidate_commit, image_digest=args.image_digest, **values)
        _write_github_output(
            args.github_output,
            {
                "profile_id": report["agent_profile"]["id"],
                "mcp_key": report["mcp"]["logical_key"],
                "run_id": report["run"]["run_id"],
                "project_id": report["run"]["project_id"],
                "materialized": "true",
            },
        )
    except ProvisioningError as exc:
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "failure": str(exc),
            "secrets_retained": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "BLOCKED", "failure": str(exc)}, sort_keys=True))
        return 2
    except (httpx.HTTPError, ValueError) as exc:
        # Keep transport/provider parsing failures fail-closed without ever
        # echoing a URL, credential, or response body into evidence.
        failure = f"{type(exc).__name__}_during_runtime_provisioning"
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "failure": failure,
            "secrets_retained": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "BLOCKED", "failure": failure}, sort_keys=True))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "profile_id_materialized": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
