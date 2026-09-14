from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

MAS_ROOT = Path(__file__).resolve().parents[1]
if str(MAS_ROOT) not in sys.path:
    sys.path.insert(0, str(MAS_ROOT))

import certify_resend_live as certificate  # noqa: E402
from identity_service.providers.resend import ResendRelayAdapter  # noqa: E402


def _args(command: str, *, confirm_send: bool = False) -> Namespace:
    return Namespace(command=command, confirm_send=confirm_send, timeout=15.0)


def _transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/domains":
        return httpx.Response(
            200,
            json={"data": [{"id": "domain-reference", "name": "agents.aiat.ca", "status": "verified"}]},
            request=request,
        )
    if request.url.path == "/emails":
        return httpx.Response(200, json={"id": "re_certification_message"}, request=request)
    return httpx.Response(404, json={}, request=request)


def _adapter() -> ResendRelayAdapter:
    return ResendRelayAdapter(
        api_key="fixture-api-key",
        sending_domain="agents.aiat.ca",
        client=httpx.AsyncClient(transport=httpx.MockTransport(_transport)),
        webhook_signing_secret="whsec_" + "c2lnbmluZy1maXh0dXJl",
    )


def _restricted_adapter() -> ResendRelayAdapter:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/domains":
            return httpx.Response(
                401,
                json={"name": "restricted_api_key", "statusCode": 401},
                request=request,
            )
        if request.url.path == "/emails":
            return httpx.Response(200, json={"id": "re_sending_access_message"}, request=request)
        return httpx.Response(404, json={}, request=request)

    return ResendRelayAdapter(
        api_key="fixture-sending-access-key",
        sending_domain="agents.aiat.ca",
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        webhook_signing_secret="whsec_" + "c2lnbmluZy1maXh0dXJl",
    )


def test_readiness_is_bounded_and_does_not_return_provider_payload(monkeypatch) -> None:
    monkeypatch.setattr(certificate, "_dns_summary", lambda domain: {
        "domain_txt": {"available": True, "present": True, "record_count": 1},
        "domain_mx": {"available": True, "present": True, "record_count": 1},
        "dmarc_txt": {"available": True, "present": True, "record_count": 1},
    })

    async def run() -> dict:
        adapter = _adapter()
        try:
            return await certificate.execute_command(_args("readiness"), adapter)
        finally:
            await adapter._client.aclose()

    import asyncio

    report = asyncio.run(run())
    assert report["status"] == "PASS"
    assert report["api_auth"] == "PASS"
    assert report["domain_status_accepted"] is True
    assert report["webhook_secret_format"] == "PASS"
    assert "data" not in json.dumps(report)
    assert "fixture-api-key" not in json.dumps(report)


def test_send_once_requires_explicit_confirmation_without_calling_provider(monkeypatch) -> None:
    calls = 0

    async def fake_readiness(adapter):
        nonlocal calls
        calls += 1
        return {"status": "PASS"}

    monkeypatch.setattr(certificate, "_readiness", fake_readiness)

    import asyncio

    async def run() -> dict:
        adapter = _adapter()
        try:
            return await certificate.execute_command(_args("send-once"), adapter)
        finally:
            await adapter._client.aclose()

    with pytest.raises(certificate.CertificationInputError, match="confirm-send"):
        asyncio.run(run())
    assert calls == 0


def test_readiness_accepts_known_restricted_sending_key(monkeypatch) -> None:
    monkeypatch.setattr(certificate, "_dns_summary", lambda domain: {
        "domain_txt": {"available": True, "present": True, "record_count": 1},
        "domain_mx": {"available": True, "present": True, "record_count": 1},
        "dmarc_txt": {"available": True, "present": True, "record_count": 1},
    })

    import asyncio

    async def run() -> dict:
        adapter = _restricted_adapter()
        try:
            return await certificate.execute_command(_args("readiness"), adapter)
        finally:
            await adapter._client.aclose()

    report = asyncio.run(run())
    assert report["status"] == "PASS"
    assert report["api_auth"] == "PASS"
    assert report["api_auth_scope"] == "sending_access"
    assert report["domain_status_accepted"] == "NOT_AVAILABLE_RESTRICTED_API_KEY"
    assert report["domain_readable"] is False


def test_send_once_uses_bounded_send_endpoint_for_restricted_key(monkeypatch) -> None:
    monkeypatch.setenv("AIAT_CERTIFICATION_RECIPIENT", "operator@example.net")
    monkeypatch.setenv("AIAT_CERTIFICATION_SENDER", "certification@agents.aiat.ca")
    monkeypatch.setenv("AIAT_CERTIFICATION_IDEMPOTENCY_KEY", "restricted-send-fixture")

    import asyncio

    async def run() -> dict:
        adapter = _restricted_adapter()
        try:
            return await certificate.execute_command(
                _args("send-once", confirm_send=True), adapter
            )
        finally:
            await adapter._client.aclose()

    report = asyncio.run(run())
    assert report["status"] == "PASS"
    assert report["send_count"] == 1
    assert report["provider_message_id"] == "re_sending_access_message"


def test_send_once_reports_only_safe_provider_metadata(monkeypatch) -> None:
    monkeypatch.setenv("AIAT_CERTIFICATION_RECIPIENT", "operator@example.net")
    monkeypatch.setenv("AIAT_CERTIFICATION_SENDER", "certification@agents.aiat.ca")
    monkeypatch.setenv("AIAT_CERTIFICATION_IDEMPOTENCY_KEY", "resend-certification-fixture")

    import asyncio

    async def _passed_readiness(adapter):
        del adapter
        return {"status": "PASS"}

    async def run() -> dict:
        adapter = _adapter()
        try:
            return await certificate.execute_command(
                _args("send-once", confirm_send=True), adapter
            )
        finally:
            await adapter._client.aclose()

    # Rebind after defining the async fixture so the monkeypatch remains a
    # coroutine and no real network is attempted.
    monkeypatch.setattr(certificate, "_readiness", _passed_readiness)
    report = asyncio.run(run())
    serialized = json.dumps(report)
    assert report["status"] == "PASS"
    assert report["send_count"] == 1
    assert report["provider_message_id"] == "re_certification_message"
    assert report["governed_identity_service_path"] is False
    assert "operator@example.net" not in serialized
    assert "AIAT mail transport certification" not in serialized


def test_cli_fails_closed_without_resend_secrets(monkeypatch, capsys) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_WEBHOOK_SIGNING_SECRET", raising=False)

    assert certificate.main(["--json", "readiness"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["error_code"] == "CERTIFICATION_INPUT_INVALID"
    assert "fixture-api-key" not in json.dumps(report)
