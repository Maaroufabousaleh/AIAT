from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "mas/infra/smtp-gateway/scripts/validate-stalwart-certification-credentials.py"
SPEC = importlib.util.spec_from_file_location("credential_validation", SCRIPT)
assert SPEC and SPEC.loader
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


def basic(username: str = validation.EXPECTED_ADDRESS, password: str = "app-password") -> str:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def credentials() -> dict[str, str]:
    return {
        "STALWART_API_KEY": "management-key",
        "STALWART_JMAP_SERVICE_TOKEN": basic(),
    }


def responses(account_id: str = "u123") -> list[dict]:
    return [
        {"permissions": sorted(validation.MANAGEMENT_PERMISSIONS)},
        {"apiUrl": "http://localhost:18080/jmap/"},
        {"methodResponses": [["x:Domain/query", {"ids": ["d1"]}, "domain"]]},
        {
            "methodResponses": [
                ["x:Account/query", {"ids": [account_id]}, "account"],
                ["x:MtaRoute/get", {"list": []}, "routes"],
                ["x:MtaOutboundStrategy/get", {"list": [{}]}, "strategy"],
            ]
        },
        {"permissions": sorted(validation.MAIL_PERMISSIONS)},
        {"apiUrl": "http://localhost:18080/jmap/"},
        {
            "methodResponses": [
                ["Mailbox/get", {"list": [{"id": "m1"}]}, "mailboxes"],
                [
                    "Identity/get",
                    {"list": [{"email": validation.EXPECTED_ADDRESS}]},
                    "identities",
                ],
            ]
        },
    ]


class FakeTransport:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def test_missing_credential_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(validation.Refused, match="missing"):
        validation.read_credentials(tmp_path / "missing")


def test_invalid_mail_credential_type_is_rejected() -> None:
    value = credentials()
    value["STALWART_JMAP_SERVICE_TOKEN"] = "Bearer management-key"
    with pytest.raises(validation.Refused, match="Basic application-password"):
        validation.validate_live(value, "u123", FakeTransport(responses()))


def test_invalid_live_credential_is_rejected() -> None:
    with pytest.raises(validation.Refused, match="rejected"):
        validation.validate_live(
            credentials(),
            "u123",
            FakeTransport([validation.Refused("Stalwart rejected a certification credential or request")]),
        )


def test_overprivileged_management_key_is_rejected() -> None:
    values = responses()
    values[0]["permissions"].append("sysAccountDestroy")
    with pytest.raises(validation.Refused, match="overprivileged"):
        validation.validate_live(credentials(), "u123", FakeTransport(values))


def test_missing_mail_permission_is_rejected() -> None:
    values = responses()
    values[4]["permissions"].remove("jmapEmailSubmissionCreate")
    with pytest.raises(validation.Refused, match="missing required permissions"):
        validation.validate_live(credentials(), "u123", FakeTransport(values))


def test_wrong_account_id_is_rejected() -> None:
    with pytest.raises(validation.Refused, match="does not belong"):
        validation.validate_live(credentials(), "wrong", FakeTransport(responses()))


def test_valid_least_privilege_credentials_pass() -> None:
    transport = FakeTransport(responses())
    validation.validate_live(credentials(), "u123", transport)
    assert all(
        not (kwargs.get("payload") is not None and url.endswith("/api"))
        for (url, _authorization), kwargs in transport.calls
    )
    assert [url for (url, _authorization), _kwargs in transport.calls if url.endswith("/jmap/")] == [
        f"{validation.EXPECTED_URL}/jmap/",
        f"{validation.EXPECTED_URL}/jmap/",
        f"{validation.EXPECTED_URL}/jmap/",
    ]


def test_session_authority_is_normalized_and_path_query_preserved() -> None:
    assert validation.resolve_jmap_api_url(
        validation.EXPECTED_URL,
        "http://localhost:18080/jmap/?capabilities=1",
    ) == "http://127.0.0.1:18080/jmap/?capabilities=1"


def test_lookup_account_id_uses_discovered_jmap_endpoint() -> None:
    transport = FakeTransport(responses())
    assert validation.lookup_account_id(credentials(), transport) == "u123"
    payload_calls = [
        (url, kwargs)
        for (url, _authorization), kwargs in transport.calls
        if kwargs.get("payload") is not None
    ]
    assert payload_calls
    assert all(url == f"{validation.EXPECTED_URL}/jmap/" for url, _kwargs in payload_calls)


def test_malformed_session_response_is_sanitized() -> None:
    transport = FakeTransport(
        [
            {"permissions": sorted(validation.MANAGEMENT_PERMISSIONS)},
            {"primaryAccounts": {"urn:stalwart:jmap": "u123"}},
        ]
    )
    with pytest.raises(validation.Refused, match="malformedJmapSession"):
        validation.lookup_account_id(credentials(), transport)


def test_http_failure_context_contains_no_credential(monkeypatch) -> None:
    secret = "API_" + "a" * 40
    body = b'{"type":"forbidden","description":"bad request"}'

    def rejected(_message, timeout):
        from io import BytesIO
        from urllib.error import HTTPError

        raise HTTPError(
            f"{validation.EXPECTED_URL}/jmap/",
            403,
            "Forbidden",
            {},
            BytesIO(body),
        )

    monkeypatch.setattr(validation.request, "urlopen", rejected)
    transport = validation.HttpTransport([secret, "Basic private-token"])
    with pytest.raises(validation.Refused) as failure:
        transport.json(
            f"{validation.EXPECTED_URL}/jmap/",
            f"Bearer {secret}",
            payload={"methodCalls": [["x:Domain/query", {}, "domain"]]},
            jmap_method="x:Domain/query",
        )
    message = str(failure.value)
    assert "ENDPOINT_PATH=/jmap/" in message
    assert "HTTP_STATUS=403" in message
    assert "JMAP_METHOD=x:Domain/query" in message
    assert "JMAP_ERROR_TYPE=forbidden" in message
    assert secret not in message
    assert "private-token" not in message


def test_jmap_failure_context_is_sanitized(monkeypatch) -> None:
    class JsonResponse:
        def read(self, _limit=-1):
            return b'{"methodResponses":[["error",{"type":"forbidden","description":"denied"},"domain"]]}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(validation.request, "urlopen", lambda _message, timeout: JsonResponse())
    transport = validation.HttpTransport(["API_redacted"])
    with pytest.raises(validation.Refused) as failure:
        transport.json(
            f"{validation.EXPECTED_URL}/jmap/",
            "Bearer API_redacted",
            payload={"methodCalls": [["x:Domain/query", {}, "domain"]]},
            jmap_method="x:Domain/query",
        )
    message = str(failure.value)
    assert "ENDPOINT_PATH=/jmap/" in message
    assert "HTTP_STATUS=200" in message
    assert "JMAP_METHOD=x:Domain/query" in message
    assert "JMAP_ERROR_TYPE=forbidden" in message
    assert "API_redacted" not in message
