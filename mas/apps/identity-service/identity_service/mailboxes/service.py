"""Idempotent provider-neutral email identity provisioning and suspension."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from ..models import IdentityState
from ..providers.base import InboundMailProvider
from ..store import IdentityStore
from ..sync.outbox import OutboxService
from ..usage.ledger import UsageLedger


class MailboxService:
    def __init__(self, *, store: IdentityStore, provider: InboundMailProvider, outbox: OutboxService, usage: UsageLedger, agent_mail_domain: str, quota_mb: int, retention_days: int = 180, provider_rate_limit: int = 120, inbound_provider_name: str | None = None, outbound_provider_name: str = "resend") -> None:
        self.store = store
        self.provider = provider
        self.outbox = outbox
        self.usage = usage
        self.agent_mail_domain = agent_mail_domain
        self.quota_mb = quota_mb
        self.retention_days = retention_days
        self.provider_rate_limit = provider_rate_limit
        self.inbound_provider_name = inbound_provider_name or str(getattr(provider, "provider_name", "stalwart"))
        self.outbound_provider_name = outbound_provider_name

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
            inbound_provider=self.inbound_provider_name,
            outbound_provider=self.outbound_provider_name,
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
                provider=self.inbound_provider_name, rate_key=f"email-identity-provision:{worker_id}",
                window_started_at=window, limit=min(self.provider_rate_limit, 10),
            ):
                raise PermissionError(f"{self.inbound_provider_name} identity provisioning rate limit exceeded")
            provider_account_id = identity.get("provider_account_id")
            binding = await self.store.get_provider_binding(
                identity_id=identity["id"], direction="INBOUND", provider=self.inbound_provider_name
            )
            provider_reference = (binding or {}).get("provider_reference")
            if provider_reference:
                created_identity = {"provider_reference": provider_reference, "provider_account_id": provider_account_id}
            elif hasattr(self.provider, "provision_identity"):
                created_identity = await self.provider.provision_identity(
                    identity["address"], identity_id=str(identity["id"]),
                    worker_id=str(worker_id), idempotency_key=idempotency_key,
                )
                provider_reference = created_identity.get("provider_reference") or created_identity.get("provider_account_id")
            else:
                # Legacy Stalwart adapters and test doubles use the mailbox
                # spelling. Reconcile by exact deterministic address first.
                existing_mailbox = await self.provider.find_mailbox(identity["address"])
                if existing_mailbox is None:
                    existing_mailbox = await self.provider.create_mailbox(
                        identity["address"], quota_mb=self.quota_mb, idempotency_key=idempotency_key
                    )
                created_identity = existing_mailbox
                provider_reference = existing_mailbox.get("provider_account_id")
            await self.usage.commit(provider_hold["id"])
            if not provider_reference:
                raise RuntimeError(f"{self.inbound_provider_name} did not return an identity provider reference")
            if self.inbound_provider_name == "stalwart":
                identity = await self.store.set_provider_account(worker_id, str(provider_reference)) or identity
                provider_account_id = str(provider_reference)
            await self.store.upsert_provider_binding(
                identity_id=identity["id"], direction="INBOUND", provider=self.inbound_provider_name,
                provider_reference=str(provider_reference), lifecycle_state="VERIFYING",
                metadata={"correlation_id": created_identity.get("correlation_id")},
            )
            if friendly_alias:
                alias_address = friendly_alias if "@" in friendly_alias else f"{friendly_alias}@{self.agent_mail_domain}"
                if hasattr(self.provider, "add_alias"):
                    if self.inbound_provider_name == "cloudflare":
                        await self.provider.add_alias(
                            str(provider_reference), alias_address,
                            identity_id=str(identity["id"]),
                        )
                    else:
                        await self.provider.add_alias(str(provider_reference), alias_address)
                await self.store.record_email_alias(identity_id=identity["id"], address=alias_address.lower())
            identity = await self.store.set_identity_state(
                worker_id,
                IdentityState.IDENTITY_VERIFYING,
                {"provider": self.inbound_provider_name, "provider_correlation_id": created_identity.get("correlation_id"), "provider_reference": str(provider_reference)},
                outbox_event_type="mailbox.provisioned",
                outbox_payload={
                    "worker_id": str(worker_id), "address": identity["address"],
                    "state": IdentityState.IDENTITY_VERIFYING,
                    "provider": self.inbound_provider_name,
                    "provider_correlation_id": created_identity.get("correlation_id"),
                },
            ) or identity
            await self.store.finish_provisioning_job(
                idempotency_key=idempotency_key, state="VERIFYING",
                provider_correlation_id=created_identity.get("correlation_id"),
                evidence={"provider": self.inbound_provider_name, "provider_reference": str(provider_reference)},
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
        binding = await self.store.get_provider_binding(
            identity_id=current["id"], direction="INBOUND", provider=self.inbound_provider_name
        )
        provider_reference = (binding or {}).get("provider_reference") or current.get("provider_account_id")
        if provider_reference and hasattr(self.provider, "activate_identity"):
            await self.provider.activate_identity(
                str(provider_reference), identity_id=str(current["id"]), address=str(current["address"])
            )
            await self.store.upsert_provider_binding(
                identity_id=current["id"], direction="INBOUND", provider=self.inbound_provider_name,
                provider_reference=str(provider_reference), lifecycle_state="ACTIVE", metadata=evidence,
            )
        outbound_reference = (
            str(provider_reference)
            if provider_reference and self.outbound_provider_name == self.inbound_provider_name
            else None
        )
        await self.store.upsert_provider_binding(
            identity_id=current["id"], direction="OUTBOUND", provider=self.outbound_provider_name,
            provider_reference=outbound_reference, lifecycle_state="ACTIVE",
            metadata={"activated_with_inbound_identity": True, **evidence},
        )
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
        provider_id = (await self.store.get_provider_binding(
            identity_id=identity["id"], direction="INBOUND", provider=self.inbound_provider_name
        ) if identity else None) or {}
        provider_reference = provider_id.get("provider_reference") or (identity or {}).get("provider_account_id")
        if provider_reference:
            if hasattr(self.provider, "suspend_identity"):
                await self.provider.suspend_identity(
                    str(provider_reference), identity_id=str(identity["id"]), address=str(identity["address"])
                )
            elif hasattr(self.provider, "disable_mailbox"):
                await self.provider.disable_mailbox(str(provider_reference))
            await self.store.upsert_provider_binding(
                identity_id=identity["id"], direction="INBOUND", provider=self.inbound_provider_name,
                provider_reference=str(provider_reference), lifecycle_state="SUSPENDED", metadata={"reason": reason},
            )
        return identity

    async def archive(self, worker_id: UUID) -> dict | None:
        identity = await self.store.get_identity(worker_id)
        if identity is None:
            return None
        binding = await self.store.get_provider_binding(
            identity_id=identity["id"], direction="INBOUND", provider=self.inbound_provider_name
        )
        provider_id = (binding or {}).get("provider_reference") or identity.get("provider_account_id")
        archived_at = datetime.now(UTC)
        delete_after = archived_at + timedelta(days=self.retention_days)
        archived = await self.store.set_identity_state(
            worker_id, IdentityState.ARCHIVED,
            {"archived_at": archived_at.isoformat(), "delete_after": delete_after.isoformat(), "retention_days": self.retention_days},
            outbox_event_type="mailbox.archived",
            outbox_payload={"worker_id": str(worker_id), "delete_after": delete_after.isoformat()},
        )
        if provider_id:
            if hasattr(self.provider, "retire_identity"):
                await self.provider.retire_identity(
                    str(provider_id), identity_id=str(identity["id"]), address=str(identity["address"])
                )
            elif hasattr(self.provider, "archive_mailbox"):
                await self.provider.archive_mailbox(str(provider_id))
            await self.store.upsert_provider_binding(
                identity_id=identity["id"], direction="INBOUND", provider=self.inbound_provider_name,
                provider_reference=str(provider_id), lifecycle_state="RETIRED", metadata={"retention_days": self.retention_days},
            )
        return archived
