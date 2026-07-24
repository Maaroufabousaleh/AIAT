"""Domain lifecycle backed by Stalwart and sanitized identity events."""

from __future__ import annotations

from ..providers.stalwart import StalwartAdapter
from ..store import IdentityStore
from ..sync.outbox import OutboxService


class DomainService:
    def __init__(self, store: IdentityStore, provider: StalwartAdapter, outbox: OutboxService) -> None:
        self.store = store
        self.provider = provider
        self.outbox = outbox

    async def create(self, domain: str, *, actor_id: str) -> dict:
        result = await self.provider.create_domain(domain, idempotency_key=f"domain:{domain}")
        provider_result = result.get("result") if isinstance(result, dict) else {}
        created = (provider_result or {}).get("created") or {}
        provider_domain_id = next((str(item.get("id")) for item in created.values() if isinstance(item, dict) and item.get("id")), None)
        row = await self.store.upsert_email_domain(domain=domain, state="PENDING_VERIFICATION", provider_domain_id=provider_domain_id, evidence={"provider_correlation_id": result.get("correlation_id")}, created_by=actor_id)
        await self.outbox.append("email_domain.created", "email_domain", domain, {"domain": domain, "provider_correlation_id": result.get("correlation_id")})
        await self.store.create_audit(
            actor_id=actor_id, action="identity.domain.create",
            target_type="email_domain", target_id=str(row["id"]),
            outcome="created",
            metadata={"domain": domain, "provider_correlation_id": result.get("correlation_id")},
        )
        return {"id": str(row["id"]), "domain": domain, "status": row["state"], "provider_correlation_id": result.get("correlation_id")}

    async def verify(self, domain: str, *, actor_id: str) -> dict:
        result = await self.provider.verify_domain(domain)
        provider_result = result.get("result") if isinstance(result, dict) else {}
        listed = (provider_result or {}).get("list") or []
        if not any(isinstance(item, dict) and str(item.get("name", "")).strip().lower() == domain for item in listed):
            raise ValueError("Stalwart did not return the requested domain")
        row = await self.store.upsert_email_domain(domain=domain, state="VERIFIED", provider_domain_id=None, evidence={"provider_correlation_id": result.get("correlation_id")}, created_by=actor_id)
        await self.outbox.append("email_domain.verified", "email_domain", domain, {"domain": domain, "provider_correlation_id": result.get("correlation_id")})
        await self.store.create_audit(
            actor_id=actor_id, action="identity.domain.verify",
            target_type="email_domain", target_id=str(row["id"]),
            outcome="verified",
            metadata={"domain": domain, "provider_correlation_id": result.get("correlation_id")},
        )
        return {"id": str(row["id"]), "domain": domain, "status": row["state"], "provider_correlation_id": result.get("correlation_id")}
