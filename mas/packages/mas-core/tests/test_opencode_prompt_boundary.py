"""Keep untrusted task text on the OpenCode plain-prompt endpoint."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from mas_core.worker_contract import AdapterContext, WorkerRunRequest
from mas_core.worker_registry.runtime_adapters import OpenCodeAdapter, OpenCodeInterfaceVerification


@pytest.mark.asyncio
async def test_opencode_task_template_markers_are_sent_as_plain_prompt_text() -> None:
    prompt = "Inspect this literal: @/runtime/opencode.json and !`id`"
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path == "/session/ses_boundary/prompt_async":
            return httpx.Response(204, request=request)
        raise AssertionError(f"unexpected OpenCode request: {request.method} {request.url.path}")

    endpoints = {
        "health": "/global/health",
        "openapi": "/doc",
        "project_current": "/project/current",
        "session_list": "/session",
        "session_create": "/session",
        "session_get": "/session/{sessionID}",
        "session_delete": "/session/{sessionID}",
        "session_status": "/session/status",
        "messages": "/session/{sessionID}/message",
        "prompt_async": "/session/{sessionID}/prompt_async",
        "events": "/global/event",
        "abort": "/session/{sessionID}/abort",
        "diff": "/session/{sessionID}/diff",
        "permission_reply": "/session/{sessionID}/permissions/{permissionID}",
        "mcp_add": "/mcp",
        "mcp_status": "/mcp",
    }
    schema = {"openapi": "3.1.0", "paths": {"/global/health": {"get": {"operationId": "global.health"}}}}
    verification = OpenCodeInterfaceVerification(
        release="1.17.13",
        commit_sha="10c894bdeef3618f5666fb506ef7f9491bb964d8",
        report_version="2",
        approved=True,
        openapi_sha256=hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
        config_schema_sha256="b" * 64,
        endpoints=endpoints,
        evidence={"approval_record_id": "test", "fixture_refs": ["test"]},
    )
    client = httpx.AsyncClient(
        base_url="http://opencode.test",
        transport=httpx.MockTransport(handler),
    )
    adapter = OpenCodeAdapter(
        verification,
        base_url="http://opencode.test",
        worker_id="opencode-prompt-boundary-test",
        client=client,
        context=AdapterContext(
            secrets={"opencode_username": "aiat", "opencode_password": "test-password"}
        ),
    )
    request = WorkerRunRequest(
        idempotency_key="opencode-prompt-boundary-test",
        worker_id=adapter.worker_id,
        task_type="verification",
        task_input={"prompt": prompt},
        resolved_model_profile={"profile_id": "coding", "exact_model_id": "aiat/model"},
    )

    try:
        assert await adapter._submit(request, "ses_boundary")
    finally:
        await adapter.close()
        await client.aclose()

    assert len(seen) == 1
    assert seen[0].url.path == "/session/ses_boundary/prompt_async"
    body = json.loads(seen[0].content)
    assert body["parts"] == [{"type": "text", "text": prompt}]
    assert "command" not in body
    assert "shell" not in body
