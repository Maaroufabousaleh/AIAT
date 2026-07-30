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
        {"methodResponses": [["x:Domain/query", {"ids": ["d1"]}, "domain"]]},
        {
            "methodResponses": [
                ["x:Account/query", {"ids": [account_id]}, "account"],
                ["x:MtaRoute/get", {"list": []}, "routes"],
                ["x:MtaOutboundStrategy/get", {"list": [{}]}, "strategy"],
            ]
        },
        {"permissions": sorted(validation.MAIL_PERMISSIONS)},
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

    def json(self, *_args, **_kwargs):
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
    values[3]["permissions"].remove("jmapEmailSubmissionCreate")
    with pytest.raises(validation.Refused, match="missing required permissions"):
        validation.validate_live(credentials(), "u123", FakeTransport(values))


def test_wrong_account_id_is_rejected() -> None:
    with pytest.raises(validation.Refused, match="does not belong"):
        validation.validate_live(credentials(), "wrong", FakeTransport(responses()))


def test_valid_least_privilege_credentials_pass() -> None:
    validation.validate_live(credentials(), "u123", FakeTransport(responses()))
