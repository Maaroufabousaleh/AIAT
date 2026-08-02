from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

GATEWAY = Path(__file__).resolve().parents[1]
SCRIPT = GATEWAY / "scripts" / "stalwart_route_lifecycle_credentials.py"
SPEC = importlib.util.spec_from_file_location("route_lifecycle_credentials", SCRIPT)
assert SPEC and SPEC.loader
credentials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credentials)


def _envelope(method: str, result: dict[str, Any], tag: str) -> dict[str, Any]:
    return {
        "methodResponses": [[method, result, tag]],
        "sessionState": "session-state",
    }


class RouteCredentialTransport:
    def __init__(self, permissions=None, *, revoke=False):
        self.permissions = permissions or {
            "@type": "Replace",
            "permissions": {name: True for name in credentials.ROUTE_KEY_PERMISSIONS},
        }
        self.revoked = revoke
        self.calls: list[tuple[str, str]] = []

    def json(self, url: str, authorization: str | None = None, **kwargs: Any) -> dict[str, Any]:
        payload = kwargs.get("payload")
        self.calls.append((url, payload["methodCalls"][0][0] if payload else "session"))
        if payload is None:
            if self.revoked and authorization == "Bearer API_REDACTED_FOR_TEST":
                raise credentials.PROVISIONING.Refused("unauthorized")
            return {"apiUrl": "http://localhost:18080/jmap/"}
        method, arguments, tag = payload["methodCalls"][0]
        if method == "x:ApiKey/get":
            if self.revoked:
                return _envelope(
                    method,
                    {"list": [], "notFound": [arguments["ids"][0]]},
                    tag,
                )
            return _envelope(
                method,
                {
                    "list": [
                        {
                            "id": arguments["ids"][0],
                            "description": credentials.ROUTE_KEY_DESCRIPTION,
                            "permissions": self.permissions,
                        }
                    ],
                    "notFound": [],
                },
                tag,
            )
        if method == "x:ApiKey/query":
            return _envelope(
                method,
                {
                    "queryState": "query-state",
                    "canCalculateChanges": False,
                    "position": 0,
                    "ids": [],
                },
                tag,
            )
        if method == "x:MtaRoute/get":
            return _envelope(method, {"list": [], "notFound": []}, tag)
        if method == "x:MtaOutboundStrategy/get":
            return _envelope(
                method,
                {"list": [{"id": "singleton", "route": {}}], "notFound": []},
                tag,
            )
        raise AssertionError((method, arguments))


class AdminPermissionTransport:
    def __init__(self, permissions: dict[str, Any]) -> None:
        self.permissions = permissions
        self.calls: list[str] = []

    @staticmethod
    def _query(method: str, tag: str, ids: list[str]) -> dict[str, Any]:
        return _envelope(
            method,
            {
                "queryState": "query-state",
                "canCalculateChanges": False,
                "position": 0,
                "ids": ids,
            },
            tag,
        )

    def json(self, _url: str, _authorization: str | None = None, **kwargs: Any) -> dict[str, Any]:
        payload = kwargs["payload"]
        method, arguments, tag = payload["methodCalls"][0]
        self.calls.append(method)
        if method == "x:Domain/query":
            return self._query(method, tag, ["domain-id"])
        if method == "x:Account/query":
            return self._query(method, tag, ["account-id"])
        if method == "x:Account/get":
            return _envelope(
                method,
                {
                    "list": [
                        {
                            "id": "account-id",
                            "name": "admin",
                            "domainId": "domain-id",
                            "permissions": self.permissions,
                        }
                    ],
                    "notFound": [],
                },
                tag,
            )
        if method == "x:Account/set":
            self.permissions = arguments["update"]["account-id"]["permissions"]
            return _envelope(
                method,
                {"updated": {"account-id": {}}},
                tag,
            )
        raise AssertionError((method, arguments))


def _metadata(credential_id: str = "route-key-id") -> dict[str, Any]:
    return {
        "version": credentials.ROUTE_KEY_METADATA_VERSION,
        "purpose": "stalwart-route-lifecycle",
        "credentialId": credential_id,
        "owner": credentials.PERMANENT_ADMINISTRATOR_ADDRESS,
        "description": credentials.ROUTE_KEY_DESCRIPTION,
        "expiresAt": "2026-08-01T12:00:00Z",
        "permissions": list(credentials.ROUTE_KEY_PERMISSIONS),
    }


def _write_files(
    tmp_path: Path, monkeypatch, metadata: dict[str, Any] | None = None
) -> tuple[Path, Path, str]:
    monkeypatch.setattr(credentials, "_require_root_owned", lambda _path, **_kwargs: None)
    secret_file = tmp_path / "stalwart-route-lifecycle.env"
    metadata_file = tmp_path / "stalwart-route-lifecycle.meta"
    secret = "API_REDACTED_FOR_TEST"
    secret_file.write_text(f"{credentials.ROUTE_KEY_VARIABLE}={secret}\n", encoding="utf-8")
    metadata_file.write_text(json.dumps(metadata or _metadata()) + "\n", encoding="utf-8")
    return secret_file, metadata_file, secret


def test_exact_route_permissions_are_v01615_set_permissions() -> None:
    assert credentials.ROUTE_KEY_PERMISSIONS == (
        "authenticate",
        "sysMtaRouteGet",
        "sysMtaRouteCreate",
        "sysMtaRouteDestroy",
        "sysMtaOutboundStrategyGet",
        "sysMtaOutboundStrategyUpdate",
    )
    assert "sysMtaRouteSet" not in credentials.ROUTE_KEY_PERMISSIONS
    assert "sysMtaOutboundStrategySet" not in credentials.ROUTE_KEY_PERMISSIONS


def test_local_credential_requires_exact_protected_profile(tmp_path: Path, monkeypatch) -> None:
    secret_file, metadata_file, secret = _write_files(tmp_path, monkeypatch)
    loaded_secret, loaded_metadata = credentials.validate_local_files(secret_file, metadata_file)
    assert loaded_secret == secret
    assert loaded_metadata["owner"] == credentials.PERMANENT_ADMINISTRATOR_ADDRESS
    bad = _metadata()
    bad["permissions"] = list(credentials.ROUTE_KEY_PERMISSIONS) + ["sysAccountGet"]
    metadata_file.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(credentials.Refused, match="least privilege"):
        credentials.validate_local_files(secret_file, metadata_file)


def test_route_key_validation_uses_admin_record_and_read_only_route_probes(
    tmp_path: Path, monkeypatch
) -> None:
    secret_file, metadata_file, _secret = _write_files(tmp_path, monkeypatch)
    transport = RouteCredentialTransport()
    monkeypatch.setattr(credentials.PROVISIONING, "HttpTransport", lambda _state: transport)
    monkeypatch.setattr(credentials, "_admin_login", lambda **_kwargs: ("token", "Bearer admin"))
    monkeypatch.setattr(credentials, "_discover", lambda **_kwargs: "http://127.0.0.1:18080/jmap/")
    credentials.validate(
        base_url=credentials.LOCAL_URL,
        administrator_address=credentials.PERMANENT_ADMINISTRATOR_ADDRESS,
        administrator_password="redacted-admin-password",
        secret_file=secret_file,
        metadata_file=metadata_file,
        diagnostic=credentials.PROVISIONING.DiagnosticState(),
    )
    assert [method for _url, method in transport.calls] == [
        "x:ApiKey/get",
        "x:MtaRoute/get",
        "x:MtaOutboundStrategy/get",
    ]


def test_extra_effective_permission_is_rejected(tmp_path: Path, monkeypatch) -> None:
    secret_file, metadata_file, _secret = _write_files(tmp_path, monkeypatch)
    permissions = {
        "@type": "Replace",
        "permissions": {
            **{name: True for name in credentials.ROUTE_KEY_PERMISSIONS},
            "sysAccountQuery": True,
        },
    }
    transport = RouteCredentialTransport(permissions)
    with pytest.raises(credentials.Refused, match="unexpected effective permissions"):
        credentials._record_for_admin(
            transport=transport,
            jmap_url="http://127.0.0.1:18080/jmap/",
            admin_authorization="Bearer admin",
            credential_id="route-key-id",
            action="test",
        )
    assert secret_file.exists()
    assert metadata_file.exists()


@pytest.mark.parametrize(
    "missing", ["sysMtaRouteCreate", "sysMtaRouteDestroy", "sysMtaOutboundStrategyUpdate"]
)
def test_missing_mutation_permission_is_rejected(tmp_path: Path, monkeypatch, missing: str) -> None:
    _secret_file, _metadata_file, _secret = _write_files(tmp_path, monkeypatch)
    permissions = {
        "@type": "Replace",
        "permissions": {
            name: True for name in credentials.ROUTE_KEY_PERMISSIONS if name != missing
        },
    }
    with pytest.raises(credentials.Refused, match="unexpected effective permissions"):
        credentials._record_for_admin(
            transport=RouteCredentialTransport(permissions),
            jmap_url="http://127.0.0.1:18080/jmap/",
            admin_authorization="Bearer admin",
            credential_id="route-key-id",
            action="test",
        )


def test_strict_query_envelope_is_validated() -> None:
    transport = RouteCredentialTransport()
    credentials._strict_jmap(
        transport=transport,
        url="http://127.0.0.1:18080/jmap/",
        authorization="Bearer admin",
        payload=credentials._query_payload(),
        action="test-query",
    )


def test_revoke_method_error_preserves_protected_files(tmp_path: Path, monkeypatch) -> None:
    secret_file, metadata_file, _secret = _write_files(tmp_path, monkeypatch)
    transport = RouteCredentialTransport()
    monkeypatch.setattr(credentials.PROVISIONING, "HttpTransport", lambda _state: transport)
    monkeypatch.setattr(credentials, "_admin_login", lambda **_kwargs: ("token", "Bearer admin"))
    monkeypatch.setattr(credentials, "_discover", lambda **_kwargs: "http://127.0.0.1:18080/jmap/")

    def fail_destroy(**kwargs):
        raise credentials.Refused("JMAP response validation failed")

    monkeypatch.setattr(credentials, "_strict_jmap", fail_destroy)
    with pytest.raises(credentials.Refused):
        credentials.revoke(
            base_url=credentials.LOCAL_URL,
            administrator_address=credentials.PERMANENT_ADMINISTRATOR_ADDRESS,
            administrator_password="redacted-admin-password",
            secret_file=secret_file,
            metadata_file=metadata_file,
            diagnostic=credentials.PROVISIONING.DiagnosticState(),
        )
    assert secret_file.exists()
    assert metadata_file.exists()


def test_revoke_ambiguous_bearer_state_preserves_protected_files(
    tmp_path: Path, monkeypatch
) -> None:
    secret_file, metadata_file, _secret = _write_files(tmp_path, monkeypatch)
    transport = RouteCredentialTransport()
    monkeypatch.setattr(credentials.PROVISIONING, "HttpTransport", lambda _state: transport)
    monkeypatch.setattr(credentials, "_admin_login", lambda **_kwargs: ("token", "Bearer admin"))
    monkeypatch.setattr(credentials, "_discover", lambda **_kwargs: "http://127.0.0.1:18080/jmap/")

    def missing_key(**_kwargs: Any) -> dict[str, Any]:
        raise credentials.Refused("temporary route credential was not found exactly once")

    monkeypatch.setattr(credentials, "_record_for_admin", missing_key)
    with pytest.raises(credentials.Refused, match="ambiguous"):
        credentials.revoke(
            base_url=credentials.LOCAL_URL,
            administrator_address=credentials.PERMANENT_ADMINISTRATOR_ADDRESS,
            administrator_password="redacted-admin-password",
            secret_file=secret_file,
            metadata_file=metadata_file,
            diagnostic=credentials.PROVISIONING.DiagnosticState(),
        )
    assert secret_file.exists()
    assert metadata_file.exists()


def test_remove_sys_api_key_create_preserves_other_permissions(monkeypatch) -> None:
    transport = AdminPermissionTransport(
        {
            "@type": "Merge",
            "enabledPermissions": {
                "sysApiKeyCreate": True,
                "sysAccountUpdate": True,
                "sysMtaRouteGet": True,
            },
            "disabledPermissions": {},
        }
    )
    monkeypatch.setattr(credentials.PROVISIONING, "HttpTransport", lambda _state: transport)
    monkeypatch.setattr(credentials, "_admin_login", lambda **_kwargs: ("token", "Bearer admin"))
    monkeypatch.setattr(credentials, "_discover", lambda **_kwargs: "http://127.0.0.1:18080/jmap/")
    credentials.remove_sys_api_key_create(
        base_url=credentials.LOCAL_URL,
        administrator_address=credentials.PERMANENT_ADMINISTRATOR_ADDRESS,
        administrator_password="redacted-admin-password",
        diagnostic=credentials.PROVISIONING.DiagnosticState(),
    )
    assert transport.permissions == {
        "@type": "Merge",
        "enabledPermissions": {"sysAccountUpdate": True, "sysMtaRouteGet": True},
        "disabledPermissions": {"sysApiKeyCreate": True},
    }
    assert "x:Account/set" in transport.calls


def test_remove_sys_api_key_create_is_idempotent(monkeypatch) -> None:
    transport = AdminPermissionTransport(
        {
            "@type": "Replace",
            "enabledPermissions": {"sysAccountUpdate": True},
            "disabledPermissions": {"sysApiKeyCreate": True},
        }
    )
    monkeypatch.setattr(credentials.PROVISIONING, "HttpTransport", lambda _state: transport)
    monkeypatch.setattr(credentials, "_admin_login", lambda **_kwargs: ("token", "Bearer admin"))
    monkeypatch.setattr(credentials, "_discover", lambda **_kwargs: "http://127.0.0.1:18080/jmap/")
    credentials.remove_sys_api_key_create(
        base_url=credentials.LOCAL_URL,
        administrator_address=credentials.PERMANENT_ADMINISTRATOR_ADDRESS,
        administrator_password="redacted-admin-password",
        diagnostic=credentials.PROVISIONING.DiagnosticState(),
    )
    assert "x:Account/set" not in transport.calls


def test_diagnostics_and_source_do_not_contain_test_secret(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    secret_file, metadata_file, secret = _write_files(tmp_path, monkeypatch)
    assert secret not in credentials.read_metadata_file(metadata_file).__repr__()
    print("ROUTE_LIFECYCLE_SECRET_PRINTED=NONE")
    assert secret not in capsys.readouterr().out
    assert "Authorization" not in secret_file.read_text(encoding="utf-8")
