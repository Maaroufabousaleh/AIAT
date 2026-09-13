"""Application service coordinating identity policy, providers, and audit."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from uuid import UUID, uuid4

from mas_core.observability.mail_edge import normalize_provider_webhook

from .approvals.service import ApprovalService
from .config import IdentitySettings
from .credentials.leases import issue_opaque_lease
from .domains.service import DomainService
from .external_accounts.service import ExternalAccountPolicy
from .mailboxes.service import MailboxService
from .models import ExternalAccountState, IdentityState
from .outbound.policy import OutboundPolicy
from .outbound.service import OutboundService
from .providers.base import InboundMailProvider, OutboundMailProvider
from .providers.factory import build_provider_pair
from .providers.resend import ResendRelayAdapter
from .sessions.browser_sessions import profile_key
from .store import IdentityStore
from .sync.outbox import OutboxService
from .usage.ledger import UsageLedger

_T = TypeVar("_T")


@dataclass(frozen=True)
class AuthenticatedClient:
    client_id: str
    scopes: frozenset[str] = frozenset()

    def has(self, scope: str) -> bool:
        return scope in self.scopes or "identity:admin" in self.scopes


class IdentityService:
    def __init__(
        self,
        *,
        settings: IdentitySettings,
        store: IdentityStore,
        inbound_provider: InboundMailProvider | None = None,
        outbound_provider: OutboundMailProvider | None = None,
        # Legacy keyword arguments remain accepted for preserved operational
        # scripts and fixtures. They are an injection compatibility layer, not
        # the production construction path.
        stalwart: Any | None = None,
        resend: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        if inbound_provider is None:
            if stalwart is not None:
                inbound_provider = stalwart
            else:
                inbound_provider, selected_outbound = build_provider_pair(settings)
                if outbound_provider is None:
                    outbound_provider = selected_outbound
        if outbound_provider is None:
            if stalwart is not None and not isinstance(resend, ResendRelayAdapter):
                # Existing in-memory lifecycle fixtures inject one fake
                # mailbox provider for both directions. Explicit production
                # factory construction never enters this branch.
                outbound_provider = stalwart
            elif resend is not None:
                outbound_provider = resend
            else:
                _unused_inbound, outbound_provider = build_provider_pair(settings)
        assert inbound_provider is not None and outbound_provider is not None
        self.inbound_provider = inbound_provider
        self.outbound_provider = outbound_provider
        self._legacy_stalwart = stalwart or (
            inbound_provider if getattr(inbound_provider, "provider_name", "") == "stalwart" else None
        )
        self._resend = resend or (
            outbound_provider if isinstance(outbound_provider, ResendRelayAdapter) else None
        )
        self.outbox = OutboxService(store)
        self.usage = UsageLedger(store)
        self.approvals = ApprovalService(store)
        inbound_fallback = (
            "stalwart"
            if stalwart is not None and inbound_provider is stalwart
            else settings.identity_inbound_provider
        )
        outbound_fallback = (
            "stalwart"
            if stalwart is not None and outbound_provider is stalwart
            else settings.identity_outbound_provider
        )
        inbound_name = str(getattr(inbound_provider, "provider_name", inbound_fallback))
        outbound_name = str(getattr(outbound_provider, "provider_name", outbound_fallback))
        self.mailboxes = MailboxService(store=store, provider=inbound_provider, outbox=self.outbox, usage=self.usage, agent_mail_domain=settings.agent_mail_domain, quota_mb=settings.default_mailbox_quota_mb, retention_days=settings.cloudflare_mail_retention_days if inbound_name == "cloudflare" else settings.default_mail_retention_days, provider_rate_limit=settings.provider_rate_limit_per_minute, inbound_provider_name=inbound_name, outbound_provider_name=outbound_name)
        self.domains = DomainService(store=store, provider=inbound_provider, outbox=self.outbox, provider_name=inbound_name)
        self.outbound = OutboundService(
            store=store,
            provider=outbound_provider,
            approvals=self.approvals,
            usage=self.usage,
            outbox=self.outbox,
            policy=OutboundPolicy(),
            agent_domain=settings.agent_mail_domain,
            provider_rate_limit=settings.outbound_rate_limit_per_minute,
            outbound_relay_certified=settings.outbound_relay_certified,
            provider_name=outbound_name,
        )
        self.external_policy = ExternalAccountPolicy()

    @property
    def stalwart(self) -> Any | None:
        """Compatibility handle for optional Stalwart fixtures and tooling."""

        return self._legacy_stalwart

    @stalwart.setter
    def stalwart(self, provider: Any) -> None:
        self._legacy_stalwart = provider
        self.inbound_provider = provider
        self.mailboxes.provider = provider
        self.mailboxes.inbound_provider_name = str(getattr(provider, "provider_name", "stalwart"))
        self.domains.provider = provider
        self.domains.provider_name = self.mailboxes.inbound_provider_name

    @property
    def resend(self) -> Any | None:
        return self._resend

    @resend.setter
    def resend(self, provider: Any) -> None:
        self._resend = provider

    @staticmethod
    def assert_worker_access(client: AuthenticatedClient, *, actor_id: str, worker_id: UUID, allow_delegate: bool = False) -> None:
        if actor_id == str(worker_id) and (
            client.has("identity:delegate")
            or client.client_id in {str(worker_id), f"worker:{worker_id}"}
        ):
            return
        if allow_delegate and client.has("identity:delegate"):
            return
        raise PermissionError("cross-worker identity access is denied")

    @staticmethod
    def assert_admin(client: AuthenticatedClient) -> None:
        if not client.has("identity:admin"):
            raise PermissionError("identity administrator scope is required")

    async def consume_mail_provider_rate(self, worker_id: UUID) -> None:
        window = datetime.now(UTC).replace(second=0, microsecond=0)
        allowed = await self.store.consume_provider_rate(
            provider=self.mailboxes.inbound_provider_name, rate_key=f"mail-access:{worker_id}",
            window_started_at=window,
            limit=self.settings.provider_rate_limit_per_minute,
        )
        if not allowed:
            raise PermissionError("mail provider rate limit exceeded")

    def _uses_cloudflare_inbound(self) -> bool:
        return self.mailboxes.inbound_provider_name == "cloudflare"

    @staticmethod
    def _inbound_list_item(row: dict[str, Any]) -> dict[str, Any]:
        message = row.get("normalized_message") if isinstance(row.get("normalized_message"), dict) else {}
        return {
            "id": row.get("provider_message_id") or message.get("id"),
            "receivedAt": message.get("receivedAt") or row.get("received_at"),
            "from": message.get("from") or ([{"email": row.get("sender")}] if row.get("sender") else []),
            "to": message.get("to") or ([{"email": row.get("envelope_recipient")}] if row.get("envelope_recipient") else []),
            "subject": row.get("subject") or message.get("subject", ""),
            "preview": message.get("preview", ""),
            "processed": bool(row.get("processed_at")),
        }

    async def synchronize_inbound(self, *, limit: int = 1000) -> dict[str, Any]:
        """Catch up Cloudflare edge events with ack-after-commit semantics."""

        if not self._uses_cloudflare_inbound():
            return {"provider": self.mailboxes.inbound_provider_name, "cursor": 0, "next_cursor": 0, "processed": 0}
        cursor = await self.store.get_inbound_sync_cursor("cloudflare")
        response = await self.inbound_provider.list_events(after=cursor, limit=limit)
        events = response.get("events") or []
        processed = 0
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("cloudflare event is not an object")
            sequence = int(event.get("sequence", 0))
            if sequence <= cursor:
                continue
            event_id = str(event.get("event_id") or event.get("provider_event_id") or "")
            message_id = str(event.get("message_id") or "")
            identity_id = UUID(str(event.get("identity_id")))
            if not event_id or not message_id:
                raise ValueError("cloudflare event is missing its durable identifiers")
            fetched = await self.inbound_provider.fetch_message(message_id)
            if str(fetched.get("identity_id")) != str(identity_id):
                raise PermissionError("cloudflare delivery identity correlation is inconsistent")
            if event.get("worker_id") and str(fetched.get("worker_id")) != str(event.get("worker_id")):
                raise PermissionError("cloudflare delivery worker correlation is inconsistent")
            fetched_event_id = str(fetched.get("provider_event_id") or event_id)
            if fetched_event_id != event_id:
                raise PermissionError("cloudflare provider event correlation is inconsistent")
            view = fetched.get("message") if isinstance(fetched.get("message"), dict) else {}
            owner = await self.store.get_identity(UUID(str(fetched.get("worker_id"))))
            if owner is None or str(owner.get("id")) != str(identity_id):
                raise PermissionError("cloudflare delivery owner is not an AIAT identity")
            envelope_recipient = str(event.get("envelope_recipient") or view.get("envelope_recipient") or "").casefold()
            if str(owner.get("address", "")).casefold() != envelope_recipient:
                alias_owner = await self.store.get_identity_by_address(envelope_recipient)
                if alias_owner is None or str(alias_owner.get("id")) != str(identity_id) or alias_owner.get("worker_id") != owner.get("worker_id"):
                    raise PermissionError("cloudflare envelope recipient does not match the AIAT identity")
            raw_mime = fetched.get("raw_mime")
            if not isinstance(raw_mime, bytes):
                raise ValueError("cloudflare message body is not bounded bytes")
            from_values = view.get("from") if isinstance(view.get("from"), list) else []
            sender = None
            if from_values and isinstance(from_values[0], dict):
                sender = str(from_values[0].get("email") or "") or None
            recipient_values = view.get("to") if isinstance(view.get("to"), list) else []
            envelope_recipient = str(event.get("envelope_recipient") or "")
            if recipient_values and isinstance(recipient_values[0], dict):
                # The event's envelope recipient remains authoritative; the
                # parsed view is only checked for a safe display projection.
                if envelope_recipient and str(recipient_values[0].get("email") or "").casefold() != envelope_recipient.casefold():
                    raise PermissionError("cloudflare envelope recipient correlation is inconsistent")
            await self.store.upsert_inbound_message(
                identity_id=identity_id,
                provider="cloudflare",
                provider_event_id=event_id,
                provider_message_id=message_id,
                envelope_recipient=envelope_recipient,
                sender=sender,
                subject=str(view.get("subject") or "")[:998],
                received_at=datetime.fromisoformat(str(event.get("received_at") or datetime.now(UTC).isoformat()).replace("Z", "+00:00")),
                raw_object_ref=str(event.get("object_key") or f"inbound/{identity_id}/{message_id}.eml"),
                raw_mime=raw_mime,
                normalized_message=view,
            )
            await self.store.record_mail_event(
                identity_id=identity_id,
                provider_message_id=message_id,
                event_type="INBOUND_RECEIVED",
                metadata={
                    "provider": "cloudflare", "provider_event_id": event_id,
                    "sequence": sequence, "raw_object_ref": str(event.get("object_key") or ""),
                },
            )
            # The edge event is acknowledged only after both the message copy
            # and the durable normalized mail event have committed.
            await self.inbound_provider.acknowledge_event(event_id)
            cursor = await self.store.advance_inbound_sync_cursor("cloudflare", sequence)
            processed += 1
        return {
            "provider": "cloudflare", "cursor": await self.store.get_inbound_sync_cursor("cloudflare"),
            "next_cursor": int(response.get("next_cursor", cursor) or cursor), "processed": processed,
        }

    async def charged_provider_call(
        self,
        *,
        worker_id: UUID,
        operation: str,
        call: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Reserve and settle one provider API unit around an actual call."""
        hold = await self.usage.reserve(
            worker_id=worker_id,
            kind="provider_api_call",
            idempotency_key=f"provider:{operation}:{worker_id}:{uuid4()}",
        )
        try:
            result = await call()
        except Exception:
            await self.usage.release(hold["id"])
            raise
        await self.usage.commit(hold["id"])
        return result

    async def owned_identity(self, client: AuthenticatedClient, *, actor_id: str, worker_id: UUID, allow_delegate: bool = False) -> dict[str, Any]:
        """Return a mailbox only when its durable worker grant authorizes use.

        The control plane's delegated client may coordinate lifecycle work, but
        a worker-scoped caller still needs the durable grant created with its
        mailbox.  This prevents a signed worker identifier alone from becoming
        mailbox authority after a row is copied or replayed.
        """
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=allow_delegate)
        identity = await self.store.get_identity(worker_id)
        if identity is None:
            raise PermissionError("identity not found")
        if not await self.store.has_identity_access_grant(
            worker_id=worker_id, identity_id=identity["id"], grant_type="mailbox"
        ):
            raise PermissionError("durable mailbox grant is required")
        return identity

    async def provision_identity(self, client: AuthenticatedClient, *, company_id: UUID, worker_id: UUID, actor_id: str, friendly_alias: str | None, idempotency_key: str, mailbox_class: str = "permanent") -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=True)
        if mailbox_class == "temporary":
            # Create the durable pending row before any provider mutation. A
            # human decision must exist before a temporary worker receives a
            # real mailbox or an identity grant.
            identity, _created = await self.store.provision_identity(
                company_id=company_id, worker_id=worker_id,
                address=self.mailboxes.address_for(worker_id), alias=friendly_alias,
                domain=self.settings.agent_mail_domain,
                idempotency_key=idempotency_key, quota_mb=self.settings.default_mailbox_quota_mb,
                inbound_provider=self.mailboxes.inbound_provider_name,
                outbound_provider=self.outbound.provider_name,
            )
            approval = await self.store.get_approval_for_target(worker_id)
            if approval is None:
                approval = await self.approvals.request(
                    worker_id=worker_id, kind="temporary_mailbox", target_id=worker_id,
                    idempotency_key=f"temporary-mailbox:{idempotency_key}",
                )
            if str(approval.get("state")) != "APPROVED":
                identity = await self.store.set_identity_state(
                    worker_id, IdentityState.TEMPORARY_MAILBOX_APPROVAL_PENDING,
                    {"approval_id": str(approval["id"]), "mailbox_class": "temporary"},
                ) or identity
                await self.store.create_audit(
                    actor_id=actor_id, action="identity.provision",
                    target_type="worker", target_id=str(worker_id),
                    outcome="awaiting_temporary_mailbox_approval",
                    metadata={"approval_id": str(approval["id"])},
                )
                return identity
        identity, created = await self.mailboxes.provision(company_id=company_id, worker_id=worker_id, friendly_alias=friendly_alias, idempotency_key=idempotency_key)
        binding = await self.store.get_provider_binding(
            identity_id=identity["id"], direction="INBOUND",
            provider=self.mailboxes.inbound_provider_name,
        )
        if identity.get("provider_account_id") or binding:
            await self.store.create_identity_access_grant(
                worker_id=worker_id, identity_id=identity["id"],
                grant_type="mailbox", issued_by=actor_id,
            )
            await self.store.upsert_provider_binding(
                identity_id=identity["id"], direction="OUTBOUND",
                provider=self.outbound.provider_name,
                provider_reference=None,
                lifecycle_state="DISABLED",
                metadata={"default_enabled": False},
            )
        await self.store.create_audit(actor_id=actor_id, action="identity.provision", target_type="worker", target_id=str(worker_id), outcome="created" if created else "idempotent", metadata={"identity_id": str(identity["id"]), "address": identity["address"]})
        return identity

    async def verify_identity(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, provider_message_id: str) -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=True)
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=True)
        if str(identity.get("state")) != IdentityState.IDENTITY_VERIFYING:
            raise PermissionError("only a verifying email identity can be activated")
        binding = await self.store.get_provider_binding(
            identity_id=identity["id"], direction="INBOUND",
            provider=self.mailboxes.inbound_provider_name,
        )
        provider_reference = (binding or {}).get("provider_reference") or identity.get("provider_account_id")
        if not provider_reference:
            raise PermissionError("inbound provider binding is unavailable for delivery verification")
        if self._uses_cloudflare_inbound():
            await self.synchronize_inbound(limit=1000)
            message = await self.inbound_provider.verify_delivery(
                str(provider_reference), provider_message_id, identity_id=str(identity["id"])
            )
        elif hasattr(self.inbound_provider, "verify_delivery"):
            message = await self.inbound_provider.verify_delivery(
                str(provider_reference), provider_message_id, identity_id=str(identity["id"])
            )
        else:
            # Preserved legacy test doubles only expose the old read method.
            message = await self.inbound_provider.read_message(str(provider_reference), provider_message_id)  # type: ignore[attr-defined]
            messages = ((message.get("result") or {}).get("list") or []) if isinstance(message, dict) else []
            if not any(isinstance(item, dict) and str(item.get("id")) == provider_message_id for item in messages):
                raise ValueError("provider delivery evidence does not contain the requested message")
        await self.store.record_mail_event(
            identity_id=identity["id"], provider_message_id=provider_message_id,
            event_type="DELIVERY_VERIFIED",
            metadata={"provider": self.mailboxes.inbound_provider_name, "provider_correlation_id": message.get("correlation_id")},
        )
        identity = await self.mailboxes.mark_delivery_verified(worker_id, evidence={"provider": self.mailboxes.inbound_provider_name, "provider_message_id": provider_message_id, "provider_correlation_id": message.get("correlation_id")})
        await self.store.create_audit(actor_id=actor_id, action="identity.verify", target_type="worker", target_id=str(worker_id), outcome="verified", metadata={"identity_id": str(identity["id"])})
        return identity

    async def suspend_identity(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, reason: str) -> dict[str, Any] | None:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=True)
        try:
            identity = await self.mailboxes.suspend(worker_id, reason=reason)
        except Exception as exc:
            await self.store.revoke_browser_sessions(worker_id)
            await self.store.suspend_external_accounts(worker_id)
            await self.store.create_audit(
                actor_id=actor_id,
                action="identity.suspend",
                target_type="worker",
                target_id=str(worker_id),
                outcome="local_revoked_provider_pending",
                metadata={"reason": reason, "failure_code": type(exc).__name__},
            )
            raise
        await self.store.revoke_browser_sessions(worker_id)
        await self.store.suspend_external_accounts(worker_id)
        await self.store.create_audit(actor_id=actor_id, action="identity.suspend", target_type="worker", target_id=str(worker_id), outcome="suspended" if identity else "not_found", metadata={"reason": reason})
        return identity

    async def archive_identity(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, reason: str) -> dict[str, Any] | None:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=True)
        try:
            identity = await self.mailboxes.archive(worker_id)
        except Exception as exc:
            await self.store.revoke_browser_sessions(worker_id)
            await self.store.suspend_external_accounts(worker_id)
            await self.store.create_audit(
                actor_id=actor_id,
                action="identity.archive",
                target_type="worker",
                target_id=str(worker_id),
                outcome="local_revoked_provider_pending",
                metadata={"reason": reason, "failure_code": type(exc).__name__},
            )
            raise
        await self.store.revoke_browser_sessions(worker_id)
        await self.store.suspend_external_accounts(worker_id)
        await self.store.create_audit(
            actor_id=actor_id, action="identity.archive", target_type="worker",
            target_id=str(worker_id), outcome="archived" if identity else "not_found",
            metadata={"reason": reason},
        )
        return identity

    async def mail_list(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, limit: int, query: str | None = None) -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        if identity is None or identity.get("state") not in {IdentityState.IDENTITY_ACTIVE, IdentityState.IDENTITY_VERIFYING, "IDENTITY_ACTIVE", "IDENTITY_VERIFYING"}:
            raise PermissionError("mailbox is not available")
        await self.consume_mail_provider_rate(worker_id)
        if self._uses_cloudflare_inbound():
            await self.synchronize_inbound(limit=1000)
            rows = await self.store.list_inbound_messages(identity_id=identity["id"], limit=limit, query=query)
            result = {
                "provider": "cloudflare",
                "result": {
                    "list": [self._inbound_list_item(row) for row in rows],
                    "ids": [str(row.get("provider_message_id")) for row in rows],
                    "limit": limit,
                    "query": query,
                },
            }
            await self.store.create_audit(actor_id=actor_id, action="mail.search" if query else "mail.list", target_type="email_identity", target_id=str(identity["id"]), outcome="ok", metadata={"provider": "cloudflare", "limit": limit, "query_present": bool(query)})
            return result
        if not identity.get("provider_account_id"):
            raise RuntimeError("inbound provider binding is not available")
        result = await self.charged_provider_call(
            worker_id=worker_id,
            operation="mail_search" if query else "mail_list",
            call=lambda: self.inbound_provider.list_messages(  # type: ignore[attr-defined]
                str(identity["provider_account_id"]), limit=limit, query=query
            ),
        )
        await self.store.create_audit(actor_id=actor_id, action="mail.search" if query else "mail.list", target_type="mailbox", target_id=str(identity["id"]), outcome="ok", metadata={"limit": limit, "query_present": bool(query)})
        return result

    async def mail_read(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, message_id: str) -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        if identity.get("state") not in {IdentityState.IDENTITY_ACTIVE, IdentityState.IDENTITY_VERIFYING, "IDENTITY_ACTIVE", "IDENTITY_VERIFYING"}:
            raise PermissionError("email identity is not available")
        await self.consume_mail_provider_rate(worker_id)
        if self._uses_cloudflare_inbound():
            await self.synchronize_inbound(limit=1000)
            row = await self.store.get_inbound_message(identity_id=identity["id"], message_id=message_id)
            message = row.get("normalized_message") if row else None
            result = {"provider": "cloudflare", "result": {"list": [message] if isinstance(message, dict) else []}}
            if row:
                await self.store.record_mail_event(identity_id=identity["id"], provider_message_id=message_id, event_type="READ", metadata={"provider": "cloudflare"})
            await self.store.create_audit(actor_id=actor_id, action="mail.read", target_type="email_identity", target_id=str(identity["id"]), outcome="ok" if row else "not_found", metadata={"message_id": message_id})
            return result
        if not identity.get("provider_account_id"):
            raise PermissionError("inbound provider binding is not available")
        result = await self.charged_provider_call(
            worker_id=worker_id,
            operation="mail_read",
            call=lambda: self.inbound_provider.read_message(  # type: ignore[attr-defined]
                str(identity["provider_account_id"]), message_id
            ),
        )
        await self.store.record_mail_event(
            identity_id=identity["id"], provider_message_id=message_id,
            event_type="READ", metadata={"provider_correlation_id": result.get("correlation_id")},
        )
        await self.store.create_audit(actor_id=actor_id, action="mail.read", target_type="mailbox", target_id=str(identity["id"]), outcome="ok", metadata={"message_id": message_id})
        return result

    async def mutate_mail_message(
        self,
        client: AuthenticatedClient,
        *,
        worker_id: UUID,
        actor_id: str,
        message_id: str,
        operation: str,
    ) -> dict[str, Any]:
        identity = await self.owned_identity(
            client, actor_id=actor_id, worker_id=worker_id
        )
        if str(identity.get("state")) != "IDENTITY_ACTIVE":
            raise PermissionError("active email identity is required")
        await self.consume_mail_provider_rate(worker_id)
        if self._uses_cloudflare_inbound():
            await self.synchronize_inbound(limit=1000)
            row = await self.store.get_inbound_message(identity_id=identity["id"], message_id=message_id)
            if row is None:
                raise PermissionError("message ownership denied")
            binding = await self.store.get_provider_binding(identity_id=identity["id"], direction="INBOUND", provider="cloudflare")
            provider_reference = str((binding or {}).get("provider_reference") or "")
            if operation == "mark_processed":
                result = await self.charged_provider_call(worker_id=worker_id, operation="mail.mark_processed", call=lambda: self.inbound_provider.mark_processed(provider_reference, message_id))  # type: ignore[attr-defined]
                await self.store.update_inbound_message(identity_id=identity["id"], message_id=message_id, processed_at=datetime.now(UTC))
                event_type = "PROCESSED"
                audit_action = "mail.mark_processed"
            elif operation == "delete":
                result = await self.charged_provider_call(worker_id=worker_id, operation="mail.delete", call=lambda: self.inbound_provider.delete_message(provider_reference, message_id))
                await self.store.update_inbound_message(identity_id=identity["id"], message_id=message_id, deleted_at=datetime.now(UTC))
                event_type = "DELETED"
                audit_action = "mail.delete"
            else:
                raise ValueError("unknown mail mutation is denied")
            await self.store.record_mail_event(identity_id=identity["id"], provider_message_id=message_id, event_type=event_type, metadata={"provider": "cloudflare", "provider_correlation_id": result.get("correlation_id")})
            await self.store.create_audit(actor_id=actor_id, action=audit_action, target_type="email_identity", target_id=str(identity["id"]), outcome="ok", metadata={"message_id": message_id})
            return result
        if not identity.get("provider_account_id"):
            raise PermissionError("inbound provider binding is not available")
        if operation == "mark_processed":
            call = lambda: self.inbound_provider.mark_processed(  # type: ignore[attr-defined]  # noqa: E731
                str(identity["provider_account_id"]), message_id
            )
            audit_action = "mail.mark_processed"
        elif operation == "delete":
            call = lambda: self.inbound_provider.delete_message(  # type: ignore[attr-defined]  # noqa: E731
                str(identity["provider_account_id"]), message_id
            )
            audit_action = "mail.delete"
        else:
            raise ValueError("unknown mail mutation is denied")
        result = await self.charged_provider_call(
            worker_id=worker_id, operation=audit_action, call=call
        )
        await self.store.record_mail_event(
            identity_id=identity["id"], provider_message_id=message_id,
            event_type="PROCESSED" if operation == "mark_processed" else "DELETED",
            metadata={"provider_correlation_id": result.get("correlation_id")},
        )
        await self.store.create_audit(
            actor_id=actor_id, action=audit_action, target_type="mailbox",
            target_id=str(identity["id"]), outcome="ok",
            metadata={"message_id": message_id},
        )
        return result

    async def record_verification_extraction(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, message_id: str, code: str | None = None, link: str | None = None) -> None:
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        # Verification codes have very low entropy; an ordinary SHA-256 digest
        # would be recoverable by brute force if the database were copied. Use
        # the fail-closed service secret as an HMAC pepper instead.
        pepper = (
            self.settings.identity_service_secret
            or "aiat-development-verification-hmac-only"
        ).encode()
        code_hash = hmac.new(pepper, code.encode(), hashlib.sha256).hexdigest() if code else None
        link_hash = hmac.new(pepper, link.encode(), hashlib.sha256).hexdigest() if link else None
        await self.store.record_verification_transaction(
            identity_id=identity["id"], provider_message_id=message_id,
            idempotency_key=f"verification:{identity['id']}:{message_id}",
            code_hash=code_hash, link_hash=link_hash,
            state="EXTRACTED" if code or link else "NOT_FOUND",
        )
        if self._uses_cloudflare_inbound() and (code or link) and hasattr(self.inbound_provider, "protect_message"):
            # The local transaction is authoritative. Edge protection is a
            # best-effort retention extension so an active recovery flow is
            # not removed by the short processed-mail cleanup window.
            try:
                binding = await self.store.get_provider_binding(
                    identity_id=identity["id"], direction="INBOUND", provider="cloudflare"
                )
                provider_reference = str((binding or {}).get("provider_reference") or "")
                if not provider_reference:
                    raise PermissionError("inbound provider binding is unavailable for retention protection")
                await self.inbound_provider.protect_message(  # type: ignore[attr-defined]
                    provider_reference,
                    message_id,
                    until=(datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat(),
                )
            except Exception as exc:
                await self.store.create_audit(
                    actor_id=actor_id, action="mail.protect_verification",
                    target_type="email_identity", target_id=str(identity["id"]),
                    outcome="edge_protection_pending", metadata={"failure_code": type(exc).__name__},
                )

    async def wait_for_verification(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, sender_domain: str | None, timeout_seconds: int) -> dict[str, Any] | None:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        if identity.get("state") not in {IdentityState.IDENTITY_ACTIVE, IdentityState.IDENTITY_VERIFYING, "IDENTITY_ACTIVE", "IDENTITY_VERIFYING"}:
            raise PermissionError("email identity is not available")
        await self.consume_mail_provider_rate(worker_id)
        if self._uses_cloudflare_inbound():
            del timeout_seconds  # Edge synchronization is bounded by one pull; callers retry with their own deadline.
            await self.synchronize_inbound(limit=1000)
            rows = await self.store.list_inbound_messages(identity_id=identity["id"], limit=100, query=None)
            result = None
            sender_needle = str(sender_domain or "").strip().casefold()
            for row in rows:
                sender = str(row.get("sender") or "").casefold()
                if sender_needle and not sender.endswith("@" + sender_needle):
                    continue
                result = {"provider": "cloudflare", "result": {"list": [row.get("normalized_message", {})]}}
                provider_message_id = str(row.get("provider_message_id") or "")
                if provider_message_id:
                    await self.store.record_mail_event(identity_id=identity["id"], provider_message_id=provider_message_id, event_type="VERIFICATION_RECEIVED", metadata={"provider": "cloudflare", "sender_domain": sender_domain})
                break
            await self.store.create_audit(actor_id=actor_id, action="mail.wait_for_verification", target_type="email_identity", target_id=str(identity["id"]), outcome="found" if result else "timeout", metadata={"provider": "cloudflare", "sender_domain": sender_domain})
            return result
        if not identity.get("provider_account_id"):
            raise PermissionError("inbound provider binding is not available")
        result = await self.charged_provider_call(
            worker_id=worker_id,
            operation="mail_wait",
            call=lambda: self.inbound_provider.wait_for_message(  # type: ignore[attr-defined]
                str(identity["provider_account_id"]),
                sender_domain=sender_domain,
                timeout_seconds=timeout_seconds,
            ),
        )
        if result:
            messages = (result.get("result") or {}).get("list") or []
            provider_message_id = next((str(item["id"]) for item in messages if isinstance(item, dict) and item.get("id")), None)
            if provider_message_id:
                await self.store.record_mail_event(
                    identity_id=identity["id"], provider_message_id=provider_message_id,
                    event_type="VERIFICATION_RECEIVED",
                    metadata={"sender_domain": sender_domain, "provider_correlation_id": result.get("correlation_id")},
                )
        await self.store.create_audit(actor_id=actor_id, action="mail.wait_for_verification", target_type="mailbox", target_id=str(identity["id"]), outcome="found" if result else "timeout", metadata={"sender_domain": sender_domain})
        return result

    async def request_outbound(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, recipients: list[str], subject: str, body: str, recipient_class: str, idempotency_key: str, content_type: str = "text/plain") -> tuple[dict[str, Any], dict[str, Any]]:
        await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        request, approval, _created = await self.outbound.request(worker_id=worker_id, recipients=recipients, subject=subject, body=body, recipient_class=recipient_class, idempotency_key=idempotency_key, content_type=content_type)
        await self.store.create_audit(actor_id=actor_id, action="mail.send_request", target_type="outbound_mail_request", target_id=str(request["id"]), outcome="pending_approval", metadata={"approval_id": str(approval["id"]), "recipient_count": len(recipients)})
        return request, approval

    async def send_approved(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, request_id: UUID, idempotency_key: str, trace_id: str | None = None) -> dict[str, Any]:
        await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        request = await self.outbound.send_approved(worker_id=worker_id, outbound_request_id=request_id, idempotency_key=idempotency_key, trace_id=trace_id)
        await self.store.create_audit(actor_id=actor_id, action="mail.send_approved", target_type="outbound_mail_request", target_id=str(request_id), outcome="submitted", metadata={"provider_correlation_id": request.get("provider_correlation_id")})
        return request

    async def record_provider_webhook(
        self,
        client: AuthenticatedClient,
        *,
        provider: str,
        payload: dict[str, Any],
        actor_id: str,
        event_id: str | None = None,
        signature_verified: bool = False,
        worker_id: UUID | None = None,
        outbound_request_id: UUID | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one verified provider event without retaining its body.

        Provider-specific signature verification belongs to the ingress
        adapter.  This signed control-plane endpoint accepts only its explicit
        boolean result; an unverified webhook can never become an AIAT
        observation.
        """

        if not client.has("identity:delegate"):
            raise PermissionError("provider mail-edge projection requires delegated scope")
        if not signature_verified:
            raise PermissionError("verified provider signature is required")
        return await self._persist_provider_webhook(
            provider=provider,
            payload=payload,
            actor_id=actor_id,
            event_id=event_id,
            worker_id=worker_id,
            outbound_request_id=outbound_request_id,
            trace_id=trace_id,
            span_id=span_id,
        )

    async def record_verified_provider_webhook(
        self,
        *,
        provider: str,
        payload: dict[str, Any],
        actor_id: str,
        event_id: str | None = None,
        worker_id: UUID | None = None,
        outbound_request_id: UUID | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an event after a provider adapter verified its raw body."""

        return await self._persist_provider_webhook(
            provider=provider,
            payload=payload,
            actor_id=actor_id,
            event_id=event_id,
            worker_id=worker_id,
            outbound_request_id=outbound_request_id,
            trace_id=trace_id,
            span_id=span_id,
        )

    async def _persist_provider_webhook(
        self,
        *,
        provider: str,
        payload: dict[str, Any],
        actor_id: str,
        event_id: str | None,
        worker_id: UUID | None,
        outbound_request_id: UUID | None,
        trace_id: str | None,
        span_id: str | None,
    ) -> dict[str, Any]:
        observation = normalize_provider_webhook(
            provider,
            payload,
            event_id=event_id,
            signature_verified=True,
            worker_id=str(worker_id) if worker_id else None,
            outbound_request_id=str(outbound_request_id) if outbound_request_id else None,
            trace_id=trace_id,
            span_id=span_id,
        )

        target: dict[str, Any] | None = None
        if outbound_request_id is not None:
            target = await self.store.get_outbound_request_metadata(outbound_request_id)
            if target is None:
                raise ValueError("outbound request for provider event was not found")
        elif observation.provider_message_ref:
            target = await self.store.find_outbound_request_by_provider_message_id(
                observation.provider_message_ref
            )
        if target is not None:
            target_worker = str(target.get("worker_id") or "")
            if worker_id is not None and target_worker != str(worker_id):
                raise PermissionError("provider event worker correlation is inconsistent")
            target_trace = str(target.get("trace_id") or "") or None
            if target_trace and observation.trace_id and target_trace != observation.trace_id:
                raise ValueError("provider event trace correlation is inconsistent")
            stored_message_ref = str(target.get("provider_message_id") or "")
            if (
                stored_message_ref
                and observation.provider_message_ref
                and stored_message_ref != observation.provider_message_ref
            ):
                raise ValueError("provider event message correlation is inconsistent")
            observation = observation.model_copy(
                update={
                    "worker_id": target_worker or observation.worker_id,
                    "outbound_request_id": str(target["id"]),
                    "provider_message_ref": observation.provider_message_ref or stored_message_ref or None,
                    "trace_id": observation.trace_id or target_trace,
                    "span_id": observation.span_id or target.get("span_id"),
                }
            )
        row = await self.store.record_mail_edge_observation(observation)
        await self.store.create_audit(
            actor_id=actor_id,
            action="mail.provider_event",
            target_type="mail_edge_observation",
            target_id=str(row["id"]),
            outcome=observation.event_type,
            metadata={
                "provider": observation.provider,
                "event_id": observation.event_id,
                "source": observation.source,
                "signature_verified": observation.signature_verified,
            },
        )
        return row

    async def cancel_queued_outbound(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, request_id: UUID) -> dict[str, Any]:
        await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        request = await self.store.get_outbound_request(request_id)
        if request is None or request.get("worker_id") != worker_id:
            raise PermissionError("outbound request ownership denied")
        provider_message_id = request.get("provider_message_id")
        if not provider_message_id:
            raise ValueError("outbound message has not been queued")
        identity = await self.store.get_identity(worker_id)
        if identity is None:
            raise PermissionError("email identity is unavailable")
        binding = await self.store.get_provider_binding(
            identity_id=identity["id"], direction="OUTBOUND", provider=self.outbound.provider_name
        )
        provider_reference = (binding or {}).get("provider_reference") or identity.get("provider_account_id")
        async def cancel() -> dict[str, Any]:
            if hasattr(self.outbound_provider, "cancel_message"):
                return await self.outbound_provider.cancel_message(  # type: ignore[attr-defined]
                    str(provider_message_id),
                    provider_reference=str(provider_reference) if provider_reference else None,
                )
            legacy = getattr(self.inbound_provider, "cancel_queued_message", None)
            if callable(legacy) and provider_reference:
                return await legacy(str(provider_reference), str(provider_message_id))
            raise ValueError("selected outbound provider does not support cancellation")

        result = await self.charged_provider_call(
            worker_id=worker_id, operation="mail_cancel_queued", call=cancel
        )
        updated = await self.store.update_outbound_request(request_id, state="CANCELLED", provider_correlation_id=result.get("correlation_id"))
        if updated is None:
            raise RuntimeError("outbound request vanished")
        await self.store.create_audit(actor_id=actor_id, action="mail.cancel_queued", target_type="outbound_mail_request", target_id=str(request_id), outcome="cancelled", metadata={"provider_correlation_id": result.get("correlation_id")})
        return updated

    async def decide_approval(self, client: AuthenticatedClient, *, approval_id: UUID, actor_id: str, approved: bool, reason: str) -> dict[str, Any] | None:
        self.assert_admin(client)
        decision = await self.approvals.decide(approval_id, actor_id=actor_id, approved=approved, reason=reason)
        if decision:
            if str(decision.get("kind")) == "external_account":
                # A service account remains default-deny until an explicit
                # recorded approval activates it. A rejection leaves it
                # suspended, rather than allowing a retry to revive it.
                await self.store.update_external_account(
                    decision["target_id"],
                    ExternalAccountState.ACTIVE if approved else ExternalAccountState.SUSPENDED,
                )
            elif str(decision.get("kind")) == "external_credential_rotation" and approved:
                account = await self.store.get_external_account(decision["target_id"])
                if account is not None:
                    await self.store.bind_external_account(
                        account["id"], approval_id=decision["id"],
                        credential_ref=f"external-credential-{uuid4().hex}",
                    )
                    # A credential rotation invalidates every live browser
                    # authorization for this worker. New sessions are issued
                    # only after the local broker observes the new reference.
                    await self.store.revoke_browser_sessions(account["worker_id"])
            elif str(decision.get("kind")) == "external_account_close" and approved:
                account = await self.store.get_external_account(decision["target_id"])
                if account is not None:
                    await self.store.update_external_account(
                        account["id"], ExternalAccountState.CLOSED
                    )
                    revoked_sessions = await self.store.revoke_browser_sessions(account["worker_id"])
                    await self.store.create_audit(
                        actor_id=actor_id,
                        action="identity.external.close",
                        target_type="external_account",
                        target_id=str(account["id"]),
                        outcome="CLOSED",
                        metadata={"revoked_browser_sessions": revoked_sessions, "approval_id": str(approval_id)},
                    )
            await self.store.create_audit(actor_id=actor_id, action="identity.approval.decide", target_type="identity_approval", target_id=str(approval_id), outcome="approved" if approved else "rejected", metadata={})
        return decision

    async def signup_external_account(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, service: str, service_category: str, idempotency_key: str, email_identity_id: UUID | None) -> dict[str, Any]:
        identity = await self.owned_identity(
            client, actor_id=actor_id, worker_id=worker_id
        )
        if str(identity.get("state")) != "IDENTITY_ACTIVE":
            raise PermissionError("active worker email identity is required")
        if email_identity_id is not None and email_identity_id != identity["id"]:
            raise PermissionError("external account email identity ownership denied")
        disposition = self.external_policy.disposition(service_category)
        hold = await self.usage.reserve(
            worker_id=worker_id, kind="signup_attempt",
            idempotency_key=f"hold:{idempotency_key}",
        )
        try:
            credential_ref = "external-credential-" + hashlib.sha256(
                f"{worker_id}:{service.strip().lower()}".encode()
            ).hexdigest()[:32]
            proposed_account_id = uuid4()
            approval = await self.approvals.request(
                worker_id=worker_id, kind="external_account",
                target_id=proposed_account_id,
                idempotency_key=f"approval:{idempotency_key}",
            )
            # The approval is created first and its target becomes the account
            # primary key. This lets the database require approval_id NOT NULL
            # without leaving a crash window containing an unapproved account.
            account_id = UUID(str(approval["target_id"]))
            account, created = await self.store.create_external_account(
                account_id=account_id,
                worker_id=worker_id,
                service=service,
                service_category=service_category,
                email_identity_id=identity["id"],
                approval_id=approval["id"],
                credential_ref=credential_ref,
                browser_profile_ref=profile_key(worker_id, service),
                idempotency_key=idempotency_key,
            )
            if disposition == "allowed" and str(approval.get("state")) == "PENDING":
                approval = await self.approvals.decide(
                    approval["id"], actor_id="identity-policy",
                    approved=True,
                    reason="service category is allow-listed",
                ) or approval
            if disposition == "allowed" and str(approval.get("state")) == "APPROVED":
                account = await self.store.update_external_account(
                    account["id"], ExternalAccountState.ACTIVE
                ) or account
            await self.usage.commit(hold["id"])
        except Exception:
            await self.usage.release(hold["id"])
            raise
        await self.store.create_audit(actor_id=actor_id, action="identity.external.signup_request", target_type="external_account", target_id=str(account["id"]), outcome=disposition if created else "idempotent", metadata={"service": service, "service_category": service_category, "approval_id": str(approval["id"])})
        return account

    async def external_account_status(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, account_id: UUID) -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        account = await self.store.get_external_account(account_id)
        if account is None or account.get("worker_id") != worker_id:
            raise PermissionError("external account ownership denied")
        await self.store.create_audit(
            actor_id=actor_id, action="identity.external.get_status",
            target_type="external_account", target_id=str(account_id),
            outcome="ok", metadata={},
        )
        return account

    async def set_external_account_state(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, account_id: UUID, state: ExternalAccountState) -> dict[str, Any]:
        if state is ExternalAccountState.CLOSED:
            raise PermissionError("closing an external account requires a human approval")
        account = await self.external_account_status(client, worker_id=worker_id, actor_id=actor_id, account_id=account_id)
        updated = await self.store.update_external_account(account_id, state)
        if updated is None:
            raise RuntimeError("external account vanished")
        revoked_sessions = 0
        if state in {ExternalAccountState.SUSPENDED, ExternalAccountState.CLOSED}:
            # A previously issued browser lease must not create a grace window
            # after account suspension. Revoking the worker's sessions makes
            # every outstanding one-use lease unusable immediately.
            revoked_sessions = await self.store.revoke_browser_sessions(worker_id)
        await self.store.create_audit(
            actor_id=actor_id,
            action=f"identity.external.{state.lower()}",
            target_type="external_account",
            target_id=str(account["id"]),
            outcome=state.value,
            metadata={"revoked_browser_sessions": revoked_sessions},
        )
        return updated

    async def request_external_account_close(
        self,
        client: AuthenticatedClient,
        *,
        worker_id: UUID,
        actor_id: str,
        account_id: UUID,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create the explicit human gate for irreversible account closure."""

        self.external_policy.action_rule("close")
        account = await self.external_account_status(
            client, worker_id=worker_id, actor_id=actor_id, account_id=account_id
        )
        if str(account.get("state")) == ExternalAccountState.CLOSED:
            return {"account_id": str(account_id), "state": ExternalAccountState.CLOSED, "approval": None}
        approval = await self.approvals.request(
            worker_id=worker_id,
            kind="external_account_close",
            target_id=account_id,
            idempotency_key=f"external-account-close:{idempotency_key}",
        )
        await self.store.create_audit(
            actor_id=actor_id,
            action="identity.external.close_request",
            target_type="external_account",
            target_id=str(account_id),
            outcome="pending_approval",
            metadata={"approval_id": str(approval["id"]), "risk": "high"},
        )
        return {"account_id": str(account_id), "state": "PENDING_APPROVAL", "approval": approval}

    def external_account_action_catalog(self) -> dict[str, object]:
        return self.external_policy.action_catalog()

    async def request_external_credential_rotation(
        self,
        client: AuthenticatedClient,
        *,
        worker_id: UUID,
        actor_id: str,
        account_id: UUID,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.external_policy.action_rule("rotate_credentials")
        account = await self.external_account_status(
            client, worker_id=worker_id, actor_id=actor_id,
            account_id=account_id,
        )
        if str(account.get("state")) != "ACTIVE":
            raise PermissionError("only an active external account can rotate credentials")
        approval = await self.approvals.request(
            worker_id=worker_id, kind="external_credential_rotation",
            target_id=account_id,
            idempotency_key=f"credential-rotation:{idempotency_key}",
        )
        await self.store.create_audit(
            actor_id=actor_id,
            action="identity.external.rotate_credentials",
            target_type="external_account", target_id=str(account_id),
            outcome="pending_approval",
            metadata={"approval_id": str(approval["id"])},
        )
        return {"account_id": str(account_id), "approval": approval, "rotation": "PENDING_APPROVAL"}

    async def create_browser_session(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, service: str, external_account_id: UUID, idempotency_key: str) -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        account = await self.store.get_external_account(external_account_id)
        if (
            account is None
            or account.get("worker_id") != worker_id
            or str(account.get("state")) != ExternalAccountState.ACTIVE
            or str(account.get("service", "")).strip().lower() != service.strip().lower()
            or not account.get("approval_id")
            or not account.get("credential_ref")
        ):
            raise PermissionError("external account ownership, approval, or status denied")
        session, _created = await self.store.create_browser_session(worker_id=worker_id, service=service, external_account_id=external_account_id, profile_ref=profile_key(worker_id, service), idempotency_key=idempotency_key)
        await self.store.create_audit(actor_id=actor_id, action="identity.session.create", target_type="browser_auth_session", target_id=str(session["id"]), outcome="active", metadata={"service": service})
        # `profile_ref` and id are opaque handles.  No cookie, profile path,
        # lease token or browser storage is returned over this API.
        return session

    async def issue_browser_session_lease(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, session_id: UUID) -> dict[str, Any]:
        if not client.has("identity:browser-broker"):
            raise PermissionError("browser broker scope is required")
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        session = await self.store.get_browser_session(session_id)
        if session is None or session.get("worker_id") != worker_id or session.get("state") != "ACTIVE":
            raise PermissionError("browser session ownership or status denied")
        external_account_id = session.get("external_account_id")
        if not external_account_id:
            raise PermissionError("browser session has no governed external account")
        account = await self.store.get_external_account(external_account_id)
        if account is None or account.get("worker_id") != worker_id or str(account.get("state")) != "ACTIVE":
            raise PermissionError("external account ownership or status denied")
        scope = f"browser:session:{session_id}"
        lease = issue_opaque_lease(session_id=str(session_id), scope=scope)
        await self.store.create_credential_lease(
            external_account_id=external_account_id, worker_id=worker_id,
            lease_hash=str(lease["lease_hash"]), scope=scope,
            expires_at=lease["expires_at"],
        )
        await self.store.create_audit(
            actor_id=actor_id, action="identity.session.lease",
            target_type="browser_auth_session", target_id=str(session_id),
            outcome="issued", metadata={"scope": scope},
        )
        return {"session_id": str(session_id), "lease_token": lease["lease_token"], "expires_at": lease["expires_at"]}

    async def use_browser_session(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, session_id: UUID, lease_token: str | None) -> dict[str, Any]:
        if not client.has("identity:browser-broker"):
            raise PermissionError("browser broker scope is required")
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        session = await self.store.get_browser_session(session_id)
        if session is None or session.get("worker_id") != worker_id or session.get("state") != "ACTIVE":
            raise PermissionError("browser session ownership or status denied")
        external_account_id = session.get("external_account_id")
        if not external_account_id or not lease_token:
            raise PermissionError("short-lived browser credential lease is required")
        lease_hash = hashlib.sha256(lease_token.encode()).hexdigest()
        if not await self.store.consume_credential_lease(
            external_account_id=external_account_id, worker_id=worker_id,
            lease_hash=lease_hash, scope=f"browser:session:{session_id}",
        ):
            raise PermissionError("browser credential lease is invalid, expired, or consumed")
        hold = await self.usage.reserve(
            worker_id=worker_id, kind="browser_minute",
            idempotency_key=f"browser-use:{session_id}:{lease_hash}",
        )
        await self.usage.commit(hold["id"])
        await self.store.create_audit(actor_id=actor_id, action="identity.session.use", target_type="browser_auth_session", target_id=str(session_id), outcome="granted", metadata={})
        return session

    async def revoke_browser_session(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str) -> int:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        count = await self.store.revoke_browser_sessions(worker_id)
        await self.store.create_audit(actor_id=actor_id, action="identity.session.revoke", target_type="worker", target_id=str(worker_id), outcome="revoked", metadata={"count": count})
        return count

    async def health(self) -> dict[str, Any]:
        # Health deliberately contains no connection string, credential state,
        # relay credential, or provider secret.
        return {
            "status": "ok", "service": "identity-service",
            "direct_mx_outbound_enabled": self.settings.direct_mx_outbound_enabled,
            "inbound_provider": self.mailboxes.inbound_provider_name,
            "outbound_provider": self.outbound.provider_name,
            "outbound_relay_provider": self.settings.outbound_relay_provider,
        }

    async def dashboard_resource(self, resource: str) -> list[dict[str, Any]]:
        rows = await self.store.dashboard_rows(resource)
        if resource != "mail-relay":
            return rows
        health: dict[str, Any] = {
            "record_type": "relay_health",
            "inbound_provider": self.mailboxes.inbound_provider_name,
            "outbound_provider": self.outbound.provider_name,
            "relay_provider": self.settings.outbound_relay_provider,
            "relay_host": self.settings.outbound_relay_host,
            "relay_port": self.settings.outbound_relay_port,
            "relay_tls_mode": self.settings.outbound_relay_tls_mode,
            "direct_mx_outbound_enabled": False,
        }
        try:
            health[f"{self.mailboxes.inbound_provider_name}_health"] = (await self.inbound_provider.health_check()).get("healthy", True)
        except Exception as exc:
            health[f"{self.mailboxes.inbound_provider_name}_health"] = "unavailable"
            health[f"{self.mailboxes.inbound_provider_name}_error"] = type(exc).__name__
        if str(self.settings.outbound_relay_provider).strip().lower() in {"disabled", "none", "off"}:
            health["resend_health"] = "disabled"
        else:
            try:
                health["resend_health"] = (await self.outbound_provider.health_check()).get(
                    "valid", False
                )
            except Exception as exc:
                health["resend_health"] = "unavailable"
                health["resend_error"] = type(exc).__name__
        safe_attempts: list[dict[str, Any]] = []
        for stored in rows:
            row = dict(stored)
            account_id = row.pop("provider_account_id", None)
            provider_message_id = row.get("provider_message_id")
            if account_id and provider_message_id and hasattr(self.inbound_provider, "get_outbound_queue_status"):
                try:
                    status = await self.inbound_provider.get_outbound_queue_status(  # type: ignore[attr-defined]
                        str(account_id), str(provider_message_id)
                    )
                    row[f"{self.mailboxes.inbound_provider_name}_queue_state"] = status.get("result")
                except Exception as exc:
                    row["stalwart_queue_state"] = "unavailable"
                    row["queue_status_error"] = type(exc).__name__
            safe_attempts.append(row)
        return [health, *safe_attempts]
