from __future__ import annotations

import os
from contextlib import suppress
from uuid import uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import create_async_engine

from mas_core.credentials import CredentialsManager, SecretPolicy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_credential_approval_and_rate_limits_are_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = os.getenv("TEST_CORE_DATABASE_DSN")
    if not dsn:
        pytest.skip("TEST_CORE_DATABASE_DSN is not configured")
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(dsn, pool_pre_ping=True)
    manager = CredentialsManager(engine.begin)
    approval_name = f"APPROVAL_{uuid4().hex}"
    rate_name = f"RATE_{uuid4().hex}"
    try:
        await manager.ensure_tables()
        await manager.create(
            approval_name,
            "must-never-enter-an-api-response",
            policy=SecretPolicy(
                allowed_requesters=["adapter-a"],
                allowed_contexts=["provider.call"],
                require_approval=True,
                rate_limit_per_minute=10,
            ),
        )
        pending = await manager.request_approval(
            approval_name,
            requester="adapter-a",
            context="provider.call",
            requested_by="operator",
        )
        decided = await manager.decide_approval(
            pending["id"], approved=True, decided_by="operator"
        )
        assert decided and decided["state"] == "APPROVED"
        assert await manager.resolve(
            approval_name,
            requester="adapter-a",
            context="provider.call",
            approval_id=pending["id"],
        ) == "must-never-enter-an-api-response"
        assert await manager.resolve(
            approval_name,
            requester="adapter-a",
            context="provider.call",
            approval_id=pending["id"],
        ) is None

        await manager.create(
            rate_name,
            "rate-limited-secret",
            policy=SecretPolicy(
                allowed_requesters=["adapter-b"],
                allowed_contexts=["provider.call"],
                rate_limit_per_minute=1,
            ),
        )
        assert await manager.resolve(
            rate_name, requester="adapter-b", context="provider.call"
        ) == "rate-limited-secret"
        assert await manager.resolve(
            rate_name, requester="adapter-b", context="provider.call"
        ) is None

        async with engine.connect() as conn:
            approval_state = await conn.scalar(
                sa.text(
                    "SELECT state FROM credential_resolve_approvals WHERE id = :id"
                ),
                {"id": pending["id"]},
            )
            rate_count = await conn.scalar(
                sa.text(
                    "SELECT resolve_count FROM credential_resolve_rates "
                    "WHERE secret_name = :name AND requester = 'adapter-b'"
                ),
                {"name": rate_name},
            )
        assert approval_state == "CONSUMED"
        assert rate_count == 1
    finally:
        with suppress(Exception):
            await manager.delete(approval_name)
        with suppress(Exception):
            await manager.delete(rate_name)
        await engine.dispose()
