"""Human-approved provider-neutral outbound delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ..approvals.service import ApprovalService
from ..models import ApprovalState
from ..providers.base import OutboundMailProvider
from ..store import IdentityStore
from ..sync.outbox import OutboxService
from ..usage.ledger import UsageLedger
from .policy import OutboundPolicy


class OutboundService:
    def __init__(self, *, store: IdentityStore, provider: OutboundMailProvider, approvals: ApprovalService, usage: UsageLedger, outbox: OutboxService, policy: OutboundPolicy, agent_domain: str, provider_rate_limit: int = 30, outbound_relay_certified: bool = False, provider_name: str | None = None) -> None:
        self.store = store
        self.provider = provider
        self.approvals = approvals
        self.usage = usage
        self.outbox = outbox
        self.policy = policy
        self.agent_domain = agent_domain
        self.provider_rate_limit = provider_rate_limit
        self.outbound_relay_certified = outbound_relay_certified
        self.provider_name = provider_name or str(getattr(provider, "provider_name", "stalwart"))

    async def request(self, *, worker_id: UUID, recipients: list[str], subject: str, body: str, recipient_class: str, idempotency_key: str, content_type: str = "text/plain") -> tuple[dict, dict, bool]:
        if not self.outbound_relay_certified:
            raise PermissionError(f"outbound mail is disabled until {self.provider_name} certification passes")
        identity = await self.store.get_identity(worker_id)
        if identity is None or str(identity.get("state")) != "IDENTITY_ACTIVE":
            raise PermissionError("active identity is required before outbound mail")
        self.policy.validate(recipients=recipients, recipient_class=recipient_class, body=body, sender_domain=self.agent_domain)
        request, created = await self.store.create_outbound_request(worker_id=worker_id, identity_id=identity["id"], sender=identity["address"], recipients=recipients, subject=subject, body=body, content_type=content_type, provider=self.provider_name, recipient_class=recipient_class, idempotency_key=idempotency_key)
        approval = await self.approvals.request(worker_id=worker_id, kind="outbound_mail", target_id=request["id"], idempotency_key=f"approval:{idempotency_key}")
        if created:
            await self.outbox.append("outbound_mail.requested", "outbound_mail_request", str(request["id"]), {"worker_id": str(worker_id), "approval_id": str(approval["id"]), "recipient_count": len(recipients)})
        return request, approval, created

    async def send_approved(self, *, worker_id: UUID, outbound_request_id: UUID, idempotency_key: str, trace_id: str | None = None) -> dict:
        if not self.outbound_relay_certified:
            raise PermissionError(f"outbound mail is disabled until {self.provider_name} certification passes")
        request = await self.store.get_outbound_request(outbound_request_id)
        if request is None or request.get("worker_id") != worker_id:
            raise PermissionError("outbound request ownership denied")
        approval = await self.store.get_approval_for_target(outbound_request_id)
        if (
            approval is None
            or str(approval.get("state")) != ApprovalState.APPROVED
            or str(approval.get("kind")) != "outbound_mail"
            or approval.get("worker_id") != worker_id
        ):
            raise PermissionError("human outbound approval is required")
        if str(request.get("state")) == "SUBMITTED":
            return request
        identity = await self.store.get_identity(worker_id)
        binding = await self.store.get_provider_binding(
            identity_id=identity["id"], direction="OUTBOUND", provider=self.provider_name
        ) if identity is not None else None
        provider_reference = (binding or {}).get("provider_reference") or (identity or {}).get("provider_account_id")
        if (
            identity is None
            or str(identity.get("state")) != "IDENTITY_ACTIVE"
            or identity.get("id") != request.get("identity_id")
            or str((binding or {}).get("lifecycle_state") or "") != "ACTIVE"
            or (self.provider_name == "stalwart" and not provider_reference)
        ):
            raise PermissionError("active sender-owned email identity is required")
        request, claimed = await self.store.claim_outbound_submission(outbound_request_id)
        if request is None:
            raise PermissionError("outbound request ownership denied")
        if not claimed:
            if str(request.get("state")) == "SUBMITTED":
                return request
            raise ValueError("outbound submission is already in progress or awaiting reconciliation")
        hold = None
        provider_hold = None
        provider_accepted = False
        try:
            hold = await self.usage.reserve(worker_id=worker_id, kind="outbound_message", idempotency_key=f"hold:{idempotency_key}")
            provider_hold = await self.usage.reserve(
                worker_id=worker_id, kind="provider_api_call",
                idempotency_key=f"hold:provider:{idempotency_key}",
            )
            window = datetime.now(UTC).replace(second=0, microsecond=0)
            if not await self.store.consume_provider_rate(
            provider=self.provider_name, rate_key=f"outbound:{worker_id}",
                window_started_at=window, limit=self.provider_rate_limit,
            ):
                raise PermissionError("outbound provider rate limit exceeded")
            if hasattr(self.provider, "send_message"):
                result = await self.provider.send_message(
                    sender=str(request["sender"]), recipients=list(request["recipients"]),
                    subject=str(request["subject"]), body=str(request["body"]),
                    idempotency_key=idempotency_key,
                    content_type=str(request.get("content_type") or "text/plain"),
                    provider_reference=str(provider_reference) if provider_reference else None,
                )
            else:
                # Compatibility for the preserved Stalwart adapter and old
                # fixture providers. New providers implement send_message.
                submit = getattr(self.provider, "submit_outbound_message", None)
                if not callable(submit) or not provider_reference:
                    raise PermissionError("selected outbound provider cannot submit this identity")
                result = await submit(
                    str(provider_reference), sender=str(request["sender"]),
                    recipients=list(request["recipients"]), subject=str(request["subject"]),
                    body=str(request["body"]), idempotency_key=idempotency_key,
                )
            provider_accepted = True
            updated = await self.store.update_outbound_request(outbound_request_id, state="SUBMITTED", provider_message_id=result.get("provider_message_id"), provider_correlation_id=result.get("correlation_id"))
            await self.store.record_delivery_attempt(
                outbound_request_id=outbound_request_id,
                provider_correlation_id=result.get("correlation_id"),
                provider_message_id=result.get("provider_message_id"),
                outcome="QUEUED",
                trace_id=trace_id,
            )
            await self.usage.commit(hold["id"])
            await self.usage.commit(provider_hold["id"])
            if updated is None:
                raise RuntimeError("outbound request vanished")
            await self.outbox.append("outbound_mail.submitted", "outbound_mail_request", str(outbound_request_id), {"worker_id": str(worker_id), "provider_correlation_id": result.get("correlation_id")})
            return updated
        except Exception as exc:
            current = await self.store.get_outbound_request(outbound_request_id)
            already_submitted = bool(current and str(current.get("state")) == "SUBMITTED")
            if not already_submitted:
                ambiguous = provider_accepted or str(getattr(exc, "code", "")).endswith(("_TIMEOUT", "_UNAVAILABLE"))
                await self.store.update_outbound_request(
                    outbound_request_id,
                    state="SUBMISSION_UNKNOWN" if ambiguous else "SUBMISSION_FAILED",
                    provider_correlation_id=getattr(exc, "correlation_id", None),
                )
            await self.store.record_delivery_attempt(
                outbound_request_id=outbound_request_id,
                provider_correlation_id=getattr(exc, "correlation_id", None),
                provider_message_id=None,
                outcome="UNKNOWN" if provider_accepted else "FAILED",
                failure_class="transient" if bool(getattr(exc, "transient", False)) else "permanent",
                sanitized_reason=type(exc).__name__,
                trace_id=trace_id,
            )
            if hold is not None and not provider_accepted and not already_submitted:
                await self.usage.release(hold["id"])
            elif hold is not None and provider_accepted:
                await self.usage.commit(hold["id"])
            if provider_hold is not None:
                if provider_accepted:
                    await self.usage.commit(provider_hold["id"])
                elif not already_submitted:
                    await self.usage.release(provider_hold["id"])
            raise
