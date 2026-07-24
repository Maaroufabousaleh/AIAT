"""Application service coordinating identity policy, providers, and audit."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID, uuid4

from .approvals.service import ApprovalService
from .config import IdentitySettings
from .credentials.leases import issue_opaque_lease
from .domains.service import DomainService
from .external_accounts.service import ExternalAccountPolicy
from .mailboxes.service import MailboxService
from .models import ExternalAccountState, IdentityState
from .outbound.policy import OutboundPolicy
from .outbound.service import OutboundService
from .providers.resend import ResendRelayAdapter
from .providers.stalwart import StalwartAdapter
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
    def __init__(self, *, settings: IdentitySettings, store: IdentityStore, stalwart: StalwartAdapter, resend: ResendRelayAdapter) -> None:
        self.settings = settings
        self.store = store
        self.outbox = OutboxService(store)
        self.usage = UsageLedger(store)
        self.approvals = ApprovalService(store)
        self.mailboxes = MailboxService(store=store, provider=stalwart, outbox=self.outbox, usage=self.usage, agent_mail_domain=settings.agent_mail_domain, quota_mb=settings.default_mailbox_quota_mb, retention_days=settings.default_mail_retention_days, provider_rate_limit=settings.provider_rate_limit_per_minute)
        self.domains = DomainService(store=store, provider=stalwart, outbox=self.outbox)
        self.outbound = OutboundService(store=store, provider=stalwart, approvals=self.approvals, usage=self.usage, outbox=self.outbox, policy=OutboundPolicy(), agent_domain=settings.agent_mail_domain, provider_rate_limit=settings.outbound_rate_limit_per_minute)
        self.stalwart = stalwart
        self.resend = resend
        self.external_policy = ExternalAccountPolicy()

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
            provider="stalwart", rate_key=f"mail-access:{worker_id}",
            window_started_at=window,
            limit=self.settings.provider_rate_limit_per_minute,
        )
        if not allowed:
            raise PermissionError("mail provider rate limit exceeded")

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
        if identity.get("provider_account_id"):
            await self.store.create_identity_access_grant(
                worker_id=worker_id, identity_id=identity["id"],
                grant_type="mailbox", issued_by=actor_id,
            )
        await self.store.create_audit(actor_id=actor_id, action="identity.provision", target_type="worker", target_id=str(worker_id), outcome="created" if created else "idempotent", metadata={"identity_id": str(identity["id"]), "address": identity["address"]})
        return identity

    async def verify_identity(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, provider_message_id: str) -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=True)
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id, allow_delegate=True)
        if identity is None or not identity.get("provider_account_id"):
            raise PermissionError("mailbox is not available for delivery verification")
        if str(identity.get("state")) != IdentityState.IDENTITY_VERIFYING:
            raise PermissionError("only a verifying mailbox can be activated")
        # A real JMAP read confirms that a message was persisted by Stalwart;
        # no caller-provided assertion can activate a mailbox on its own.
        message = await self.stalwart.read_message(str(identity["provider_account_id"]), provider_message_id)
        messages = ((message.get("result") or {}).get("list") or []) if isinstance(message, dict) else []
        if not any(isinstance(item, dict) and str(item.get("id")) == provider_message_id for item in messages):
            raise ValueError("JMAP delivery evidence does not contain the requested message")
        await self.store.record_mail_event(
            identity_id=identity["id"], provider_message_id=provider_message_id,
            event_type="DELIVERY_VERIFIED",
            metadata={"provider_correlation_id": message.get("correlation_id")},
        )
        identity = await self.mailboxes.mark_delivery_verified(worker_id, evidence={"provider_message_id": provider_message_id, "provider_correlation_id": message.get("correlation_id")})
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
        if not identity.get("provider_account_id"):
            raise RuntimeError("mailbox provider account is not available")
        await self.consume_mail_provider_rate(worker_id)
        result = await self.charged_provider_call(
            worker_id=worker_id,
            operation="mail_search" if query else "mail_list",
            call=lambda: self.stalwart.list_messages(
                str(identity["provider_account_id"]), limit=limit, query=query
            ),
        )
        await self.store.create_audit(actor_id=actor_id, action="mail.search" if query else "mail.list", target_type="mailbox", target_id=str(identity["id"]), outcome="ok", metadata={"limit": limit, "query_present": bool(query)})
        return result

    async def mail_read(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, message_id: str) -> dict[str, Any]:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        if identity.get("state") not in {IdentityState.IDENTITY_ACTIVE, IdentityState.IDENTITY_VERIFYING, "IDENTITY_ACTIVE", "IDENTITY_VERIFYING"} or not identity.get("provider_account_id"):
            raise PermissionError("mailbox is not available")
        await self.consume_mail_provider_rate(worker_id)
        result = await self.charged_provider_call(
            worker_id=worker_id,
            operation="mail_read",
            call=lambda: self.stalwart.read_message(
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
        if (
            str(identity.get("state")) != "IDENTITY_ACTIVE"
            or not identity.get("provider_account_id")
        ):
            raise PermissionError("active mailbox is required")
        await self.consume_mail_provider_rate(worker_id)
        if operation == "mark_processed":
            call = lambda: self.stalwart.mark_processed(  # noqa: E731
                str(identity["provider_account_id"]), message_id
            )
            audit_action = "mail.mark_processed"
        elif operation == "delete":
            call = lambda: self.stalwart.delete_message(  # noqa: E731
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

    async def wait_for_verification(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, sender_domain: str | None, timeout_seconds: int) -> dict[str, Any] | None:
        self.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
        identity = await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        if identity.get("state") not in {IdentityState.IDENTITY_ACTIVE, IdentityState.IDENTITY_VERIFYING, "IDENTITY_ACTIVE", "IDENTITY_VERIFYING"} or not identity.get("provider_account_id"):
            raise PermissionError("mailbox is not available")
        await self.consume_mail_provider_rate(worker_id)
        result = await self.charged_provider_call(
            worker_id=worker_id,
            operation="mail_wait",
            call=lambda: self.stalwart.wait_for_message(
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

    async def request_outbound(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, recipients: list[str], subject: str, body: str, recipient_class: str, idempotency_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        request, approval, _created = await self.outbound.request(worker_id=worker_id, recipients=recipients, subject=subject, body=body, recipient_class=recipient_class, idempotency_key=idempotency_key)
        await self.store.create_audit(actor_id=actor_id, action="mail.send_request", target_type="outbound_mail_request", target_id=str(request["id"]), outcome="pending_approval", metadata={"approval_id": str(approval["id"]), "recipient_count": len(recipients)})
        return request, approval

    async def send_approved(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, request_id: UUID, idempotency_key: str) -> dict[str, Any]:
        await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        request = await self.outbound.send_approved(worker_id=worker_id, outbound_request_id=request_id, idempotency_key=idempotency_key)
        await self.store.create_audit(actor_id=actor_id, action="mail.send_approved", target_type="outbound_mail_request", target_id=str(request_id), outcome="submitted", metadata={"provider_correlation_id": request.get("provider_correlation_id")})
        return request

    async def cancel_queued_outbound(self, client: AuthenticatedClient, *, worker_id: UUID, actor_id: str, request_id: UUID) -> dict[str, Any]:
        await self.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
        request = await self.store.get_outbound_request(request_id)
        if request is None or request.get("worker_id") != worker_id:
            raise PermissionError("outbound request ownership denied")
        provider_message_id = request.get("provider_message_id")
        if not provider_message_id:
            raise ValueError("outbound message has not been queued")
        identity = await self.store.get_identity(worker_id)
        if identity is None or not identity.get("provider_account_id"):
            raise PermissionError("mailbox is unavailable")
        result = await self.charged_provider_call(
            worker_id=worker_id,
            operation="mail_cancel_queued",
            call=lambda: self.stalwart.cancel_queued_message(
                str(identity["provider_account_id"]), str(provider_message_id)
            ),
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

    async def request_external_credential_rotation(
        self,
        client: AuthenticatedClient,
        *,
        worker_id: UUID,
        actor_id: str,
        account_id: UUID,
        idempotency_key: str,
    ) -> dict[str, Any]:
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
        return {"status": "ok", "service": "identity-service", "direct_mx_outbound_enabled": self.settings.direct_mx_outbound_enabled, "outbound_relay_provider": self.settings.outbound_relay_provider}

    async def dashboard_resource(self, resource: str) -> list[dict[str, Any]]:
        rows = await self.store.dashboard_rows(resource)
        if resource != "mail-relay":
            return rows
        health: dict[str, Any] = {
            "record_type": "relay_health",
            "relay_provider": "resend",
            "relay_host": self.settings.outbound_relay_host,
            "relay_port": self.settings.outbound_relay_port,
            "relay_tls_mode": self.settings.outbound_relay_tls_mode,
            "direct_mx_outbound_enabled": False,
        }
        try:
            health["stalwart_health"] = (await self.stalwart.health_check()).get(
                "healthy", True
            )
        except Exception as exc:
            health["stalwart_health"] = "unavailable"
            health["stalwart_error"] = type(exc).__name__
        try:
            health["resend_health"] = (await self.resend.health_check()).get(
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
            if account_id and provider_message_id:
                try:
                    status = await self.stalwart.get_outbound_queue_status(
                        str(account_id), str(provider_message_id)
                    )
                    row["stalwart_queue_state"] = status.get("result")
                except Exception as exc:
                    row["stalwart_queue_state"] = "unavailable"
                    row["queue_status_error"] = type(exc).__name__
            safe_attempts.append(row)
        return [health, *safe_attempts]
