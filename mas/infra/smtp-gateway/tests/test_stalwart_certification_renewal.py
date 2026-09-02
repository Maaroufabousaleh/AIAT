from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

GATEWAY = Path(__file__).resolve().parents[1]
SCRIPT = GATEWAY / "scripts" / "renew-stalwart-certification-credential.py"
SPEC = importlib.util.spec_from_file_location("certification_renewal", SCRIPT)
assert SPEC and SPEC.loader
renewal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renewal)


def _record(
    credential_id: str = "key-1",
    *,
    description: str = renewal.KEY_DESCRIPTION,
    expires_at: str = "2026-08-03T00:00:00Z",
    owner: str | None = None,
) -> dict:
    value = {
        "id": credential_id,
        "description": description,
        "expiresAt": expires_at,
        "permissions": {
            "@type": "Replace",
            "permissions": {name: True for name in renewal.KEY_PERMISSIONS},
        },
    }
    if owner is not None:
        value["accountId"] = owner
    return value


class InventoryTransport:
    def __init__(self, records: list[dict], *, total: int | None = None):
        self.records = records
        self.total = len(records) if total is None else total
        self.calls: list[dict] = []

    def json(self, _url, _authorization, *, payload, **_kwargs):
        self.calls.append(payload)
        method, _arguments, tag = payload["methodCalls"][0]
        if method == "x:ApiKey/query":
            return {
                "sessionState": "state-1",
                "methodResponses": [
                    [
                        method,
                        {
                            "queryState": "query-state-1",
                            "canCalculateChanges": False,
                            "position": 0,
                            "ids": [item["id"] for item in self.records],
                            "total": self.total,
                        },
                        tag,
                    ]
                ]
            }
        if method == "x:ApiKey/get":
            return {
                "sessionState": "state-1",
                "methodResponses": [
                    [method, {"list": self.records, "notFound": []}, tag]
                ]
            }
        raise AssertionError(method)


def _query(records: list[dict], *, owner: str = "admin-id") -> list[dict]:
    return renewal.query_matching_records(
        transport=InventoryTransport(records),
        jmap_url=f"{renewal.LOCAL_URL}/jmap/",
        admin_authorization="Bearer test-management-token",
        owner_account_id=owner,
        now=datetime(2026, 8, 2, tzinfo=UTC),
    )


def test_expired_exact_matching_key_is_diagnosed() -> None:
    records = _query([_record(expires_at="2026-08-01T00:00:00Z")])
    assert records[0]["state"] == "EXPIRED"
    assert renewal.determine_diagnosis(
        records, old_jmap_status=401, old_account_status=401
    ) == ("EXPIRED", "EXPIRED")


def test_missing_or_revoked_key_is_diagnosed() -> None:
    assert renewal.determine_diagnosis(
        [], old_jmap_status=401, old_account_status=401
    ) == ("REVOKED_OR_MISSING", "MISSING")


def test_one_valid_matching_key_is_idempotent_state() -> None:
    records = _query([_record()])
    assert renewal.determine_diagnosis(
        records, old_jmap_status=200, old_account_status=200
    ) == ("OTHER_PROVEN_REASON", "VALID")


def test_duplicate_matching_keys_are_ambiguous() -> None:
    records = _query([_record("key-1"), _record("key-2")])
    assert renewal.determine_diagnosis(
        records, old_jmap_status=401, old_account_status=401
    ) == ("DUPLICATE_OR_AMBIGUOUS", "AMBIGUOUS")


def test_wrong_owner_is_refused() -> None:
    with pytest.raises(renewal.RenewalRefused, match="owner"):
        _query([_record(owner="different-owner")])


def test_wrong_description_is_not_a_match() -> None:
    assert _query([_record(description="unrelated key")]) == []


def test_malformed_expiry_is_refused() -> None:
    with pytest.raises(renewal.RenewalRefused, match="expiresAt"):
        _query([_record(expires_at="tomorrow")])


def test_insufficient_admin_create_permission_is_refused(monkeypatch) -> None:
    diagnostic = SimpleNamespace(sensitive_values=[], attempts=[])
    transport = SimpleNamespace(json=lambda *_args, **_kwargs: {})
    monkeypatch.setattr(renewal.PROVISIONING, "DiagnosticState", lambda **_kwargs: diagnostic)
    monkeypatch.setattr(renewal.PROVISIONING, "HttpTransport", lambda _state: transport)
    monkeypatch.setattr(renewal.PROVISIONING, "inspect_running_image", lambda *_args: "image")
    monkeypatch.setattr(renewal.PROVISIONING, "require_patched_server", lambda *_args: None)
    monkeypatch.setattr(
        renewal.PROVISIONING, "authenticate_administrator", lambda **_kwargs: "oauth-token"
    )
    monkeypatch.setattr(
        renewal.PROVISIONING,
        "discover_jmap_api_url",
        lambda **_kwargs: f"{renewal.LOCAL_URL}/jmap/",
    )
    monkeypatch.setattr(
        renewal.PROVISIONING, "require_permanent_directory_principal", lambda *_args: None
    )
    monkeypatch.setattr(
        renewal.PROVISIONING,
        "prove_persisted_create_permission",
        lambda **_kwargs: (_ for _ in ()).throw(renewal.PROVISIONING.Refused("denied")),
    )
    with pytest.raises(renewal.RenewalRefused, match="authorization is blocked"):
        renewal.establish_admin_session("protected-password")


class DestroyTransport:
    def __init__(self, credential_id: str):
        self.credential_id = credential_id
        self.methods: list[str] = []

    def json(self, _url, _authorization, *, payload, **_kwargs):
        method, arguments, tag = payload["methodCalls"][0]
        self.methods.append(method)
        if method == "x:ApiKey/set":
            assert arguments == {"destroy": [self.credential_id]}
            return {
                "sessionState": "state-1",
                "methodResponses": [[method, {"destroyed": [self.credential_id]}, tag]],
            }
        assert method == "x:ApiKey/get"
        assert arguments["ids"] == [self.credential_id]
        return {
            "sessionState": "state-2",
            "methodResponses": [
                [method, {"list": [], "notFound": [self.credential_id]}, tag]
            ]
        }


def test_exact_stale_key_revocation_and_not_found_proof() -> None:
    transport = DestroyTransport("stale-key")
    renewal.destroy_and_prove(
        transport=transport,
        jmap_url=f"{renewal.LOCAL_URL}/jmap/",
        authorization="Bearer management-token",
        credential_id="stale-key",
    )
    assert transport.methods == ["x:ApiKey/set", "x:ApiKey/get"]


def test_creation_requests_exactly_one_least_privilege_replacement() -> None:
    calls: list[dict] = []
    created_id = renewal.PROVISIONING.API_KEY_CREATE_ID
    record = _record("new-key", expires_at="2026-08-09T00:00:00Z", owner="admin-id")

    class CreateTransport:
        def json(self, _url, _authorization, *, payload, **_kwargs):
            calls.append(payload)
            method, _arguments, tag = payload["methodCalls"][0]
            if method == "x:ApiKey/set":
                return {
                    "sessionState": "state-1",
                    "methodResponses": [
                        [
                            method,
                            {
                                "created": {
                                    created_id: {
                                        "id": "new-key",
                                        "secret": "API_replacement-secret-value",
                                    }
                                }
                            },
                            tag,
                        ]
                    ],
                }
            return {
                "sessionState": "state-2",
                "methodResponses": [
                    [method, {"list": [record], "notFound": []}, tag]
                ],
            }

    identifier, secret, safe_record = renewal.create_replacement(
        transport=CreateTransport(),
        jmap_url=f"{renewal.LOCAL_URL}/jmap/",
        authorization="Bearer management-token",
        owner_account_id="admin-id",
        expires_at="2026-08-09T00:00:00Z",
    )
    assert identifier == "new-key"
    assert secret.startswith("API_")
    assert safe_record["permissions"] == list(renewal.KEY_PERMISSIONS)
    create = calls[0]["methodCalls"][0][1]["create"]
    assert list(create) == [created_id]
    assert create[created_id]["description"] == renewal.KEY_DESCRIPTION
    assert set(create[created_id]["permissions"]["permissions"]) == set(
        renewal.KEY_PERMISSIONS
    )


def test_old_bearer_rejection_requires_both_safe_statuses() -> None:
    assert renewal.determine_diagnosis(
        [{"state": "ACTIVE"}], old_jmap_status=401, old_account_status=200
    ) == ("OTHER_PROVEN_REASON", "AMBIGUOUS")


def test_control_file_requires_exact_safe_approval_set(monkeypatch) -> None:
    valid = "\n".join(
        f"{key}={'true' if value else 'false'}"
        for key, value in renewal.EXPECTED_CONTROLS.items()
    ) + "\n"
    monkeypatch.setattr(renewal, "_read_protected_bytes", lambda _path: valid.encode())
    assert renewal.parse_control_file(Path("/protected/control")) == renewal.EXPECTED_CONTROLS
    for changed in (
        valid.replace("APPROVE_ROUTE_MUTATION=false", "APPROVE_ROUTE_MUTATION=true"),
        valid + "EXTRA=true\n",
        valid.replace("APPROVE_EMAIL_SUBMISSION=false\n", ""),
    ):
        monkeypatch.setattr(renewal, "_read_protected_bytes", lambda _path, v=changed: v.encode())
        with pytest.raises(renewal.RenewalRefused):
            renewal.parse_control_file(Path("/protected/control"))


def test_credential_file_requires_exact_two_line_order(monkeypatch) -> None:
    valid = b"STALWART_API_KEY=API_example\nSTALWART_JMAP_SERVICE_TOKEN=Basic example\n"
    monkeypatch.setattr(renewal, "_read_protected_bytes", lambda _path: valid)
    values, raw = renewal.read_credential_file(Path("/protected/credential"))
    assert raw == valid
    assert list(values) == ["STALWART_API_KEY", "STALWART_JMAP_SERVICE_TOKEN"]
    for malformed in (
        valid.rstrip(b"\n"),
        b"STALWART_JMAP_SERVICE_TOKEN=Basic example\nSTALWART_API_KEY=API_example\n",
        valid + b"EXTRA=value\n",
    ):
        monkeypatch.setattr(renewal, "_read_protected_bytes", lambda _path, v=malformed: v)
        with pytest.raises(renewal.RenewalRefused):
            renewal.read_credential_file(Path("/protected/credential"))


def test_symlink_and_broken_symlink_are_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("safe\n", encoding="utf-8")
    symlink = tmp_path / "valid-link"
    symlink.symlink_to(target)
    broken = tmp_path / "broken-link"
    broken.symlink_to(tmp_path / "missing")
    for path in (symlink, broken):
        with pytest.raises(renewal.RenewalRefused, match="non-symlink"):
            renewal._require_root_regular(path)


def test_incorrect_owner_or_mode_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "credential"
    path.write_text("value\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(renewal.RenewalRefused, match="root:root mode 0600"):
        renewal._require_root_regular(path)


def test_atomic_replace_uses_candidate_without_backup(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "credential"
    candidate = tmp_path / ".credential.candidate"
    destination.write_bytes(b"old\n")
    candidate.write_bytes(b"new\n")
    monkeypatch.setattr(renewal, "_require_root_regular", lambda path: path.stat())
    renewal.atomic_replace(candidate, destination)
    assert destination.read_bytes() == b"new\n"
    assert not candidate.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["credential"]


def test_candidate_has_exact_two_lines_and_reconstructed_service_credential() -> None:
    value = renewal._candidate_bytes("API_replacement", "app_gateway-password")
    lines = value.decode().splitlines()
    assert len(lines) == 2
    assert lines[0] == "STALWART_API_KEY=API_replacement"
    assert lines[1].startswith("STALWART_JMAP_SERVICE_TOKEN=Basic ")
    assert value.endswith(b"\n")


def test_operator_output_never_includes_secret_values(monkeypatch, capsys) -> None:
    secret = "API_SUPER_PRIVATE_VALUE"
    monkeypatch.setattr(renewal, "renew", lambda **_kwargs: (_ for _ in ()).throw(
        renewal.RenewalRefused(f"authorization token={secret}")
    ))
    assert renewal.main(["--control-file", "/protected/control", "--expires-in-hours", "168"]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "token=<redacted>" in captured.out


def test_source_contains_no_route_or_email_submission_mutation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "x:MtaRoute/set" not in source
    assert "x:MtaOutboundStrategy/set" not in source
    assert '"Email/set"' not in source
    assert '"EmailSubmission/set"' not in source
    assert "sysApiKeyCreate" not in source


def test_cli_defaults_and_expiry_bounds(monkeypatch) -> None:
    args = renewal.build_parser().parse_args(
        ["--control-file", "/protected/control", "--expires-in-hours", "168"]
    )
    assert args.admin_source_file == Path("/etc/aiat/stalwart-admin-source.env")
    assert args.credential_file == Path("/etc/aiat/resend-certification.env")
    monkeypatch.setattr(renewal.os, "geteuid", lambda: 0)
    for value in (0, 169):
        with pytest.raises(renewal.RenewalRefused, match="between 1 and 168"):
            renewal.renew(
                control_file=Path("/not-read"),
                admin_source_file=Path("/not-read"),
                credential_file=Path("/not-read"),
                expires_in_hours=value,
            )


def test_shared_lock_contention_is_refused(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "shared.lock"
    monkeypatch.setattr(renewal, "SHARED_LOCK_FILE", lock)
    first = renewal._acquire_shared_lock()
    try:
        with pytest.raises(renewal.RenewalRefused, match="already running"):
            renewal._acquire_shared_lock()
    finally:
        first.close()


def _mock_renewal_transaction(
    tmp_path: Path,
    monkeypatch,
    *,
    records: list[dict],
    probe_statuses: list[int],
) -> dict[str, object]:
    calls: dict[str, object] = {
        "destroyed": [],
        "created": 0,
        "replaced": 0,
        "evidence": [],
    }
    lock = (tmp_path / "transaction.lock").open("w+")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    credential = tmp_path / "resend-certification.env"
    credential.write_bytes(b"original-protected-bytes\n")
    candidate = tmp_path / ".resend-certification.env.candidate"
    diagnostic = SimpleNamespace(sensitive_values=[])
    session = {
        "transport": object(),
        "diagnostic": diagnostic,
        "authorization": "Bearer management-token",
        "jmap_url": f"{renewal.LOCAL_URL}/jmap/",
        "owner_account_id": "admin-id",
        "image": "approved-image",
    }
    statuses = iter(probe_statuses)

    monkeypatch.setattr(renewal.os, "geteuid", lambda: 0)
    monkeypatch.setattr(renewal, "parse_control_file", lambda _path: dict(renewal.EXPECTED_CONTROLS))
    monkeypatch.setattr(renewal, "_acquire_shared_lock", lambda: lock)
    monkeypatch.setattr(renewal, "_create_evidence_dir", lambda: evidence)
    monkeypatch.setattr(renewal, "_container_snapshot", lambda: {"id": "container-1", "health": "healthy"})
    monkeypatch.setattr(
        renewal,
        "read_credential_file",
        lambda _path: (
            {
                "STALWART_API_KEY": "API_old-rejected-value",
                "STALWART_JMAP_SERVICE_TOKEN": "Basic old-service-value",
            },
            credential.read_bytes(),
        ),
    )
    monkeypatch.setattr(
        renewal,
        "_file_evidence",
        lambda path, value=None: {
            "name": path.name,
            "owner": "root:root",
            "mode": "0600",
            "size": len(value if value is not None else path.read_bytes()),
            "sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(
        renewal, "read_admin_credentials", lambda _path: ("admin-password", "app-password")
    )
    monkeypatch.setattr(renewal, "establish_admin_session", lambda _password: session)
    monkeypatch.setattr(renewal, "query_matching_records", lambda **_kwargs: records)
    monkeypatch.setattr(renewal, "probe_bearer_status", lambda *_args: next(statuses))
    monkeypatch.setattr(
        renewal,
        "validate_service_before_mutation",
        lambda **_kwargs: (0, [{"endpoint_path": "/jmap/", "http_status": "200"}]),
    )

    def destroy(**kwargs):
        calls["destroyed"].append(kwargs["credential_id"])

    def create(**_kwargs):
        calls["created"] += 1
        return (
            "new-key",
            "API_new-protected-value",
            {
                "id": "new-key",
                "description": renewal.KEY_DESCRIPTION,
                "expiresAt": "2026-08-09T00:00:00Z",
                "owner": renewal.ADMIN_ADDRESS,
                "permissions": list(renewal.KEY_PERMISSIONS),
                "state": "ACTIVE",
            },
        )

    def write(_destination, value):
        candidate.write_bytes(value)
        return candidate

    def replace(path, destination):
        calls["replaced"] += 1
        os.replace(path, destination)

    monkeypatch.setattr(renewal, "destroy_and_prove", destroy)
    monkeypatch.setattr(renewal, "create_replacement", create)
    monkeypatch.setattr(renewal, "write_candidate", write)
    monkeypatch.setattr(
        renewal,
        "_validate_candidate",
        lambda _path: (0, [{"endpoint_path": "/jmap/session", "http_status": "200"}]),
    )
    monkeypatch.setattr(renewal, "atomic_replace", replace)
    monkeypatch.setattr(
        renewal,
        "_write_evidence",
        lambda _directory, name, value: calls["evidence"].append((name, value)),
    )
    calls["credential"] = credential
    calls["candidate"] = candidate
    return calls


def test_renewal_revokes_exact_stale_key_creates_once_then_replaces(
    tmp_path: Path, monkeypatch
) -> None:
    stale = {
        "id": "stale-key",
        "description": renewal.KEY_DESCRIPTION,
        "expiresAt": "2026-08-01T00:00:00Z",
        "owner": renewal.ADMIN_ADDRESS,
        "permissions": list(renewal.KEY_PERMISSIONS),
        "state": "EXPIRED",
    }
    calls = _mock_renewal_transaction(
        tmp_path, monkeypatch, records=[stale], probe_statuses=[401, 401, 401, 401]
    )
    result = renewal.renew(
        control_file=Path("/protected/control"),
        admin_source_file=Path("/protected/admin-source"),
        credential_file=calls["credential"],
        expires_in_hours=168,
    )
    assert calls["destroyed"] == ["stale-key"]
    assert calls["created"] == 1
    assert calls["replaced"] == 1
    assert result["old_stale_key_revocation"] == "PASS"
    assert result["new_certification_key_creation"] == "PASS"
    assert result["protected_file_replacement"] == "PASS"
    assert result["email_submission_count"] == 0


def test_failure_after_creation_revokes_new_key_and_preserves_original(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _mock_renewal_transaction(
        tmp_path, monkeypatch, records=[], probe_statuses=[401, 401, 401, 401]
    )
    original = calls["credential"].read_bytes()
    monkeypatch.setattr(
        renewal,
        "_validate_candidate",
        lambda _path: (_ for _ in ()).throw(
            renewal.RenewalRefused("candidate validation blocked")
        ),
    )
    with pytest.raises(renewal.RenewalRefused, match="candidate validation blocked"):
        renewal.renew(
            control_file=Path("/protected/control"),
            admin_source_file=Path("/protected/admin-source"),
            credential_file=calls["credential"],
            expires_in_hours=168,
        )
    assert calls["destroyed"] == ["new-key"]
    assert calls["created"] == 1
    assert calls["replaced"] == 0
    assert calls["credential"].read_bytes() == original
    assert not calls["candidate"].exists()


def test_valid_rerun_is_idempotent_and_performs_no_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    active = {
        "id": "valid-key",
        "description": renewal.KEY_DESCRIPTION,
        "expiresAt": "2026-08-09T00:00:00Z",
        "owner": renewal.ADMIN_ADDRESS,
        "permissions": list(renewal.KEY_PERMISSIONS),
        "state": "ACTIVE",
    }
    calls = _mock_renewal_transaction(
        tmp_path, monkeypatch, records=[active], probe_statuses=[200, 200]
    )
    result = renewal.renew(
        control_file=Path("/protected/control"),
        admin_source_file=Path("/protected/admin-source"),
        credential_file=calls["credential"],
        expires_in_hours=168,
    )
    assert calls["destroyed"] == []
    assert calls["created"] == 0
    assert calls["replaced"] == 0
    assert result["old_key_state"] == "VALID"
    assert result["protected_file_replacement"] == "NOT_REQUIRED"


def test_duplicate_state_blocks_before_provisioning_or_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    ambiguous = [
        {"id": "one", "state": "ACTIVE"},
        {"id": "two", "state": "EXPIRED"},
    ]
    calls = _mock_renewal_transaction(
        tmp_path, monkeypatch, records=ambiguous, probe_statuses=[401, 401]
    )
    with pytest.raises(renewal.RenewalRefused, match="ambiguous"):
        renewal.renew(
            control_file=Path("/protected/control"),
            admin_source_file=Path("/protected/admin-source"),
            credential_file=calls["credential"],
            expires_in_hours=168,
        )
    assert calls["destroyed"] == []
    assert calls["created"] == 0
    assert calls["replaced"] == 0
