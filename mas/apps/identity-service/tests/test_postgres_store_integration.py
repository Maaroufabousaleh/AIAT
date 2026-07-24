from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from identity_service.store import PostgresIdentityStore


@pytest.fixture
def anyio_backend() -> str:
    # asyncpg is an asyncio driver; exercising it under AnyIO's Trio backend
    # would test an unsupported event-loop combination rather than the store.
    return "asyncio"


@pytest.mark.anyio
async def test_postgres_store_durable_identity_lifecycle() -> None:
    dsn = os.getenv("TEST_IDENTITY_DATABASE_DSN")
    if not dsn:
        pytest.skip("TEST_IDENTITY_DATABASE_DSN is not configured")

    store = PostgresIdentityStore(dsn, content_encryption_key=Fernet.generate_key().decode())
    company_id, worker_id = uuid4(), uuid4()
    idempotency_key = f"mailbox:{company_id}:{worker_id}"
    try:
        assert await store.healthcheck()
        registration = await store.ensure_client_registration(
            client_id=f"integration-{worker_id}", public_key="public-key-fixture",
            scopes=["identity:delegate"],
        )
        assert registration["state"] == "ACTIVE"
        assert (await store.get_client_registration(registration["client_id"]))["public_key"] == "public-key-fixture"
        domain = await store.upsert_email_domain(
            domain="agents.integration.invalid", state="VERIFIED",
            provider_domain_id="domain-integration", evidence={"test": True},
            created_by="integration-test",
        )
        assert domain["state"] == "VERIFIED"
        identity, created = await store.provision_identity(
            company_id=company_id, worker_id=worker_id,
            address=f"w-{worker_id}@agents.integration.invalid", alias=None,
            domain="agents.integration.invalid",
            idempotency_key=idempotency_key, quota_mb=100,
        )
        assert created is True
        alias_address = f"integration-{worker_id}@agents.integration.invalid"
        alias = await store.record_email_alias(
            identity_id=identity["id"], address=alias_address,
        )
        assert alias["identity_id"] == identity["id"] and alias["state"] == "ACTIVE"
        other_worker = uuid4()
        other_identity, _ = await store.provision_identity(
            company_id=company_id, worker_id=other_worker,
            address=f"w-{other_worker}@agents.integration.invalid", alias=None,
            domain="agents.integration.invalid",
            idempotency_key=f"mailbox:{company_id}:{other_worker}", quota_mb=100,
        )
        with pytest.raises(ValueError, match="already owned"):
            await store.record_email_alias(identity_id=other_identity["id"], address=alias_address)
        job = await store.start_provisioning_job(
            identity_id=identity["id"], company_id=company_id,
            worker_id=worker_id, idempotency_key=idempotency_key,
        )
        assert job["attempt_count"] == 1
        job = await store.finish_provisioning_job(
            idempotency_key=idempotency_key, state="VERIFYING",
            provider_correlation_id="integration-correlation", evidence={"safe": True},
        )
        assert job and job["state"] == "VERIFYING"
        await store.create_identity_access_grant(
            worker_id=worker_id, identity_id=identity["id"],
            grant_type="mailbox", issued_by="integration-test",
        )
        assert await store.has_identity_access_grant(
            worker_id=worker_id, identity_id=identity["id"], grant_type="mailbox",
        )
        await store.record_mail_event(
            identity_id=identity["id"], provider_message_id="message-integration",
            event_type="READ", metadata={"safe": True},
        )
        verification = await store.record_verification_transaction(
            identity_id=identity["id"], provider_message_id="message-integration",
            idempotency_key=f"verification:{identity['id']}:message-integration",
            code_hash="a" * 64, link_hash=None, state="EXTRACTED",
        )
        assert verification["code_hash"] == "a" * 64
        outbound, _ = await store.create_outbound_request(
            worker_id=worker_id, identity_id=identity["id"],
            sender=identity["address"], recipients=["recipient@example.invalid"],
            subject="Integration", body="encrypted at rest",
            recipient_class="approved_external",
            idempotency_key=f"outbound:{worker_id}:integration",
        )
        claimed, did_claim = await store.claim_outbound_submission(outbound["id"])
        assert did_claim is True and claimed and claimed["state"] == "SUBMITTING"
        replayed, did_reclaim = await store.claim_outbound_submission(outbound["id"])
        assert did_reclaim is False and replayed and replayed["state"] == "SUBMITTING"
        event = await store.create_outbox(
            "mailbox.integration", "agent_email_identity", str(identity["id"]),
            {"worker_id": str(worker_id)},
        )
        events = await store.list_outbox(0, 100)
        assert any(item["sequence"] == event["sequence"] for item in events)
        assert await store.advance_client_cursor("integration-client", int(event["sequence"])) == int(event["sequence"])
        assert await store.advance_client_cursor("integration-client", 0) == int(event["sequence"])
        window = datetime.now(UTC).replace(second=0, microsecond=0)
        assert await store.consume_provider_rate(
            provider="stalwart", rate_key=f"mail:{worker_id}",
            window_started_at=window, limit=1,
        )
        assert not await store.consume_provider_rate(
            provider="stalwart", rate_key=f"mail:{worker_id}",
            window_started_at=window, limit=1,
        )
        account_id = uuid4()
        approval = await store.create_approval(
            worker_id=worker_id, kind="external_account",
            target_id=account_id,
            idempotency_key=f"approval:account:{worker_id}:integration",
        )
        account, account_created = await store.create_external_account(
            account_id=account_id, worker_id=worker_id,
            email_identity_id=identity["id"], service="github",
            service_category="development_test",
            credential_ref=f"external-credential-{worker_id}",
            browser_profile_ref=f"profile-{worker_id}",
            approval_id=approval["id"],
            idempotency_key=f"account:{worker_id}:integration",
        )
        assert account_created is True
        assert account["approval_id"] == approval["id"]
        replayed_account, replayed_created = await store.create_external_account(
            account_id=uuid4(), worker_id=worker_id,
            email_identity_id=identity["id"], service="github",
            service_category="development_test",
            credential_ref="must-not-replace-existing",
            browser_profile_ref=f"profile-{worker_id}",
            approval_id=approval["id"],
            idempotency_key=f"account:{worker_id}:integration",
        )
        assert replayed_created is False
        assert replayed_account["id"] == account["id"]
        async with store.engine.connect() as connection:
            approval_nullable = await connection.scalar(sa.text(
                """SELECT is_nullable
                     FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'external_accounts'
                      AND column_name = 'approval_id'"""
            ))
        assert approval_nullable == "NO"
    finally:
        await store.close()
