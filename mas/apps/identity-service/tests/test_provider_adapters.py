from __future__ import annotations

import json

import httpx
import pytest
from identity_service.providers.resend import ResendRelayAdapter
from identity_service.providers.stalwart import StalwartAdapter, StalwartAdapterError


@pytest.mark.anyio
async def test_stalwart_adapter_uses_jmap_idempotency_and_redacts_provider_failures() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.setdefault("paths", []).append(request.url.path)
        body = json.loads(request.content)
        if body["methodCalls"][0][0] == "x:Domain/query":
            return httpx.Response(200, json={"methodResponses": [["x:Domain/query", {"ids": ["domain-1"]}, "find-domain"]]})
        observed["idempotency"] = request.headers.get("Idempotency-Key")
        observed["body"] = body
        creation_id = next(iter(body["methodCalls"][0][1]["create"]))
        return httpx.Response(200, json={
            "methodResponses": [["x:Account/set", {"created": {creation_id: {"id": "a-1"}}}, "create-mailbox"]],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(base_url="https://mail.example", api_key="not-returned", client=client)
        result = await adapter.create_mailbox("w-1@agents.example", quota_mb=100, idempotency_key="mailbox:identity-job")

    assert observed["paths"] == ["/jmap", "/jmap"]
    assert observed["idempotency"] == "mailbox:identity-job"
    assert result["provider_account_id"] == "a-1"
    assert "x:Account/set" in str(observed["body"])
    assert observed["body"]["using"] == ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]  # type: ignore[index]
    account = next(iter(observed["body"]["methodCalls"][0][1]["create"].values()))  # type: ignore[index]
    assert account["domainId"] == "domain-1"
    assert account["credentials"] == {}
    assert account["permissions"]["@type"] == "Replace"
    assert account["permissions"]["enabledPermissions"]["emailReceive"] is True
    assert account["quotas"]["maxDiskQuota"] == 100 * 1024 * 1024


@pytest.mark.anyio
async def test_stalwart_adapter_reconciles_only_exact_passwordless_mailbox() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["methodCalls"][0][0]
        responses = {
            "x:Domain/query": {"ids": ["domain-1"]},
            "x:Account/query": {"ids": ["account-1"]},
            "x:Account/get": {"list": [{
                "id": "account-1",
                "name": "w-1",
                "domainId": "domain-1",
                "emailAddress": "w-1@agents.example",
                "credentials": [],
            }]},
        }
        return httpx.Response(200, json={
            "methodResponses": [[method, responses[method], "reconcile"]],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(
            base_url="https://mail.example",
            api_key="management-api-key",
            client=client,
        )
        result = await adapter.find_mailbox("w-1@agents.example")

    assert result and result["provider_account_id"] == "account-1"


@pytest.mark.anyio
async def test_stalwart_adapter_refuses_to_adopt_mailbox_with_login_credentials() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["methodCalls"][0][0]
        responses = {
            "x:Domain/query": {"ids": ["domain-1"]},
            "x:Account/query": {"ids": ["account-1"]},
            "x:Account/get": {"list": [{
                "id": "account-1",
                "name": "w-1",
                "domainId": "domain-1",
                "emailAddress": "w-1@agents.example",
                "credentials": [{"@type": "Password", "secret": "must-not-escape"}],
            }]},
        }
        return httpx.Response(200, json={
            "methodResponses": [[method, responses[method], "reconcile"]],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(
            base_url="https://mail.example",
            api_key="management-api-key",
            client=client,
        )
        with pytest.raises(StalwartAdapterError, match="cannot be adopted"):
            await adapter.find_mailbox("w-1@agents.example")


@pytest.mark.anyio
async def test_stalwart_adapter_exposes_only_sanitized_structured_failure() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="provider detail must not escape")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(base_url="https://mail.example", api_key="secret-api-key", client=client)
        with pytest.raises(StalwartAdapterError) as error:
            await adapter.health_check()

    assert error.value.code == "STALWART_UNAVAILABLE"
    assert "secret-api-key" not in str(error.value)
    assert "provider detail" not in str(error.value)


@pytest.mark.anyio
async def test_stalwart_mail_operations_use_the_separate_jmap_service_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jmap"
        assert request.headers["Authorization"] == "Bearer mail-service-token"
        payload = json.loads(request.content)
        assert "filter" not in payload["methodCalls"][0][1]
        return httpx.Response(200, json={"methodResponses": [["Email/query", {"ids": []}, "list-mail"]]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(
            base_url="https://mail.example", api_key="management-api-key",
            jmap_service_token="mail-service-token", client=client,
        )
        result = await adapter.list_messages("a-1")

    assert result["result"]["ids"] == []


@pytest.mark.anyio
async def test_stalwart_verification_wait_uses_structured_sender_filter_and_fetches_body() -> None:
    observed: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed.append(payload)
        method = payload["methodCalls"][0][0]
        if method == "Email/query":
            return httpx.Response(200, json={"methodResponses": [[method, {"ids": ["message-1"]}, "wait-verification"]]})
        return httpx.Response(200, json={"methodResponses": [[method, {
            "list": [{"id": "message-1", "bodyValues": {"body": {"value": "Code 123456"}}}],
        }, "read-mail"]]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(
            base_url="https://mail.example", api_key="management-api-key",
            jmap_service_token="mail-service-token", client=client,
        )
        result = await adapter.wait_for_message("account-1", sender_domain="verification.example", timeout_seconds=1)

    assert result and result["result"]["list"][0]["id"] == "message-1"
    assert observed[0]["methodCalls"][0][1]["filter"] == {"from": "verification.example"}
    read_arguments = observed[1]["methodCalls"][0][1]
    assert read_arguments["fetchTextBodyValues"] is True
    assert read_arguments["fetchHTMLBodyValues"] is True


@pytest.mark.anyio
async def test_stalwart_submission_uses_rfc_mail_shape_and_sender_identity() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jmap"
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["methodCalls"][0][0] == "Mailbox/get":
            return httpx.Response(200, json={"methodResponses": [
                ["Mailbox/get", {"list": [{"id": "drafts-1", "role": "drafts"}, {"id": "sent-1", "role": "sent"}]}, "submission-mailboxes"],
                ["Identity/get", {"list": [{"id": "identity-1", "email": "w-1@agents.example"}]}, "submission-identities"],
            ]})
        return httpx.Response(200, json={"methodResponses": [
            ["Email/set", {"created": {"email-submit-1": {"id": "email-1"}}}, "create-email"],
            ["EmailSubmission/set", {"created": {"submission-submit-1": {"id": "submission-1", "undoStatus": "pending"}}}, "submit-email"],
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(
            base_url="https://mail.example", api_key="management-api-key",
            jmap_service_token="mail-service-token", client=client,
        )
        result = await adapter.submit_outbound_message(
            "account-1", sender="w-1@agents.example", recipients=["recipient@example.net"],
            subject="Subject", body="Body", idempotency_key="submit-1",
        )

    assert result["provider_message_id"] == "submission-1"
    submission_request = requests[1]
    assert "urn:ietf:params:jmap:submission" in submission_request["using"]
    email = submission_request["methodCalls"][0][1]["create"]["email-submit-1"]
    assert email["mailboxIds"] == {"drafts-1": True}
    assert email["bodyStructure"] == {"type": "text/plain", "partId": "body"}
    submission = submission_request["methodCalls"][1][1]
    assert submission["create"]["submission-submit-1"]["identityId"] == "identity-1"
    assert submission["onSuccessUpdateEmail"]["#submission-submit-1"]["mailboxIds/sent-1"] is True


@pytest.mark.anyio
async def test_stalwart_submission_cancel_uses_mail_jmap_and_requires_destroy_evidence() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/jmap"
        assert payload["methodCalls"][0][0] == "EmailSubmission/set"
        assert payload["methodCalls"][0][1]["accountId"] == "account-1"
        return httpx.Response(200, json={"methodResponses": [[
            "EmailSubmission/set", {"destroyed": ["submission-1"]}, "cancel-submission",
        ]]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(
            base_url="https://mail.example", api_key="management-api-key",
            jmap_service_token="mail-service-token", client=client,
        )
        result = await adapter.cancel_queued_message("account-1", "submission-1")

    assert result["result"]["destroyed"] == ["submission-1"]


@pytest.mark.anyio
async def test_resend_adapter_validates_domain_without_exposing_api_key() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/domains"
        return httpx.Response(200, json={"data": [{"id": "domain-1", "name": "agents.example", "status": "verified"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ResendRelayAdapter(api_key="secret-api-key", sending_domain="agents.example", client=client)
        result = await adapter.validate_sending_domain()

    assert result["valid"] is True
    assert result["domain_id"] == "domain-1"
    assert "secret-api-key" not in json.dumps(result)
    assert adapter.classify_transient_or_permanent_failure(503) == "transient"
    assert adapter.classify_transient_or_permanent_failure(400) == "permanent"
