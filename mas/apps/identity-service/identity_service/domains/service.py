"""Provider-neutral domain lifecycle and sanitized identity events."""

from __future__ import annotations

from ..providers.base import InboundMailProvider
from ..store import IdentityStore
from ..sync.outbox import OutboxService


class DomainService:
    def __init__(self, store: IdentityStore, provider: InboundMailProvider, outbox: OutboxService, *, provider_name: str | None = None) -> None:
        self.store = store
        self.provider = provider
        self.outbox = outbox
        self.provider_name = provider_name or str(getattr(provider, "provider_name", "stalwart"))

    async def create(self, domain: str, *, actor_id: str) -> dict:
        if hasattr(self.provider, "create_domain"):
            result = await self.provider.create_domain(domain, idempotency_key=f"domain:{domain}")
            provider_result = result.get("result") if isinstance(result, dict) else {}
            created = (provider_result or {}).get("created") or {}
            provider_domain_id = next((str(item.get("id")) for item in created.values() if isinstance(item, dict) and item.get("id")), None)
        else:
            # Cloudflare Email Routing is configured at the edge; it has no
            # mailbox/domain-admin API in the runtime identity service. AIAT
            # records the domain locally and operators verify routing during
            # deployment certification.
            result = {"correlation_id": None, "result": {"provider": self.provider_name}}
            provider_domain_id = None
        row = await self.store.upsert_email_domain(domain=domain, state="PENDING_VERIFICATION", provider_domain_id=provider_domain_id, evidence={"provider": self.provider_name, "provider_correlation_id": result.get("correlation_id")}, created_by=actor_id)
        await self.outbox.append("email_domain.created", "email_domain", domain, {"domain": domain, "provider": self.provider_name, "provider_correlation_id": result.get("correlation_id")})
        await self.store.create_audit(
            actor_id=actor_id, action="identity.domain.create",
            target_type="email_domain", target_id=str(row["id"]),
            outcome="created",
            metadata={"domain": domain, "provider_correlation_id": result.get("correlation_id")},
        )
        return {"id": str(row["id"]), "domain": domain, "status": row["state"], "provider_correlation_id": result.get("correlation_id")}

    async def verify(self, domain: str, *, actor_id: str) -> dict:
        if hasattr(self.provider, "verify_domain"):
            result = await self.provider.verify_domain(domain)
            provider_result = result.get("result") if isinstance(result, dict) else {}
            listed = (provider_result or {}).get("list") or []
            if not any(isinstance(item, dict) and str(item.get("name", "")).strip().lower() == domain for item in listed):
                raise ValueError("provider did not return the requested domain")
        else:
            result = {"correlation_id": None, "result": {"provider": self.provider_name, "operator_verified": True}}
        row = await self.store.upsert_email_domain(domain=domain, state="VERIFIED", provider_domain_id=None, evidence={"provider": self.provider_name, "provider_correlation_id": result.get("correlation_id"), "operator_verified": self.provider_name == "cloudflare"}, created_by=actor_id)
        await self.outbox.append("email_domain.verified", "email_domain", domain, {"domain": domain, "provider": self.provider_name, "provider_correlation_id": result.get("correlation_id")})
        await self.store.create_audit(
            actor_id=actor_id, action="identity.domain.verify",
            target_type="email_domain", target_id=str(row["id"]),
            outcome="verified",
            metadata={"domain": domain, "provider_correlation_id": result.get("correlation_id")},
        )
        return {"id": str(row["id"]), "domain": domain, "status": row["state"], "provider_correlation_id": result.get("correlation_id")}
