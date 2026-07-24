"""Stalwart JMAP adapter.

This adapter calls Stalwart's management/JMAP surface; it never returns a
mailbox password or a Stalwart administration credential.  Actual object
schemas are negotiated through JMAP, which keeps the adapter version-aware.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import anyio
import httpx

from ..models import redact

logger = logging.getLogger(__name__)


class StalwartAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, transient: bool = False, correlation_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient
        self.correlation_id = correlation_id


class StalwartAdapter:
    """Timeout-bounded, audited-at-service-layer Stalwart JMAP adapter."""

    def __init__(self, *, base_url: str, api_key: str, jmap_service_token: str = "", timeout_seconds: float = 15.0, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._jmap_service_token = jmap_service_token
        self.timeout = timeout_seconds
        self._client = client

    @staticmethod
    def _headers(token: str, correlation_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "X-Request-ID": correlation_id}

    async def _call(self, method_calls: list[list[Any]], *, endpoint: str, token: str, idempotency_key: str | None = None) -> tuple[dict[str, Any], str]:
        correlation_id = str(uuid4())
        using = (
            ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]
            if endpoint == "/api"
            else ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail", "urn:ietf:params:jmap:submission"]
        )
        payload = {"using": using, "methodCalls": method_calls}
        headers = self._headers(token, correlation_id)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            if self._client is not None:
                response = await self._client.post(f"{self.base_url}{endpoint}", json=payload, headers=headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                    response = await client.post(f"{self.base_url}{endpoint}", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise StalwartAdapterError("STALWART_TIMEOUT", "Stalwart request timed out", transient=True, correlation_id=correlation_id) from exc
        except httpx.HTTPError as exc:
            raise StalwartAdapterError("STALWART_UNAVAILABLE", "Stalwart request failed", transient=True, correlation_id=correlation_id) from exc
        if response.status_code >= 500:
            raise StalwartAdapterError("STALWART_UNAVAILABLE", "Stalwart server error", transient=True, correlation_id=correlation_id)
        if response.status_code >= 400:
            raise StalwartAdapterError("STALWART_REJECTED", "Stalwart rejected the request", correlation_id=correlation_id)
        try:
            data = response.json()
        except ValueError as exc:
            raise StalwartAdapterError("STALWART_INVALID_RESPONSE", "Stalwart returned invalid JSON", transient=True, correlation_id=correlation_id) from exc
        return data, correlation_id

    async def _management_call(self, method_calls: list[list[Any]], *, idempotency_key: str | None = None) -> tuple[dict[str, Any], str]:
        """Call Stalwart's management JMAP API with the provisioning API key."""
        return await self._call(method_calls, endpoint="/api", token=self._api_key, idempotency_key=idempotency_key)

    async def _mail_call(self, method_calls: list[list[Any]], *, idempotency_key: str | None = None) -> tuple[dict[str, Any], str]:
        """Call mail JMAP with a separate service bearer token, never an API key."""
        if not self._jmap_service_token:
            raise StalwartAdapterError("STALWART_JMAP_AUTH_UNAVAILABLE", "Stalwart mail JMAP service authentication is unavailable")
        return await self._call(method_calls, endpoint="/jmap", token=self._jmap_service_token, idempotency_key=idempotency_key)

    @staticmethod
    def _first_response(data: dict[str, Any]) -> dict[str, Any]:
        calls = data.get("methodResponses") or []
        if not calls:
            raise StalwartAdapterError("STALWART_INVALID_RESPONSE", "Stalwart returned no method response")
        name, body, _tag = calls[0]
        if name == "error" or isinstance(body, dict) and body.get("type"):
            raise StalwartAdapterError("STALWART_OPERATION_FAILED", "Stalwart operation failed")
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _response_for(data: dict[str, Any], method_name: str) -> dict[str, Any]:
        for response in data.get("methodResponses") or []:
            if not isinstance(response, list) or len(response) < 2:
                continue
            name, body = response[0], response[1]
            if name == "error":
                raise StalwartAdapterError("STALWART_OPERATION_FAILED", "Stalwart operation failed")
            if name == method_name and isinstance(body, dict):
                return body
        raise StalwartAdapterError("STALWART_INVALID_RESPONSE", f"Stalwart returned no {method_name} response")

    @staticmethod
    def _require_set_created(body: dict[str, Any], creation_id: str) -> dict[str, Any]:
        not_created = body.get("notCreated") or {}
        if creation_id in not_created:
            raise StalwartAdapterError("STALWART_OPERATION_FAILED", "Stalwart object creation failed")
        created = (body.get("created") or {}).get(creation_id)
        if not isinstance(created, dict) or not created.get("id"):
            raise StalwartAdapterError("STALWART_INVALID_RESPONSE", "Stalwart did not return a created object identifier")
        return created

    async def health_check(self) -> dict[str, Any]:
        data, correlation_id = await self._management_call([["Core/echo", {"ping": "identity-service"}, "health"]])
        return {"healthy": True, "provider": "stalwart", "correlation_id": correlation_id, "response": redact(self._first_response(data))}

    async def create_domain(self, domain: str, *, idempotency_key: str) -> dict[str, Any]:
        value = {
            "name": domain, "aliases": {},
            "certificateManagement": {"@type": "Manual"},
            "dkimManagement": {"@type": "Automatic"},
            "dnsManagement": {"@type": "Manual"},
            "subAddressing": {"@type": "Enabled"},
        }
        data, correlation_id = await self._management_call([["x:Domain/set", {"create": {domain: value}}, "create-domain"]], idempotency_key=idempotency_key)
        return {"correlation_id": correlation_id, "result": self._first_response(data)}

    async def verify_domain(self, domain: str) -> dict[str, Any]:
        data, correlation_id = await self._management_call([["x:Domain/query", {"filter": {"name": domain}}, "verify-domain"]])
        return {"correlation_id": correlation_id, "result": self._first_response(data)}

    async def create_mailbox(self, address: str, *, quota_mb: int, idempotency_key: str) -> dict[str, Any]:
        # Account provisioning uses the management JMAP endpoint. Credentials
        # are explicitly empty, so a worker never receives a direct IMAP/SMTP
        # or JMAP password; AIAT reaches mail only through a separate service
        # token held by the identity-service.
        local_part, separator, domain = address.partition("@")
        if not separator or not local_part or not domain:
            raise ValueError("mailbox address must contain a local part and domain")
        domain_data, _domain_correlation = await self._management_call(
            [["x:Domain/query", {"filter": {"name": domain}}, "find-domain"]]
        )
        domain_ids = (self._first_response(domain_data).get("ids") or [])
        if not domain_ids:
            raise StalwartAdapterError("STALWART_DOMAIN_NOT_FOUND", "Stalwart mailbox domain is not configured")
        creation_id = f"identity-{idempotency_key[-32:]}"
        account = {
            "@type": "User", "name": local_part, "domainId": str(domain_ids[0]),
            "credentials": [], "memberGroupIds": [], "roles": {"@type": "User"},
            "permissions": {"@type": "Inherit"},
            "quotas": {"maxDiskQuota": quota_mb * 1024 * 1024}, "aliases": [],
            "encryptionAtRest": {"@type": "Disabled"},
        }
        data, correlation_id = await self._management_call([["x:Account/set", {"create": {creation_id: account}}, "create-mailbox"]], idempotency_key=idempotency_key)
        result = self._first_response(data)
        created = self._require_set_created(result, creation_id)
        return {"correlation_id": correlation_id, "provider_account_id": created.get("id"), "result": redact(result)}

    async def find_mailbox(self, address: str) -> dict[str, Any] | None:
        """Find a previously created passwordless mailbox after a crash.

        JMAP creation ids are scoped to one request, so an HTTP retry header
        alone cannot prove that `Account/set` was not already committed. The
        deterministic AIAT address is reconciled before create, and only an
        exact account with no direct-login credentials may be adopted.
        """
        local_part, separator, domain = address.partition("@")
        if not separator or not local_part or not domain:
            raise ValueError("mailbox address must contain a local part and domain")
        domain_data, _domain_correlation = await self._management_call(
            [["x:Domain/query", {"filter": {"name": domain}}, "find-domain"]]
        )
        domain_ids = self._first_response(domain_data).get("ids") or []
        if not domain_ids:
            return None
        domain_id = str(domain_ids[0])
        query_data, correlation_id = await self._management_call([[
            "x:Account/query",
            {"filter": {"name": local_part, "domainId": domain_id}, "limit": 2},
            "find-mailbox",
        ]])
        account_ids = self._first_response(query_data).get("ids") or []
        if not account_ids:
            return None
        if len(account_ids) != 1:
            raise StalwartAdapterError(
                "STALWART_ACCOUNT_AMBIGUOUS",
                "Stalwart returned multiple accounts for the worker mailbox",
            )
        account_data, _get_correlation = await self._management_call([[
            "x:Account/get", {"ids": [str(account_ids[0])]}, "verify-mailbox",
        ]])
        accounts = self._first_response(account_data).get("list") or []
        if len(accounts) != 1 or not isinstance(accounts[0], dict):
            raise StalwartAdapterError(
                "STALWART_INVALID_RESPONSE",
                "Stalwart did not return the reconciled mailbox",
            )
        account = accounts[0]
        actual_address = str(
            account.get("emailAddress")
            or f"{account.get('name', '')}@{domain}"
        ).casefold()
        if (
            actual_address != address.casefold()
            or str(account.get("domainId")) != domain_id
            or account.get("credentials") not in (None, [], {})
        ):
            raise StalwartAdapterError(
                "STALWART_ACCOUNT_UNSAFE",
                "Existing Stalwart mailbox cannot be adopted safely",
            )
        return {
            "correlation_id": correlation_id,
            "provider_account_id": str(account_ids[0]),
            "result": redact(account),
        }

    async def get_mailbox(self, provider_account_id: str) -> dict[str, Any]:
        data, correlation_id = await self._management_call([["x:Account/get", {"ids": [provider_account_id]}, "get-mailbox"]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def _account_set(self, provider_account_id: str, patch: dict[str, Any], tag: str) -> dict[str, Any]:
        data, correlation_id = await self._management_call([["x:Account/set", {"update": {provider_account_id: patch}}, tag]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def disable_mailbox(self, provider_account_id: str) -> dict[str, Any]:
        return await self._account_set(provider_account_id, {"permissions": {"@type": "Replace", "enabledPermissions": []}}, "disable-mailbox")

    async def enable_mailbox(self, provider_account_id: str) -> dict[str, Any]:
        return await self._account_set(provider_account_id, {"permissions": {"@type": "Inherit"}}, "enable-mailbox")

    async def rotate_mailbox_password(self, provider_account_id: str) -> dict[str, Any]:
        # Mailboxes are created without password credentials. Reasserting an
        # empty credential set revokes any accidental direct-login credential
        # without generating a secret that AIAT could expose or persist.
        return await self._account_set(provider_account_id, {"credentials": []}, "rotate-mailbox-password")

    async def set_quota(self, provider_account_id: str, quota_mb: int) -> dict[str, Any]:
        return await self._account_set(provider_account_id, {"quotas/maxDiskQuota": quota_mb * 1024 * 1024}, "set-quota")

    async def set_mailbox_quota(self, provider_account_id: str, quota_mb: int) -> dict[str, Any]:
        """Expose the plan's named mailbox-quota adapter contract."""
        return await self.set_quota(provider_account_id, quota_mb)

    async def _account_details(self, provider_account_id: str) -> dict[str, Any]:
        data, _correlation_id = await self._management_call(
            [["x:Account/get", {"ids": [provider_account_id]}, "account-details"]]
        )
        values = self._first_response(data).get("list") or []
        if not values or not isinstance(values[0], dict):
            raise StalwartAdapterError("STALWART_ACCOUNT_NOT_FOUND", "Stalwart mailbox account was not found")
        return values[0]

    async def add_alias(self, provider_account_id: str, alias: str) -> dict[str, Any]:
        account = await self._account_details(provider_account_id)
        local_part, separator, domain = alias.partition("@")
        if not local_part:
            raise ValueError("alias local part is required")
        domain_id = account.get("domainId")
        if separator:
            domain_data, _correlation_id = await self._management_call(
                [["x:Domain/query", {"filter": {"name": domain}}, "find-alias-domain"]]
            )
            ids = self._first_response(domain_data).get("ids") or []
            if not ids:
                raise StalwartAdapterError("STALWART_DOMAIN_NOT_FOUND", "Stalwart alias domain is not configured")
            domain_id = ids[0]
        if not domain_id:
            raise StalwartAdapterError("STALWART_ACCOUNT_INVALID", "Stalwart mailbox has no domain")
        aliases = list(account.get("aliases") or [])
        candidate = {"name": local_part, "domainId": str(domain_id), "enabled": True}
        if not any(item.get("name") == local_part and str(item.get("domainId")) == str(domain_id) for item in aliases if isinstance(item, dict)):
            aliases.append(candidate)
        return await self._account_set(provider_account_id, {"aliases": aliases}, "add-alias")

    async def remove_alias(self, provider_account_id: str, alias: str) -> dict[str, Any]:
        account = await self._account_details(provider_account_id)
        local_part = alias.split("@", 1)[0]
        aliases = [item for item in list(account.get("aliases") or []) if not (isinstance(item, dict) and item.get("name") == local_part)]
        return await self._account_set(provider_account_id, {"aliases": aliases}, "remove-alias")

    async def archive_mailbox(self, provider_account_id: str) -> dict[str, Any]:
        return await self._account_set(provider_account_id, {"permissions": {"@type": "Replace", "enabledPermissions": []}, "description": "Archived by AIAT"}, "archive-mailbox")

    async def delete_mailbox(self, provider_account_id: str) -> dict[str, Any]:
        data, correlation_id = await self._management_call([["x:Account/set", {"destroy": [provider_account_id]}, "delete-mailbox"]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def list_messages(self, account_id: str, *, limit: int = 25, query: str | None = None) -> dict[str, Any]:
        filter_value = {"text": query} if query else None
        data, correlation_id = await self._mail_call([["Email/query", {"accountId": account_id, "filter": filter_value, "limit": limit}, "list-mail"]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def search_messages(self, account_id: str, query: str, *, limit: int = 25) -> dict[str, Any]:
        return await self.list_messages(account_id, limit=limit, query=query)

    async def read_message(self, account_id: str, message_id: str) -> dict[str, Any]:
        data, correlation_id = await self._mail_call([["Email/get", {
            "accountId": account_id, "ids": [message_id],
            "properties": ["id", "threadId", "receivedAt", "from", "to", "subject", "preview", "bodyStructure", "bodyValues"],
            "fetchTextBodyValues": True, "fetchHTMLBodyValues": True,
            "maxBodyValueBytes": 250_000,
        }, "read-mail"]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def mark_processed(self, account_id: str, message_id: str) -> dict[str, Any]:
        data, correlation_id = await self._mail_call([["Email/set", {"accountId": account_id, "update": {message_id: {"keywords/$seen": True}}}, "mark-processed"]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def delete_message(self, account_id: str, message_id: str) -> dict[str, Any]:
        data, correlation_id = await self._mail_call([["Email/set", {"accountId": account_id, "destroy": [message_id]}, "delete-mail"]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def wait_for_message(self, account_id: str, *, sender_domain: str | None, timeout_seconds: int) -> dict[str, Any] | None:
        deadline = anyio.current_time() + timeout_seconds
        while anyio.current_time() < deadline:
            filter_value = {"from": sender_domain} if sender_domain else None
            data, _correlation_id = await self._mail_call([[
                "Email/query", {"accountId": account_id, "filter": filter_value, "limit": 1},
                "wait-verification",
            ]])
            ids = self._first_response(data).get("ids") or []
            if ids:
                return await self.read_message(account_id, str(ids[0]))
            await anyio.sleep(min(2, max(0.1, deadline - anyio.current_time())))
        return None

    async def submit_outbound_message(self, account_id: str, *, sender: str, recipients: list[str], subject: str, body: str, idempotency_key: str) -> dict[str, Any]:
        # Stalwart owns SMTP submission and queueing.  This submits via JMAP,
        # therefore no worker or laptop gets SMTP/Resend credentials.
        prerequisites, _prerequisite_correlation = await self._mail_call([
            ["Mailbox/get", {"accountId": account_id}, "submission-mailboxes"],
            ["Identity/get", {"accountId": account_id}, "submission-identities"],
        ])
        mailboxes = self._response_for(prerequisites, "Mailbox/get").get("list") or []
        draft_id = next((str(item["id"]) for item in mailboxes if isinstance(item, dict) and item.get("role") == "drafts" and item.get("id")), None)
        sent_id = next((str(item["id"]) for item in mailboxes if isinstance(item, dict) and item.get("role") == "sent" and item.get("id")), None)
        if not draft_id or not sent_id:
            raise StalwartAdapterError("STALWART_MAILBOX_ROLE_MISSING", "Stalwart Drafts or Sent mailbox is unavailable")
        identities = self._response_for(prerequisites, "Identity/get").get("list") or []
        identity_id = next(
            (str(item["id"]) for item in identities if isinstance(item, dict) and str(item.get("email", "")).casefold() == sender.casefold() and item.get("id")),
            None,
        )
        if not identity_id:
            raise StalwartAdapterError("STALWART_SENDER_IDENTITY_MISSING", "Stalwart sender identity is unavailable")
        email_id = f"email-{idempotency_key[-32:]}"
        submission_id = f"submission-{idempotency_key[-32:]}"
        email = {
            "mailboxIds": {draft_id: True},
            "keywords": {"$draft": True},
            "from": [{"email": sender}],
            "to": [{"email": item} for item in recipients],
            "subject": subject,
            "bodyStructure": {"type": "text/plain", "partId": "body"},
            "bodyValues": {"body": {"value": body, "isTruncated": False}},
        }
        calls = [
            ["Email/set", {"accountId": account_id, "create": {email_id: email}}, "create-email"],
            ["EmailSubmission/set", {
                "accountId": account_id,
                "create": {submission_id: {
                    "emailId": f"#{email_id}", "identityId": identity_id,
                    "envelope": {"mailFrom": {"email": sender}, "rcptTo": [{"email": item} for item in recipients]},
                }},
                "onSuccessUpdateEmail": {f"#{submission_id}": {
                    f"mailboxIds/{draft_id}": None,
                    f"mailboxIds/{sent_id}": True,
                    "keywords/$draft": None,
                    "keywords/$seen": True,
                }},
            }, "submit-email"],
        ]
        data, correlation_id = await self._mail_call(calls, idempotency_key=idempotency_key)
        self._require_set_created(self._response_for(data, "Email/set"), email_id)
        submission = self._require_set_created(self._response_for(data, "EmailSubmission/set"), submission_id)
        provider_message_id = str(submission["id"])
        return {"correlation_id": correlation_id, "provider_message_id": provider_message_id, "result": redact(data)}

    async def get_outbound_queue_status(self, account_id: str, provider_message_id: str) -> dict[str, Any]:
        data, correlation_id = await self._mail_call([["EmailSubmission/get", {"accountId": account_id, "ids": [provider_message_id]}, "submission-status"]])
        return {"correlation_id": correlation_id, "result": redact(self._first_response(data))}

    async def cancel_queued_message(self, account_id: str, provider_message_id: str) -> dict[str, Any]:
        data, correlation_id = await self._mail_call([["EmailSubmission/set", {"accountId": account_id, "destroy": [provider_message_id]}, "cancel-submission"]])
        result = self._first_response(data)
        if provider_message_id not in (result.get("destroyed") or []):
            raise StalwartAdapterError("STALWART_SUBMISSION_NOT_CANCELLABLE", "Stalwart submission could not be cancelled")
        return {"correlation_id": correlation_id, "result": redact(result)}
