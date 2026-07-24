"""Regression coverage for signed production tool caller identity."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from tool_service.caller_auth import verify_signed_caller
from tool_service.config import Settings
from tool_service.registry import ToolRegistry
from tool_service.routes import router
from tool_service.tool_grants import ToolGrantStore
from tool_service.tools.identity import _worker_context

from mas_core.protocols.tool import ToolResponse


def _signed_request(app: FastAPI, private_key: Ed25519PrivateKey, body: bytes, nonce: str) -> Request:
    timestamp = str(int(time.time()))
    path = "/tools/execute"
    canonical = "\n".join(
        ("aiat.tool.v1", "POST", path, timestamp, nonce, hashlib.sha256(body).hexdigest())
    ).encode()
    headers = [
        (b"x-aiat-signature-version", b"aiat.tool.v1"),
        (b"x-aiat-client-id", b"worker-1"),
        (b"x-aiat-timestamp", timestamp.encode()),
        (b"x-aiat-nonce", nonce.encode()),
        (b"x-aiat-signature", base64.b64encode(private_key.sign(canonical))),
    ]
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "server": ("tool-service", 443),
            "client": ("127.0.0.1", 10000),
            "app": app,
        },
        receive,
    )


@pytest.mark.anyio
async def test_signed_caller_binds_request_and_rejects_replay() -> None:
    app = FastAPI()
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    body = b'{"caller_id":"worker-1"}'
    nonce = str(uuid4())

    assert await verify_signed_caller(
        _signed_request(app, private_key, body, nonce), {"worker-1": public_key}
    ) == "worker-1"
    with pytest.raises(HTTPException, match="Replayed"):
        await verify_signed_caller(
            _signed_request(app, private_key, body, nonce), {"worker-1": public_key}
        )


@pytest.mark.anyio
async def test_signed_caller_uses_durable_nonce_store() -> None:
    app = FastAPI()
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    body = b'{"caller_id":"worker-1"}'
    nonce = str(uuid4())

    class ReplayStore:
        def __init__(self) -> None:
            self.values: set[tuple[str, str]] = set()

        async def consume_signature_nonce(self, client_id, value, _expires_at):
            key = (client_id, value)
            if key in self.values:
                return False
            self.values.add(key)
            return True

    replay_store = ReplayStore()
    assert await verify_signed_caller(
        _signed_request(app, private_key, body, nonce),
        {"worker-1": public_key}, replay_store,
    ) == "worker-1"
    with pytest.raises(HTTPException, match="Replayed"):
        await verify_signed_caller(
            _signed_request(app, private_key, body, nonce),
            {"worker-1": public_key}, replay_store,
        )


def test_identity_tools_require_an_explicit_durable_grant() -> None:
    registry = object.__new__(ToolRegistry)
    registry._worker_grants = {}
    assert registry._check_worker_grant("worker-1", "mail.list") is False
    assert registry._check_worker_grant("worker-1", "identity.external.login") is False
    assert registry._check_worker_grant("worker-1", "web_search") is True
    registry.grant_tool("worker-1", "mail.list")
    assert registry._check_worker_grant("worker-1", "mail.list") is True


def test_worker_identity_context_cannot_select_another_worker() -> None:
    with pytest.raises(PermissionError, match="cross-worker"):
        _worker_context(
            {
                "worker_id": "worker-2",
                "_aiat_context": {
                    "caller_id": "worker-1",
                    "caller_role": "worker",
                },
            }
        )
    worker_id, actor = _worker_context(
        {
            "worker_id": "worker-1",
            "_aiat_context": {
                "caller_id": "worker-1",
                "caller_role": "worker",
            },
        }
    )
    assert worker_id == actor["actor_id"] == "worker-1"


def test_production_tool_authentication_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="TOOL_SECRET"):
        Settings(
            redis_password="not-the-default",
            minio_secret_key="not-the-default",
            pgbouncer_dsn="postgresql://db.example/mas",
            tool_secret="short",
            aiat_tool_caller_public_keys_json=(
                '{"orchestrator-api":"' + public_key + '"}'
            ),
            aiat_tool_delegate_client_ids_json='["orchestrator-api"]',
        )


@pytest.mark.anyio
async def test_production_route_reuses_signed_body_for_model_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")
    settings = Settings(
        redis_password="not-the-default",
        minio_secret_key="not-the-default",
        pgbouncer_dsn="postgresql://db.example/mas",
        tool_secret="route-test-secret-that-is-at-least-32-characters",
        aiat_tool_caller_public_keys_json=json.dumps({"worker-1": public_key}),
    )

    class ReplayStore:
        async def consume_signature_nonce(self, *_args) -> bool:
            return True

    class Registry:
        async def execute(self, body):
            return ToolResponse(tool_name=body.tool_name, success=True, result={"ok": True})

    app = FastAPI()
    app.state.settings = settings
    app.state.tool_grant_store = ReplayStore()
    app.state.registry = Registry()
    app.include_router(router)

    payload = {
        "agent_id": "worker-1",
        "sender_role": "worker",
        "tool_name": "mail.list",
        "kwargs": {},
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    nonce = str(uuid4())
    canonical = "\n".join(
        (
            "aiat.tool.v1", "POST", "/tools/execute", timestamp, nonce,
            hashlib.sha256(raw).hexdigest(),
        )
    ).encode()
    headers = {
        "Authorization": f"Bearer {settings.tool_secret}",
        "Content-Type": "application/json",
        "X-AIAT-Signature-Version": "aiat.tool.v1",
        "X-AIAT-Client-ID": "worker-1",
        "X-AIAT-Timestamp": timestamp,
        "X-AIAT-Nonce": nonce,
        "X-AIAT-Signature": base64.b64encode(private_key.sign(canonical)).decode(),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://tool-service"
    ) as client:
        response = await client.post("/tools/execute", content=raw, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"ok": True}


@pytest.mark.anyio
async def test_browser_identity_and_signature_nonce_are_durable_in_postgres() -> None:
    dsn = os.getenv("TEST_CORE_DATABASE_DSN")
    if not dsn:
        pytest.skip("TEST_CORE_DATABASE_DSN is not configured")
    store = await ToolGrantStore.connect(dsn)
    worker_id = f"worker-{uuid4()}"
    nonce = str(uuid4())
    try:
        identity = await store.ensure_browser_identity(worker_id)
        assert identity["state"] == "ACTIVE"
        assert await store.consume_signature_nonce("worker-test", nonce, int(time.time()) + 300)
        assert not await store.consume_signature_nonce("worker-test", nonce, int(time.time()) + 300)
        revoked = await store.revoke_browser_identity(worker_id, retired=True)
        assert revoked and revoked["state"] == "RETIRED"
    finally:
        async with store._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM tool_signature_nonces WHERE client_id = $1 AND nonce = $2",
                "worker-test", nonce,
            )
            await conn.execute(
                "DELETE FROM worker_browser_identities WHERE worker_id = $1",
                worker_id,
            )
        await store.close()
