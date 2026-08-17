"""Versioned signed HTTPS identity APIs.

Every non-health endpoint verifies a request signature before it accepts actor
or worker data.  The route layer only returns safe views; it does not expose
raw password, cookie, token, credential, or relay fields.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute

from .clients.auth import SIGNATURE_VERSION, verify_request
from .messages.verification_parser import (
    extract_verification_code,
    extract_verification_link,
    message_text,
)
from .models import (
    ApprovalDecisionRequest,
    BrowserSessionLeaseRequest,
    BrowserSessionRequest,
    BrowserSessionUseRequest,
    DomainCreateRequest,
    ExternalAccountRequest,
    ExternalAccountState,
    ExternalAccountStatusRequest,
    IdentityVerificationRequest,
    IdentityView,
    MailQueryRequest,
    OutboundRequest,
    OutboundStatusRequest,
    ProviderWebhookRequest,
    ProvisionIdentityRequest,
    RequestActor,
    SendApprovedRequest,
    SyncAckRequest,
    SyncRequest,
    VerificationWaitRequest,
    WorkerIdentityActionRequest,
    redact,
)
from .observability import normalize_trace_id
from .service import AuthenticatedClient, IdentityService


class SignedBodyRoute(APIRoute):
    """Cache request bytes before FastAPI resolves bodies and dependencies.

    Reading the ASGI body for signature verification from a dependency can
    consume the stream that FastAPI subsequently uses for model validation.
    Capturing it at the route-handler boundary keeps one authoritative byte
    sequence for both operations.
    """

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def signed_body_route_handler(request: Request):
            request.scope["aiat.identity.raw_body"] = await request.body()
            return await route_handler(request)

        return signed_body_route_handler


router = APIRouter(prefix="/v1", tags=["identity"], route_class=SignedBodyRoute)


async def _service(request: Request) -> IdentityService:
    return request.app.state.identity_service


async def _signed_client(request: Request) -> AuthenticatedClient:
    settings = request.app.state.settings
    if request.headers.get("X-AIAT-Signature-Version") != SIGNATURE_VERSION:
        raise HTTPException(401, "identity request signature version is required")
    client_id = request.headers.get("X-AIAT-Client-ID", "")
    try:
        signed_target = request.url.path
        if request.url.query:
            signed_target = f"{signed_target}?{request.url.query}"
        await verify_request(
            client_id=client_id,
            timestamp=request.headers.get("X-AIAT-Timestamp", ""),
            nonce=request.headers.get("X-AIAT-Nonce", ""),
            signature=request.headers.get("X-AIAT-Signature", ""),
            method=request.method,
            path=signed_target,
            body=request.scope.get("aiat.identity.raw_body", b""),
            public_keys=settings.client_public_keys,
            replay_store=request.app.state.identity_store,
        )
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    registration = await request.app.state.identity_store.get_client_registration(client_id)
    if (
        registration is None
        or registration.get("state") != "ACTIVE"
        or registration.get("public_key") != settings.client_public_keys.get(client_id)
    ):
        raise HTTPException(401, "identity client registration is inactive or mismatched")
    return AuthenticatedClient(client_id=client_id, scopes=frozenset(registration.get("scopes") or []))


def _safe_identity(value: dict[str, Any]) -> dict[str, Any]:
    return IdentityView.model_validate({
        "id": value["id"], "company_id": value["company_id"], "worker_id": value["worker_id"],
        "address": value["address"], "alias": value.get("alias") or value.get("friendly_alias"),
        "state": value["state"], "quota_mb": value.get("quota_mb", 100),
        "outbound_enabled": bool(value.get("outbound_enabled", False)),
        "provider_account_id": value.get("provider_account_id"),
        "created_at": value["created_at"], "updated_at": value["updated_at"],
    }).model_dump(mode="json")


def _safe_outbound(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {"id", "worker_id", "identity_id", "sender", "recipients", "recipient_class", "state", "provider_message_id", "provider_correlation_id", "created_at", "updated_at"}
    return redact({key: item for key, item in value.items() if key in allowed})


def _safe_session(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {"id", "worker_id", "service", "external_account_id", "state", "created_at", "updated_at", "lease_version"}
    return redact({key: item for key, item in value.items() if key in allowed})


def _safe_mail_edge(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id", "schema_version", "provider", "source", "event_id", "event_type",
        "outcome", "failure_class", "worker_id", "outbound_request_id",
        "provider_message_ref", "trace_id", "span_id", "occurred_at",
        "received_at", "signature_verified", "metadata",
    }
    return redact({key: item for key, item in value.items() if key in allowed})


@router.post("/domains")
async def create_domain(body: DomainCreateRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        service.assert_admin(client)
        return await service.domains.create(body.domain, actor_id=body.actor.actor_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/domains/{domain}/verify")
async def verify_domain(domain: str, actor: RequestActor, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        service.assert_admin(client)
        return await service.domains.verify(domain.strip().lower().rstrip("."), actor_id=actor.actor_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/worker-identities/provision")
async def provision_identity(body: ProvisionIdentityRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        identity = await service.provision_identity(client, company_id=body.company_id, worker_id=body.worker_id, actor_id=body.actor.actor_id, friendly_alias=body.friendly_alias, idempotency_key=body.idempotency_key, mailbox_class=body.mailbox_class)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 422, str(exc)) from exc
    return _safe_identity(identity)


@router.post("/worker-identities/{worker_id}/verify")
async def verify_identity(worker_id: UUID, body: IdentityVerificationRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        identity = await service.verify_identity(client, worker_id=worker_id, actor_id=body.actor.actor_id, provider_message_id=body.provider_message_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 409, str(exc)) from exc
    return _safe_identity(identity)


@router.post("/mail-edge/provider-webhook")
async def record_provider_webhook(body: ProviderWebhookRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        observation = await service.record_provider_webhook(
            client,
            provider=body.provider,
            payload=body.payload,
            actor_id=body.actor.actor_id,
            event_id=body.event_id,
            signature_verified=body.signature_verified,
            worker_id=body.worker_id,
            outbound_request_id=body.outbound_request_id,
            trace_id=body.trace_id,
            span_id=body.span_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _safe_mail_edge(observation)


@router.post("/mail-edge/provider-webhook/resend")
async def receive_resend_provider_webhook(request: Request, service: IdentityService = Depends(_service)) -> dict[str, Any]:
    """Accept a Resend/Svix webhook after raw-body signature verification."""

    raw_body = bytes(request.scope.get("aiat.identity.raw_body", b""))
    if len(raw_body) > 1 * 1024 * 1024:
        raise HTTPException(413, "webhook body exceeds 1 MiB limit")
    if not service.resend.verify_configured_webhook_signature(raw_body, request.headers):
        raise HTTPException(401, "invalid provider webhook authentication")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "webhook body must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "webhook body must be a JSON object")
    try:
        observation = await service.record_verified_provider_webhook(
            provider="resend",
            payload=payload,
            actor_id="provider:resend",
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _safe_mail_edge(observation)


@router.post("/worker-identities/{worker_id}/suspend")
async def suspend_identity(worker_id: UUID, body: WorkerIdentityActionRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        identity = await service.suspend_identity(client, worker_id=worker_id, actor_id=body.actor.actor_id, reason=body.actor.purpose)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if identity is None:
        raise HTTPException(404, "identity not found")
    return _safe_identity(identity)


@router.post("/worker-identities/{worker_id}/archive")
async def archive_identity(worker_id: UUID, body: WorkerIdentityActionRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        identity = await service.archive_identity(client, worker_id=worker_id, actor_id=body.actor.actor_id, reason=body.actor.purpose)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if identity is None:
        raise HTTPException(404, "identity not found")
    return _safe_identity(identity)


@router.get("/worker-identities/{worker_id}")
async def get_identity(worker_id: UUID, actor_id: str, purpose: str, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        service.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    try:
        identity = await service.owned_identity(client, actor_id=actor_id, worker_id=worker_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return _safe_identity(identity)


@router.post("/identity/email-address")
async def email_address(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        identity = await service.owned_identity(client, actor_id=body.actor.actor_id, worker_id=body.worker_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    await service.store.create_audit(
        actor_id=body.actor.actor_id, action="identity.email.get_address",
        target_type="agent_email_identity", target_id=str(identity["id"]),
        outcome="ok", metadata={},
    )
    return {"address": identity["address"], "state": identity["state"]}


@router.post("/mail/list")
async def mail_list(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return await service.mail_list(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, limit=body.limit)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/mail/search")
async def mail_search(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not body.query:
        raise HTTPException(422, "mail search query is required")
    try:
        return await service.mail_list(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, limit=body.limit, query=body.query)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/mail/read")
async def mail_read(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not body.message_id:
        raise HTTPException(422, "message_id is required")
    try:
        return await service.mail_read(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, message_id=body.message_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/mail/mark-processed")
async def mark_processed(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not body.message_id:
        raise HTTPException(422, "message_id is required")
    try:
        return await service.mutate_mail_message(
            client, worker_id=body.worker_id, actor_id=body.actor.actor_id,
            message_id=body.message_id, operation="mark_processed",
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/mail/delete")
async def delete_mail(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not body.message_id:
        raise HTTPException(422, "message_id is required")
    try:
        return await service.mutate_mail_message(
            client, worker_id=body.worker_id, actor_id=body.actor.actor_id,
            message_id=body.message_id, operation="delete",
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/mail/wait-for-verification")
async def wait_for_verification(body: VerificationWaitRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        message = await service.wait_for_verification(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, sender_domain=body.sender_domain, timeout_seconds=body.timeout_seconds)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"found": message is not None, "message": message}


@router.post("/mail/extract-code")
async def extract_code(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not body.message_id:
        raise HTTPException(422, "message_id is required")
    try:
        message = await service.mail_read(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, message_id=body.message_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    code = extract_verification_code(message_text(message))
    await service.record_verification_extraction(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, message_id=body.message_id, code=code)
    return {"code": code}


@router.post("/mail/extract-link")
async def extract_link(body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not body.message_id:
        raise HTTPException(422, "message_id is required")
    try:
        message = await service.mail_read(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, message_id=body.message_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    link = extract_verification_link(message_text(message))
    await service.record_verification_extraction(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, message_id=body.message_id, link=link)
    return {"link": link}


@router.post("/outbound/request")
async def outbound_request(body: OutboundRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        request, approval = await service.request_outbound(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, recipients=body.recipients, subject=body.subject, body=body.body, recipient_class=body.recipient_class, idempotency_key=body.idempotency_key)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"request": _safe_outbound(request), "approval": redact(approval)}


@router.post("/outbound/send-approved")
async def outbound_send_approved(request: Request, body: SendApprovedRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        result = await service.send_approved(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, request_id=body.outbound_request_id, idempotency_key=body.idempotency_key, trace_id=normalize_trace_id(request.headers.get("X-AIAT-Trace-ID")))
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 409, str(exc)) from exc
    return _safe_outbound(result)


@router.get("/outbound/{request_id}/delivery-status")
async def delivery_status(request_id: UUID, worker_id: UUID, actor_id: str, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        service.assert_worker_access(client, actor_id=actor_id, worker_id=worker_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    request = await service.store.get_outbound_request(request_id)
    if request is None or request.get("worker_id") != worker_id:
        raise HTTPException(404, "outbound request not found")
    await service.store.create_audit(
        actor_id=actor_id, action="mail.get_delivery_status",
        target_type="outbound_mail_request", target_id=str(request_id),
        outcome="ok", metadata={},
    )
    return _safe_outbound(request)


@router.post("/outbound/delivery-status")
async def delivery_status_post(body: OutboundStatusRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        service.assert_worker_access(client, actor_id=body.actor.actor_id, worker_id=body.worker_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    request = await service.store.get_outbound_request(body.outbound_request_id)
    if request is None or request.get("worker_id") != body.worker_id:
        raise HTTPException(404, "outbound request not found")
    await service.store.create_audit(
        actor_id=body.actor.actor_id, action="mail.get_delivery_status",
        target_type="outbound_mail_request",
        target_id=str(body.outbound_request_id), outcome="ok", metadata={},
    )
    return _safe_outbound(request)


@router.post("/outbound/{request_id}/cancel")
async def cancel_queued(request_id: UUID, body: MailQueryRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        request = await service.cancel_queued_outbound(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, request_id=request_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 409, str(exc)) from exc
    return _safe_outbound(request)


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(approval_id: UUID, body: ApprovalDecisionRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        decision = await service.decide_approval(client, approval_id=approval_id, actor_id=body.actor.actor_id, approved=body.approved, reason=body.reason)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if decision is None:
        raise HTTPException(404, "pending approval not found")
    return redact(decision)


@router.post("/external-accounts/signup-request")
async def signup_external_account(body: ExternalAccountRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return redact(await service.signup_external_account(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, service=body.service, service_category=body.service_category, idempotency_key=body.idempotency_key, email_identity_id=body.email_identity_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/external-accounts/action-policy")
async def external_account_action_policy(client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    """Return the versioned high-risk action taxonomy for operator clients."""

    return service.external_account_action_catalog()


@router.get("/external-accounts/{account_id}")
async def external_account_status(account_id: UUID, worker_id: UUID, actor_id: str, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return redact(await service.external_account_status(client, worker_id=worker_id, actor_id=actor_id, account_id=account_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/external-accounts/{account_id}/status")
async def external_account_status_post(account_id: UUID, body: ExternalAccountStatusRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return redact(await service.external_account_status(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, account_id=account_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/external-accounts/{account_id}/login")
async def external_account_login(account_id: UUID, body: BrowserSessionRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    # Login is a local-browser-session request, never a credential export.
    if body.external_account_id and body.external_account_id != account_id:
        raise HTTPException(422, "external account id does not match request path")
    try:
        session = await service.create_browser_session(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, service=body.service, external_account_id=account_id, idempotency_key=body.idempotency_key)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return _safe_session(session)


@router.post("/external-accounts/{account_id}/rotate-credentials")
async def rotate_external_credentials(account_id: UUID, body: ExternalAccountRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        result = await service.request_external_credential_rotation(
            client, worker_id=body.worker_id, actor_id=body.actor.actor_id,
            account_id=account_id, idempotency_key=body.idempotency_key,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return redact(result)


@router.post("/external-accounts/{account_id}/suspend")
async def suspend_external_account(account_id: UUID, body: ExternalAccountRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return redact(await service.set_external_account_state(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, account_id=account_id, state=ExternalAccountState.SUSPENDED))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/external-accounts/{account_id}/close")
async def close_external_account(account_id: UUID, body: ExternalAccountRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return redact(await service.request_external_account_close(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, account_id=account_id, idempotency_key=body.idempotency_key))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/sessions/create")
async def create_session(body: BrowserSessionRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        session = await service.create_browser_session(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, service=body.service, external_account_id=body.external_account_id, idempotency_key=body.idempotency_key)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return _safe_session(session)


@router.post("/sessions/use")
async def use_session(body: BrowserSessionUseRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return _safe_session(await service.use_browser_session(client, worker_id=body.worker_id, actor_id=body.actor.actor_id, session_id=body.session_id, lease_token=body.lease_token))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/sessions/lease")
async def lease_session(body: BrowserSessionLeaseRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    """Issue a one-use lease only to the signed local browser broker."""
    try:
        lease = await service.issue_browser_session_lease(
            client, worker_id=body.worker_id, actor_id=body.actor.actor_id,
            session_id=body.session_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return lease


@router.post("/sessions/revoke")
async def revoke_session(body: BrowserSessionUseRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        return {"revoked": await service.revoke_browser_session(client, worker_id=body.worker_id, actor_id=body.actor.actor_id)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/sync/events")
async def sync_events(body: SyncRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not client.has("identity:delegate"):
        raise HTTPException(403, "reconciliation requires delegated control-plane scope")
    return await service.outbox.reconcile(client.client_id, body.cursor, body.limit)


@router.post("/sync/ack")
async def sync_ack(body: SyncAckRequest, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    if not client.has("identity:delegate"):
        raise HTTPException(403, "reconciliation acknowledgement requires delegated control-plane scope")
    try:
        return await service.outbox.acknowledge(client.client_id, body.cursor)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/dashboard/{resource}")
async def dashboard_resource(resource: str, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    try:
        service.assert_admin(client)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    allowed = {"identities", "mail-domains", "mailboxes", "outbound-mail", "mail-relay", "mail-edge", "external-accounts", "auth-sessions", "identity-approvals", "identity-audit"}
    if resource not in allowed:
        raise HTTPException(404, "identity dashboard resource not found")
    return {"items": redact(await service.dashboard_resource(resource)), "resource": resource}


@router.post("/dashboard/{resource}")
async def dashboard_resource_post(resource: str, client: AuthenticatedClient = Depends(_signed_client), service: IdentityService = Depends(_service)) -> dict[str, Any]:
    return await dashboard_resource(resource, client, service)
