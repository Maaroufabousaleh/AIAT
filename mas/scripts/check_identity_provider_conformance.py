"""Run the bounded identity-provider adapter conformance fixture.

The fixture drives the real Stalwart and Resend adapters through mocked HTTP
responses.  It covers the provider-specific request/response contracts,
passwordless mailbox reconciliation, separate mail-service authentication,
submission/cancellation, relay-domain validation, webhook normalization, and
transient/permanent failure classification.  ``httpx.MockTransport`` is used
for every call: no provider network, account, recipient, or mutation is
performed and no provider payload is retained in the report.

External relay delivery, outage, restore, and operator-owned account evidence
remain separate live boundaries.  Licence metadata is provenance only.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any

import httpx

IDENTITY_ROOT = Path(__file__).resolve().parents[1] / "apps" / "identity-service"
if str(IDENTITY_ROOT) not in sys.path:
    sys.path.insert(0, str(IDENTITY_ROOT))

from identity_service.providers.resend import ResendRelayAdapter  # noqa: E402
from identity_service.providers.stalwart import StalwartAdapter, StalwartAdapterError  # noqa: E402

CHECK_SCHEMA = "aiat.identity-provider-conformance.v1"
FAKE_MANAGEMENT_TOKEN = "fixture-management-token"
FAKE_MAIL_TOKEN = "fixture-mail-token"
FAKE_RESEND_TOKEN = "fixture-resend-token"


def _response(method_responses: list[list[Any]]) -> httpx.Response:
    return httpx.Response(200, json={"methodResponses": method_responses})


async def _run_stalwart() -> tuple[list[str], dict[str, bool]]:
    cases: list[str] = []
    checks: dict[str, bool] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        methods = [str(call[0]) for call in payload.get("methodCalls", [])]
        first = methods[0] if methods else ""
        if first == "Core/echo":
            return _response([["Core/echo", {"ping": "ok"}, "health"]])
        if first == "x:Domain/query":
            return _response([[first, {"ids": ["domain-1"]}, "domain"]])
        if first == "x:Account/set":
            create = payload["methodCalls"][0][1].get("create") or {}
            creation_id = next(iter(create), "identity-fixture")
            return _response([[first, {"created": {creation_id: {"id": "account-1"}}}, "account"]])
        if first == "x:Account/query":
            return _response([[first, {"ids": ["account-1"]}, "accounts"]])
        if first == "x:Account/get":
            return _response([[first, {"list": [{
                "id": "account-1",
                "name": "worker-1",
                "domainId": "domain-1",
                "emailAddress": "worker-1@agents.example",
                "credentials": [],
            }]}, "account-read"]])
        if first == "Email/query":
            if request.headers.get("Authorization") != f"Bearer {FAKE_MAIL_TOKEN}":
                return httpx.Response(401, json={"error": "wrong fixture token"})
            return _response([[first, {"ids": []}, "mail-list"]])
        if first == "Mailbox/get":
            return _response([
                ["Mailbox/get", {"list": [
                    {"id": "drafts-1", "role": "drafts"},
                    {"id": "sent-1", "role": "sent"},
                ]}, "mailboxes"],
                ["Identity/get", {"list": [{"id": "identity-1", "email": "worker-1@agents.example"}]}, "identities"],
            ])
        if first == "Email/set":
            if "EmailSubmission/set" in methods:
                email_create = payload["methodCalls"][0][1].get("create") or {}
                submission_create = payload["methodCalls"][1][1].get("create") or {}
                email_id = next(iter(email_create), "email-fixture")
                submission_id = next(iter(submission_create), "submission-fixture")
                return _response([
                    ["Email/set", {"created": {email_id: {"id": "email-1"}}}, "email"],
                    ["EmailSubmission/set", {"created": {submission_id: {"id": "submission-1"}}}, "submission"],
                ])
            create = payload["methodCalls"][0][1].get("create") or {}
            creation_id = next(iter(create), "email-fixture")
            return _response([[first, {"created": {creation_id: {"id": "email-1"}}}, "email"]])
        if first == "EmailSubmission/set":
            arguments = payload["methodCalls"][0][1]
            if arguments.get("destroy"):
                return _response([[first, {"destroyed": ["submission-1"]}, "cancel"]])
            email_create = payload["methodCalls"][0][1].get("create") or {}
            submission_create = payload["methodCalls"][1][1].get("create") or {}
            email_id = next(iter(email_create), "email-fixture")
            submission_id = next(iter(submission_create), "submission-fixture")
            return _response([
                ["Email/set", {"created": {email_id: {"id": "email-1"}}}, "email"],
                [first, {"created": {submission_id: {"id": "submission-1"}}}, "submission"],
            ])
        return httpx.Response(404, json={"error": "unsupported fixture method"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StalwartAdapter(
            base_url="https://mail.example",
            api_key=FAKE_MANAGEMENT_TOKEN,
            jmap_service_token=FAKE_MAIL_TOKEN,
            client=client,
        )
        health = await adapter.health_check()
        checks["stalwart_health"] = health.get("healthy") is True
        cases.append("stalwart_health")

        created = await adapter.create_mailbox(
            "worker-1@agents.example", quota_mb=100, idempotency_key="fixture-mailbox-1"
        )
        checks["stalwart_passwordless_provisioning"] = created.get("provider_account_id") == "account-1"
        cases.append("stalwart_passwordless_provisioning")

        found = await adapter.find_mailbox("worker-1@agents.example")
        checks["stalwart_exact_mailbox_reconciliation"] = found is not None and found.get("provider_account_id") == "account-1"
        cases.append("stalwart_exact_mailbox_reconciliation")

        messages = await adapter.list_messages("account-1")
        checks["stalwart_separate_mail_token"] = messages.get("result", {}).get("ids") == []
        cases.append("stalwart_separate_mail_token")

        submission = await adapter.submit_outbound_message(
            "account-1",
            sender="worker-1@agents.example",
            recipients=["recipient@example.net"],
            subject="fixture subject",
            body="fixture body must not enter the report",
            idempotency_key="fixture-submit-1",
        )
        checks["stalwart_submission"] = submission.get("provider_message_id") == "submission-1"
        cases.append("stalwart_submission")

        cancelled = await adapter.cancel_queued_message("account-1", "submission-1")
        checks["stalwart_submission_cancellation"] = cancelled.get("result", {}).get("destroyed") == ["submission-1"]
        cases.append("stalwart_submission_cancellation")

    async def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="provider detail is not retained")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        failing = StalwartAdapter(base_url="https://mail.example", api_key=FAKE_MANAGEMENT_TOKEN, client=client)
        try:
            await failing.health_check()
        except StalwartAdapterError as exc:
            checks["stalwart_transient_failure_classification"] = (
                exc.code == "STALWART_UNAVAILABLE" and exc.transient is True
            )
        else:
            checks["stalwart_transient_failure_classification"] = False
        cases.append("stalwart_transient_failure_classification")

    return cases, checks


async def _run_resend() -> tuple[list[str], dict[str, bool]]:
    cases: list[str] = []
    checks: dict[str, bool] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/domains":
            return httpx.Response(404, json={"error": "unsupported fixture path"})
        return httpx.Response(200, json={"data": [{"id": "domain-1", "name": "agents.example", "status": "verified"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ResendRelayAdapter(
            api_key=FAKE_RESEND_TOKEN,
            sending_domain="agents.example",
            client=client,
        )
        domain = await adapter.validate_sending_domain()
        checks["resend_domain_validation"] = domain.get("valid") is True and domain.get("domain_id") == "domain-1"
        cases.append("resend_domain_validation")

    secret = b"fixture-webhook-secret"
    encoded_secret = "whsec_" + base64.b64encode(secret).decode("ascii")
    body = b'{"id":"evt-1","type":"email.delivered"}'
    message_id = "msg-1"
    timestamp = 1_700_000_000
    signed = f"{message_id}.{timestamp}.".encode("ascii") + body
    signature = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode("ascii")
    headers = {
        "svix-id": message_id,
        "svix-timestamp": str(timestamp),
        "svix-signature": f"v1,{signature}",
    }
    checks["resend_webhook_signature"] = ResendRelayAdapter.verify_webhook_signature(
        body, headers, signing_secret=encoded_secret, now=timestamp + 10
    )
    cases.append("resend_webhook_signature")
    observation = ResendRelayAdapter.normalize_webhook(
        {"id": "evt-1", "type": "email.delivered", "data": {"status": "delivered", "body": "drop"}},
        signature_verified=True,
    )
    checks["resend_payload_free_normalization"] = (
        observation.event_type == "delivered"
        and observation.metadata == {"provider_event_type": "email.delivered", "provider_status": "delivered"}
    )
    cases.append("resend_payload_free_normalization")
    checks["resend_failure_classification"] = (
        ResendRelayAdapter.classify_transient_or_permanent_failure(503) == "transient"
        and ResendRelayAdapter.classify_transient_or_permanent_failure(400) == "permanent"
    )
    cases.append("resend_failure_classification")
    return cases, checks


async def _fixture() -> dict[str, Any]:
    cases: list[str] = []
    checks: dict[str, bool] = {}
    errors: list[dict[str, str]] = []
    for provider, runner in (("stalwart", _run_stalwart), ("resend", _run_resend)):
        try:
            provider_cases, provider_checks = await runner()
        except Exception as exc:  # pragma: no cover - defensive fixture boundary
            errors.append({"provider": provider, "error_type": type(exc).__name__})
            continue
        cases.extend(provider_cases)
        checks.update(provider_checks)
    passed = sum(bool(value) for value in checks.values())
    errors.extend(
        {"case": name, "error_type": "AssertionError"}
        for name, value in checks.items()
        if not value
    )
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "mode": "mocked-provider-conformance",
        "status": "pass" if not errors and passed == len(cases) else "fail",
        "providers": ["resend", "stalwart"],
        "case_count": len(cases),
        "passed_case_count": passed,
        "error_count": len(errors),
        "errors": errors,
        "failure_classification": {
            "provider_transient_failure": "checked",
            "provider_permanent_failure": "checked",
            "external_network_access": "not_performed",
            "external_provider_mutation": "not_performed",
        },
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "secret_safe_report": True,
        "payload_free": True,
        "licence_metadata_is_gate": False,
        "scope": "mocked Stalwart/Resend adapter conformance only; live relay/account/outage/restore evidence remains separate",
    }
    rendered = json.dumps(report, sort_keys=True)
    if any(marker in rendered for marker in (FAKE_MANAGEMENT_TOKEN, FAKE_MAIL_TOKEN, FAKE_RESEND_TOKEN, "fixture body")):
        report["status"] = "fail"
        report["secret_safe_report"] = False
        report["error_count"] = int(report["error_count"]) + 1
        report["errors"].append({"case": "secret_safe_report", "error_type": "AssertionError"})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--live", action="store_true", help="require external provider evidence")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "live provider account, relay, outage, and restore evidence requires operator-selected endpoints and credentials",
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
            "licence_metadata_is_gate": False,
        }
        exit_code = 2
    else:
        report = asyncio.run(_fixture())
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"identity-provider conformance: {report['status']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
