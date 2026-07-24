"""Idempotent mailbox provisioning and suspension."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from ..models import IdentityState
from ..providers.stalwart import StalwartAdapter
from ..store import IdentityStore
from ..sync.outbox import OutboxService
from ..usage.ledger import UsageLedger


class MailboxService:
    def __init__(self, *, store: IdentityStore, provider: StalwartAdapter, outbox: OutboxService, usage: UsageLedger, agent_mail_domain: str, quota_mb: int, retention_days: int = 180, provider_rate_limit: int = 120) -> None:
        self.store = store
        self.provider = provider
        self.outbox = outbox
        self.usage = usage
        self.agent_mail_domain = agent_mail_domain
        self.quota_mb = quota_mb
        self.retention_days = retention_days
        self.provider_rate_limit = provider_rate_limit

    def address_for(self, worker_id: UUID) -> str:
        return f"w-{worker_id}@{self.agent_mail_domain}"

    async def provision(self, *, company_id: UUID, worker_id: UUID, friendly_alias: str | None, idempotency_key: str) -> tuple[dict, bool]:
        expected_key = f"mailbox:{company_id}:{worker_id}"
        if idempotency_key != expected_key:
            raise ValueError("mailbox provisioning requires stable company/worker idempotency key")
        identity, created = await self.store.provision_identity(
            company_id=company_id, worker_id=worker_id,
            address=self.address_for(worker_id), alias=friendly_alias,
            domain=self.agent_mail_domain, idempotency_key=idempotency_key,
            quota_mb=self.quota_mb,
        )
        if not created and identity.get("state") in {
            IdentityState.IDENTITY_ACTIVE,
            IdentityState.IDENTITY_VERIFYING,
        }:
            return identity, False
        if not created and identity.get("state") == IdentityState.IDENTITY_PROVISIONING:
            job = await self.store.get_provisioning_job(idempotency_key)
            updated_at = job.get("updated_at") if job else None
            if (
                job
                and str(job.get("state")) == "RUNNING"
                and isinstance(updated_at, datetime)
                and updated_at > datetime.now(UTC) - timedelta(seconds=60)
            ):
                # A concurrent request is still inside its bounded provider
                # call. Older RUNNING records are crash residue and resume
                # through provider-side address reconciliation below.
                return identity, False
        hold = await self.usage.reserve(worker_id=worker_id, kind="mailbox_provisioning", idempotency_key=f"hold:{idempotency_key}")
        storage_hold = await self.usage.reserve(
            worker_id=worker_id,
            kind="mailbox_storage_mb",
            units=self.quota_mb,
            idempotency_key=f"hold:storage:{idempotency_key}",
        )
        provider_hold = await self.usage.reserve(
            worker_id=worker_id,
            kind="provider_api_call",
            idempotency_key=f"hold:provider:{idempotency_key}",
        )
        try:
            await self.store.start_provisioning_job(
                identity_id=identity["id"], company_id=company_id,
                worker_id=worker_id, idempotency_key=idempotency_key,
            )
            await self.store.set_identity_state(worker_id, IdentityState.IDENTITY_PROVISIONING, {"idempotency_key": idempotency_key})
            window = datetime.now(UTC).replace(second=0, microsecond=0)
            if not await self.store.consume_provider_rate(
                provider="stalwart", rate_key=f"mailbox-provision:{worker_id}",
                window_started_at=window, limit=min(self.provider_rate_limit, 10),
            ):
                raise PermissionError("Stalwart mailbox provisioning rate limit exceeded")
            provider_account_id = identity.get("provider_account_id")
            if provider_account_id:
                existing_mailbox = await self.provider.get_mailbox(
                    str(provider_account_id)
                )
                created_mailbox = {
                    "provider_account_id": str(provider_account_id),
                    "correlation_id": existing_mailbox.get("correlation_id"),
                    "result": existing_mailbox.get("result"),
                }
            else:
                # Reconcile the deterministic worker address before create.
                # This recovers a provider commit followed by a local crash and
                # prevents a retry from attempting a duplicate mailbox.
                created_mailbox = await self.provider.find_mailbox(
                    identity["address"]
                )
                if created_mailbox is None:
                    created_mailbox = await self.provider.create_mailbox(
                        identity["address"],
                        quota_mb=self.quota_mb,
                        idempotency_key=idempotency_key,
                    )
            await self.usage.commit(provider_hold["id"])
            if not created_mailbox.get("provider_account_id"):
                raise RuntimeError("Stalwart did not return a mailbox account identifier")
            identity = await self.store.set_provider_account(worker_id, created_mailbox.get("provider_account_id")) or identity
            if friendly_alias:
                await self.provider.add_alias(str(identity["provider_account_id"]), friendly_alias)
                alias_address = friendly_alias if "@" in friendly_alias else f"{friendly_alias}@{self.agent_mail_domain}"
                await self.store.record_email_alias(identity_id=identity["id"], address=alias_address.lower())
            identity = await self.store.set_identity_state(
                worker_id,
                IdentityState.IDENTITY_VERIFYING,
                {"provider_correlation_id": created_mailbox.get("correlation_id"), "provider_account_id": identity.get("provider_account_id")},
                outbox_event_type="mailbox.provisioned",
                outbox_payload={
                    "worker_id": str(worker_id), "address": identity["address"],
                    "state": IdentityState.IDENTITY_VERIFYING,
                    "provider_correlation_id": created_mailbox.get("correlation_id"),
                },
            ) or identity
            await self.store.finish_provisioning_job(
                idempotency_key=idempotency_key, state="VERIFYING",
                provider_correlation_id=created_mailbox.get("correlation_id"),
                evidence={"provider_account_id": identity.get("provider_account_id")},
            )
            await self.usage.commit(hold["id"])
            await self.usage.commit(storage_hold["id"])
            return identity, created
        except Exception as exc:
            await self.usage.release(hold["id"])
            await self.usage.release(storage_hold["id"])
            await self.usage.release(provider_hold["id"])
            await self.store.finish_provisioning_job(
                idempotency_key=idempotency_key, state="FAILED",
                provider_correlation_id=getattr(exc, "correlation_id", None),
                evidence={"error_code": type(exc).__name__},
            )
            await self.store.set_identity_state(
                worker_id,
                IdentityState.IDENTITY_PROVISIONING_FAILED,
                {"error_code": type(exc).__name__},
                outbox_event_type="mailbox.provisioning_failed",
                outbox_payload={"worker_id": str(worker_id), "error_code": type(exc).__name__},
            )
            raise

    async def mark_delivery_verified(self, worker_id: UUID, *, evidence: dict) -> dict:
        current = await self.store.get_identity(worker_id)
        if current is None:
            raise LookupError("identity not found")
        identity = await self.store.set_identity_state(
            worker_id, IdentityState.IDENTITY_ACTIVE, evidence,
            outbox_event_type="mailbox.identity_active",
            outbox_payload={"worker_id": str(worker_id), "address": current["address"]},
        )
        if identity is None:
            raise LookupError("identity not found")
        return identity

    async def suspend(self, worker_id: UUID, *, reason: str) -> dict | None:
        identity = await self.store.get_identity(worker_id)
        if identity is None:
            return None
        identity = await self.store.set_identity_state(
            worker_id, IdentityState.SUSPENDED, {"reason": reason},
            outbox_event_type="mailbox.suspended",
            outbox_payload={"worker_id": str(worker_id), "reason": reason},
        )
        # Revoke AIAT authorization before the network call. A provider outage
        # may delay remote disablement, but it must never leave local mailbox
        # or browser access active.
        provider_id = identity.get("provider_account_id") if identity else None
        if provider_id:
            await self.provider.disable_mailbox(str(provider_id))
        return identity

    async def archive(self, worker_id: UUID) -> dict | None:
        identity = await self.store.get_identity(worker_id)
        if identity is None:
            return None
        provider_id = identity.get("provider_account_id")
        archived_at = datetime.now(UTC)
        delete_after = archived_at + timedelta(days=self.retention_days)
        archived = await self.store.set_identity_state(
            worker_id, IdentityState.ARCHIVED,
            {"archived_at": archived_at.isoformat(), "delete_after": delete_after.isoformat(), "retention_days": self.retention_days},
            outbox_event_type="mailbox.archived",
            outbox_payload={"worker_id": str(worker_id), "delete_after": delete_after.isoformat()},
        )
        if provider_id:
            await self.provider.archive_mailbox(str(provider_id))
        return archived
