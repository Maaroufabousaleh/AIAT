"""Governed mailbox, external-account and browser-session tools."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ..identity_client import IdentityGatewayClient

_ROLES = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE, AgentRole.ADMIN, AgentRole.WORKER]


def _worker_context(kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    context = kwargs.pop("_aiat_context", None)
    if not isinstance(context, dict) or not context.get("caller_id"):
        raise PermissionError("trusted tool caller context is required")
    worker_id = str(kwargs.get("worker_id") or "")
    if not worker_id:
        raise ValueError("worker_id is required")
    role = str(context.get("caller_role") or "").lower()
    # A worker is never allowed to select another worker's resource. Elevated
    # control-plane roles are separately audited and identity-service scoped.
    if role in {"worker", "sub_agent"} and context["caller_id"] != worker_id:
        raise PermissionError("cross-worker identity access is denied")
    actor = {"actor_id": context["caller_id"], "project_id": context.get("project_id"), "worker_run_id": context.get("worker_run_id"), "purpose": str(kwargs.pop("purpose", "governed identity operation"))}
    return worker_id, actor


class IdentityTool(BaseTool):
    group = ToolGroup.KPI_UTILITY
    allowed_roles = _ROLES
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 3

    def __init__(self, name: str, path: str, description: str, mode: str = "standard") -> None:
        self.name = name
        self.path = path
        self.description = description
        self.mode = mode

    async def execute(self, **kwargs: Any) -> Any:
        worker_id, actor = _worker_context(kwargs)
        client = IdentityGatewayClient()
        if self.mode == "address":
            return await client.post("/v1/identity/email-address", {"worker_id": worker_id, "actor": actor})
        if self.mode in {"list", "search", "read", "mark", "delete", "extract-code", "extract-link"}:
            body = {"worker_id": worker_id, "actor": actor, "limit": kwargs.get("limit", 25), "query": kwargs.get("query"), "message_id": kwargs.get("message_id")}
        elif self.mode == "wait":
            body = {"worker_id": worker_id, "actor": actor, "sender_domain": kwargs.get("sender_domain"), "timeout_seconds": kwargs.get("timeout_seconds", 30)}
        elif self.mode == "outbound-request":
            body = {"worker_id": worker_id, "actor": actor, "idempotency_key": kwargs.get("idempotency_key"), "recipients": kwargs.get("recipients"), "subject": kwargs.get("subject"), "body": kwargs.get("body"), "recipient_class": kwargs.get("recipient_class")}
        elif self.mode == "outbound-approved":
            body = {"worker_id": worker_id, "actor": actor, "outbound_request_id": kwargs.get("outbound_request_id"), "idempotency_key": kwargs.get("idempotency_key")}
        elif self.mode == "outbound-cancel":
            request_id = UUID(str(kwargs.get("outbound_request_id")))
            return await client.post(f"/v1/outbound/{request_id}/cancel", {"worker_id": worker_id, "actor": actor})
        elif self.mode == "outbound-status":
            body = {"worker_id": worker_id, "actor": actor, "outbound_request_id": kwargs.get("outbound_request_id")}
        elif self.mode == "external":
            body = {"worker_id": worker_id, "actor": actor, "service": kwargs.get("service"), "service_category": kwargs.get("service_category"), "idempotency_key": kwargs.get("idempotency_key"), "email_identity_id": kwargs.get("email_identity_id")}
        elif self.mode.startswith("external-"):
            account_id = UUID(str(kwargs.get("external_account_id")))
            if self.mode == "external-status":
                return await client.post(f"/v1/external-accounts/{account_id}/status", {"worker_id": worker_id, "actor": actor})
            if self.mode == "external-login":
                return await client.post(f"/v1/external-accounts/{account_id}/login", {"worker_id": worker_id, "actor": actor, "service": kwargs.get("service"), "external_account_id": str(account_id), "idempotency_key": kwargs.get("idempotency_key")})
            if self.mode == "external-rotate":
                return await client.post(f"/v1/external-accounts/{account_id}/rotate-credentials", {"worker_id": worker_id, "actor": actor, "service": kwargs.get("service"), "service_category": kwargs.get("service_category", "development_test"), "idempotency_key": kwargs.get("idempotency_key")})
            state_path = "suspend" if self.mode == "external-suspend" else "close"
            return await client.post(f"/v1/external-accounts/{account_id}/{state_path}", {"worker_id": worker_id, "actor": actor, "service": kwargs.get("service"), "service_category": kwargs.get("service_category", "development_test"), "idempotency_key": kwargs.get("idempotency_key")})
        elif self.mode == "session-create":
            body = {"worker_id": worker_id, "actor": actor, "service": kwargs.get("service"), "external_account_id": kwargs.get("external_account_id"), "idempotency_key": kwargs.get("idempotency_key")}
        elif self.mode in {"session-use", "session-revoke"}:
            body = {"worker_id": worker_id, "actor": actor, "session_id": kwargs.get("session_id")}
        else:
            raise RuntimeError("identity tool configuration is invalid")
        if self.mode == "session-use":
            session_id = str(body.get("session_id") or "")
            if not session_id:
                raise ValueError("session_id is required")
            return await client.use_browser_session(
                worker_id=worker_id, actor=actor, session_id=session_id
            )
        return await client.post(self.path, body)


def get_identity_tools() -> list[BaseTool]:
    return [
        IdentityTool("identity.email.get_address", "/v1/identity/email-address", "Get the caller-owned governed mailbox address.", "address"),
        IdentityTool("mail.list", "/v1/mail/list", "List only the caller-owned mailbox messages.", "list"),
        IdentityTool("mail.search", "/v1/mail/search", "Search only the caller-owned mailbox messages.", "search"),
        IdentityTool("mail.read", "/v1/mail/read", "Read a caller-owned mailbox message.", "read"),
        IdentityTool("mail.wait_for_verification", "/v1/mail/wait-for-verification", "Wait for a caller-owned verification message.", "wait"),
        IdentityTool("mail.extract_code", "/v1/mail/extract-code", "Extract a code from a caller-owned verification message.", "extract-code"),
        IdentityTool("mail.extract_link", "/v1/mail/extract-link", "Extract a safe link from a caller-owned verification message.", "extract-link"),
        IdentityTool("mail.mark_processed", "/v1/mail/mark-processed", "Mark a caller-owned mailbox message processed.", "mark"),
        IdentityTool("mail.delete", "/v1/mail/delete", "Delete a caller-owned mailbox message.", "delete"),
        IdentityTool("mail.send_request", "/v1/outbound/request", "Request human-approved outbound mail through Stalwart.", "outbound-request"),
        IdentityTool("mail.send_approved", "/v1/outbound/send-approved", "Submit an approved mail request through Stalwart's queue.", "outbound-approved"),
        IdentityTool("mail.get_delivery_status", "/v1/outbound/delivery-status", "Read caller-owned outbound delivery status.", "outbound-status"),
        IdentityTool("mail.cancel_queued", "/v1/outbound/{outbound_request_id}/cancel", "Cancel a caller-owned queued outbound message.", "outbound-cancel"),
        IdentityTool("identity.external.signup_request", "/v1/external-accounts/signup-request", "Request a governed external account.", "external"),
        IdentityTool("identity.external.login", "/v1/external-accounts/{external_account_id}/login", "Begin external account login in an isolated local browser profile.", "external-login"),
        IdentityTool("identity.external.get_status", "/v1/external-accounts/{external_account_id}/status", "Read caller-owned external-account state.", "external-status"),
        IdentityTool("identity.external.rotate_credentials", "/v1/external-accounts/{external_account_id}/rotate-credentials", "Request governed credential rotation.", "external-rotate"),
        IdentityTool("identity.external.suspend", "/v1/external-accounts/{external_account_id}/suspend", "Suspend caller-owned external account.", "external-suspend"),
        IdentityTool("identity.external.close", "/v1/external-accounts/{external_account_id}/close", "Close caller-owned external account.", "external-close"),
        IdentityTool("identity.session.create", "/v1/sessions/create", "Create an opaque local browser session handle.", "session-create"),
        IdentityTool("identity.session.use", "/v1/sessions/use", "Use a caller-owned opaque browser session handle.", "session-use"),
        IdentityTool("identity.session.revoke", "/v1/sessions/revoke", "Revoke caller-owned browser sessions.", "session-revoke"),
    ]
