#!/usr/bin/env python3
"""Governed renewal of the local AIAT Stalwart certification credential."""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, request

WORKSPACE = Path("/mnt/c/projects/AIAT")
GATEWAY = WORKSPACE / "mas/infra/smtp-gateway"
SCRIPTS = GATEWAY / "scripts"
LOCAL_URL = "http://127.0.0.1:18080"
ADMIN_ADDRESS = "admin@agents.aiat.local"
GATEWAY_ADDRESS = "gateway-test@agents.aiat.ca"
ACCOUNT_ID = "w"
DEFAULT_CONTROL_FILE = Path("/etc/aiat/certification-credential-renewal.env")
DEFAULT_ADMIN_SOURCE_FILE = Path("/etc/aiat/stalwart-admin-source.env")
DEFAULT_CREDENTIAL_FILE = Path("/etc/aiat/resend-certification.env")
SHARED_LOCK_FILE = Path("/run/lock/aiat-resend-route-finish.lock")
EVIDENCE_PARENT = Path("/secure/rollback")
CONTROL_KEYS = (
    "AIAT_CERTIFICATION_CREDENTIAL_RENEWAL_APPROVED",
    "APPROVE_STALE_CERTIFICATION_KEY_REVOCATION",
    "APPROVE_CERTIFICATION_KEY_CREATION",
    "APPROVE_PROTECTED_FILE_REPLACEMENT",
    "APPROVE_EMAIL_SUBMISSION",
    "APPROVE_ROUTE_MUTATION",
)
EXPECTED_CONTROLS = {
    "AIAT_CERTIFICATION_CREDENTIAL_RENEWAL_APPROVED": True,
    "APPROVE_STALE_CERTIFICATION_KEY_REVOCATION": True,
    "APPROVE_CERTIFICATION_KEY_CREATION": True,
    "APPROVE_PROTECTED_FILE_REPLACEMENT": True,
    "APPROVE_EMAIL_SUBMISSION": False,
    "APPROVE_ROUTE_MUTATION": False,
}
CONTROL_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(true|false)$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_module(name: str, filename: str) -> Any:
    specification = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"required helper {filename} is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


PROVISIONING = _load_module(
    "stalwart_certification_provisioning_for_renewal",
    "provision-stalwart-certification-api-key.py",
)
VALIDATOR = _load_module(
    "stalwart_certification_validator_for_renewal",
    "validate-stalwart-certification-credentials.py",
)
ADMIN_SOURCE = _load_module(
    "stalwart_admin_source_for_certification_renewal",
    "stalwart_admin_source.py",
)
JMAP_RESPONSE = _load_module(
    "stalwart_jmap_response_for_certification_renewal",
    "stalwart_jmap_response.py",
)
KEY_DESCRIPTION = PROVISIONING.KEY_DESCRIPTION
KEY_PERMISSIONS = tuple(PROVISIONING.REQUIRED_KEY_PERMISSIONS)


class RenewalRefused(RuntimeError):
    """A fail-closed refusal whose message is safe for operator output."""


def _safe_reason(value: Any) -> str:
    return PROVISIONING.sanitize_description(value, limit=200)


def _require_root_regular(path: Path, *, mode: int = 0o600) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise RenewalRefused(f"protected file {path.name} is unavailable") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise RenewalRefused(f"protected file {path.name} must be a regular non-symlink")
    if details.st_uid != 0 or details.st_gid != 0 or stat.S_IMODE(details.st_mode) != mode:
        raise RenewalRefused(f"protected file {path.name} must be root:root mode {mode:04o}")
    return details


def _read_protected_bytes(path: Path) -> bytes:
    before = _require_root_regular(path)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_gid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RenewalRefused("protected file changed during validation")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    except OSError as exc:
        raise RenewalRefused(f"protected file {path.name} could not be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def parse_control_file(path: Path) -> dict[str, bool]:
    try:
        value = _read_protected_bytes(path).decode("utf-8")
    except UnicodeError as exc:
        raise RenewalRefused("renewal control file is not valid UTF-8") from exc
    if "\x00" in value or "\r" in value or not value.endswith("\n"):
        raise RenewalRefused("renewal control file is malformed")
    lines = value.splitlines()
    if len(lines) != len(CONTROL_KEYS):
        raise RenewalRefused("renewal control file does not contain the exact approval set")
    controls: dict[str, bool] = {}
    for line in lines:
        match = CONTROL_LINE.fullmatch(line)
        if match is None:
            raise RenewalRefused("renewal control file contains a malformed line")
        key, raw = match.groups()
        if key not in EXPECTED_CONTROLS or key in controls:
            raise RenewalRefused("renewal control file contains an unknown or duplicate key")
        controls[key] = raw == "true"
    if controls != EXPECTED_CONTROLS:
        raise RenewalRefused("renewal control approvals are not the exact safe values")
    return controls


def read_credential_file(path: Path) -> tuple[dict[str, str], bytes]:
    raw = _read_protected_bytes(path)
    if b"\x00" in raw or b"\r" in raw:
        raise RenewalRefused("certification credential file contains malformed data")
    try:
        value = raw.decode("utf-8")
    except UnicodeError as exc:
        raise RenewalRefused("certification credential file is not valid UTF-8") from exc
    lines = value.splitlines()
    expected = ("STALWART_API_KEY", "STALWART_JMAP_SERVICE_TOKEN")
    if not value.endswith("\n") or len(lines) != len(expected):
        raise RenewalRefused("certification credential file must contain exactly two lines")
    credentials: dict[str, str] = {}
    for key, line in zip(expected, lines, strict=True):
        prefix = f"{key}="
        if not line.startswith(prefix) or not line[len(prefix) :]:
            raise RenewalRefused("certification credential file has an unexpected variable")
        credentials[key] = line[len(prefix) :]
    if any("\x00" in item or "\n" in item or "\r" in item for item in credentials.values()):
        raise RenewalRefused("certification credential file contains malformed data")
    return credentials, raw


def read_admin_credentials(path: Path) -> tuple[str, str]:
    try:
        values = ADMIN_SOURCE.read_protected_admin_source(path)
    except ADMIN_SOURCE.AdminSourceRefused as exc:
        raise RenewalRefused("protected admin source is invalid") from exc
    admin_password = values["admin-st"]
    app_password = values["guest"]
    if (
        not admin_password
        or any(character.isspace() for character in admin_password)
        or not app_password.startswith("app_")
        or any(character.isspace() for character in app_password)
    ):
        raise RenewalRefused("gateway service application password is invalid")
    return admin_password, app_password


def _strict_jmap(
    *,
    transport: Any,
    jmap_url: str,
    authorization: str,
    payload: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    method = payload["methodCalls"][0][0]
    try:
        response = transport.json(
            jmap_url,
            authorization,
            payload=payload,
            endpoint_path=PROVISIONING.endpoint_path(jmap_url),
            jmap_method=method,
            authentication_mechanism="oauth2-bearer-management-jmap",
        )
        JMAP_RESPONSE.validate_jmap_response(
            payload,
            response,
            action=action,
            http_status="200",
            endpoint_path=PROVISIONING.endpoint_path(jmap_url),
        )
        return response
    except (PROVISIONING.Refused, JMAP_RESPONSE.JmapResponseError) as exc:
        raise RenewalRefused(f"Stalwart refused {action}") from exc


def _method_result(response: dict[str, Any], method: str) -> dict[str, Any]:
    try:
        return PROVISIONING.method_result(response, method)
    except PROVISIONING.Refused as exc:
        raise RenewalRefused("Stalwart returned an unexpected JMAP result") from exc


def _query_payload() -> dict[str, Any]:
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [["x:ApiKey/query", {"limit": 100}, "certification-key-query"]],
    }


def _get_payload(ids: list[str], call_id: str = "certification-key-details") -> dict[str, Any]:
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [
            [
                "x:ApiKey/get",
                {
                    "ids": ids,
                    "properties": ["description", "expiresAt", "permissions"],
                },
                call_id,
            ]
        ],
    }


def _destroy_payload(credential_id: str) -> dict[str, Any]:
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [
            [
                "x:ApiKey/set",
                {"destroy": [credential_id]},
                "destroy-stale-certification-key",
            ]
        ],
    }


def _record_permissions(record: dict[str, Any]) -> tuple[str, ...]:
    permissions = record.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("@type") != "Replace":
        raise RenewalRefused("certification key permissions are malformed")
    values = permissions.get("permissions")
    if not isinstance(values, dict) or set(values) != set(KEY_PERMISSIONS):
        raise RenewalRefused("certification key permissions are not exact least privilege")
    if any(value is not True for value in values.values()):
        raise RenewalRefused("certification key permissions contain disabled values")
    return tuple(permission for permission in KEY_PERMISSIONS if values[permission])


def _parse_expiry(value: Any) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC.fullmatch(value) is None:
        raise RenewalRefused("certification key expiresAt is malformed")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise RenewalRefused("certification key expiresAt is malformed") from exc
    return parsed


def query_matching_records(
    *,
    transport: Any,
    jmap_url: str,
    admin_authorization: str,
    owner_account_id: str,
    now: datetime,
) -> list[dict[str, Any]]:
    query = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=admin_authorization,
        payload=_query_payload(),
        action="certification-key-query",
    )
    result = _method_result(query, "x:ApiKey/query")
    ids = result.get("ids")
    if not isinstance(ids, list) or any(
        not isinstance(item, str) or SAFE_ID.fullmatch(item) is None for item in ids
    ):
        raise RenewalRefused("API-key query returned malformed identifiers")
    total = result.get("total", len(ids))
    if (
        len(ids) >= 100
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total != len(ids)
    ):
        raise RenewalRefused("API-key inventory is incomplete or ambiguous")
    if not ids:
        return []
    details = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=admin_authorization,
        payload=_get_payload(ids),
        action="certification-key-get",
    )
    get_result = _method_result(details, "x:ApiKey/get")
    records = get_result.get("list")
    not_found = get_result.get("notFound", [])
    if (
        not isinstance(records, list)
        or not isinstance(not_found, list)
        or not_found
        or len(records) != len(ids)
    ):
        raise RenewalRefused("API-key inventory details are incomplete or ambiguous")
    matching: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise RenewalRefused("API-key inventory contains a malformed record")
        if record.get("description") != KEY_DESCRIPTION:
            continue
        credential_id = record.get("id")
        if not isinstance(credential_id, str) or SAFE_ID.fullmatch(credential_id) is None:
            raise RenewalRefused("certification key identifier is malformed")
        record_owner = record.get("accountId")
        if record_owner is not None and record_owner != owner_account_id:
            raise RenewalRefused("certification key owner does not match the administrator")
        permissions = _record_permissions(record)
        expires_at = record.get("expiresAt")
        expires = _parse_expiry(expires_at)
        matching.append(
            {
                "id": credential_id,
                "description": KEY_DESCRIPTION,
                "expiresAt": expires_at,
                "owner": ADMIN_ADDRESS,
                "permissions": list(permissions),
                "state": "EXPIRED" if expires <= now else "ACTIVE",
            }
        )
    return matching


def determine_diagnosis(
    records: list[dict[str, Any]],
    *,
    old_jmap_status: int,
    old_account_status: int,
) -> tuple[str, str]:
    old_rejected = old_jmap_status in {401, 403} and old_account_status in {401, 403}
    if len(records) > 1:
        return "DUPLICATE_OR_AMBIGUOUS", "AMBIGUOUS"
    if not records:
        if old_rejected:
            return "REVOKED_OR_MISSING", "MISSING"
        return "OTHER_PROVEN_REASON", "UNMATCHED_ACCEPTED_BEARER"
    record = records[0]
    if record["state"] == "EXPIRED":
        return "EXPIRED", "EXPIRED"
    if old_rejected:
        return "SERVER_RECORD_PRESENT_BUT_BEARER_REJECTED", "STALE"
    if old_jmap_status == 200 and old_account_status == 200:
        return "OTHER_PROVEN_REASON", "VALID"
    return "OTHER_PROVEN_REASON", "AMBIGUOUS"


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise RenewalRefused("local credential probe redirects are not permitted")


def probe_bearer_status(api_key: str, endpoint_path: str) -> int:
    if endpoint_path not in {"/jmap/session", "/api/account"}:
        raise RenewalRefused("credential probe endpoint is not allowlisted")
    message = request.Request(
        f"{LOCAL_URL}{endpoint_path}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(message, timeout=15) as response:
            response.read(1)
            return int(response.status)
    except error.HTTPError as exc:
        return int(exc.code)
    except RenewalRefused:
        raise
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RenewalRefused("local credential probe could not reach Stalwart") from exc


def _administrator_account_id(
    *, transport: Any, jmap_url: str, authorization: str
) -> str:
    domain = ADMIN_ADDRESS.rsplit("@", 1)[1]
    domain_response = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:Domain/query",
                    {"filter": {"name": domain}, "limit": 2},
                    "renewal-admin-domain",
                ]
            ],
        },
        action="administrator-domain-query",
    )
    domain_ids = _method_result(domain_response, "x:Domain/query").get("ids") or []
    if len(domain_ids) != 1 or not isinstance(domain_ids[0], str):
        raise RenewalRefused("administrator domain identity is ambiguous")
    account_response = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=authorization,
        payload={
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [
                [
                    "x:Account/query",
                    {
                        "filter": {"name": "admin", "domainId": domain_ids[0]},
                        "limit": 2,
                    },
                    "renewal-admin-account",
                ]
            ],
        },
        action="administrator-account-query",
    )
    account_ids = _method_result(account_response, "x:Account/query").get("ids") or []
    if len(account_ids) != 1 or not isinstance(account_ids[0], str):
        raise RenewalRefused("administrator account identity is ambiguous")
    return account_ids[0]


def establish_admin_session(admin_password: str) -> dict[str, Any]:
    diagnostic = PROVISIONING.DiagnosticState(sensitive_values=[admin_password])
    transport = PROVISIONING.HttpTransport(diagnostic)
    try:
        image = PROVISIONING.inspect_running_image(PROVISIONING.STALWART_CONTAINER)
        PROVISIONING.require_patched_server(image, diagnostic)
        token = PROVISIONING.authenticate_administrator(
            transport=transport,
            base_url=LOCAL_URL,
            administrator_address=ADMIN_ADDRESS,
            administrator_password=admin_password,
            diagnostic=diagnostic,
        )
        authorization = f"Bearer {token}"
        diagnostic.sensitive_values.extend([token, authorization])
        transport.json(
            f"{LOCAL_URL}/api/account",
            authorization,
            endpoint_path="/api/account",
            jmap_method="GET /api/account",
            authentication=True,
            authentication_mechanism="oauth2-bearer",
        )
        jmap_url = PROVISIONING.discover_jmap_api_url(
            transport=transport,
            base_url=LOCAL_URL,
            authorization=authorization,
            diagnostic=diagnostic,
        )
        PROVISIONING.require_permanent_directory_principal(ADMIN_ADDRESS, diagnostic)
        PROVISIONING.prove_persisted_create_permission(
            transport=transport,
            jmap_url=jmap_url,
            authorization=authorization,
            administrator_address=ADMIN_ADDRESS,
            diagnostic=diagnostic,
        )
        owner_account_id = _administrator_account_id(
            transport=transport,
            jmap_url=jmap_url,
            authorization=authorization,
        )
        return {
            "transport": transport,
            "diagnostic": diagnostic,
            "authorization": authorization,
            "jmap_url": jmap_url,
            "owner_account_id": owner_account_id,
            "image": image,
        }
    except (PROVISIONING.Refused, RenewalRefused) as exc:
        diagnostic.sensitive_values.clear()
        raise RenewalRefused("permanent administrator authorization is blocked") from exc


def destroy_and_prove(
    *,
    transport: Any,
    jmap_url: str,
    authorization: str,
    credential_id: str,
) -> None:
    response = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=authorization,
        payload=_destroy_payload(credential_id),
        action="destroy-stale-certification-key",
    )
    destroyed = _method_result(response, "x:ApiKey/set").get("destroyed")
    if destroyed != [credential_id]:
        raise RenewalRefused("Stalwart did not prove the exact credential destruction")
    verification = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=authorization,
        payload=_get_payload([credential_id], "verify-certification-key-destroyed"),
        action="verify-certification-key-destroyed",
    )
    result = _method_result(verification, "x:ApiKey/get")
    if result.get("list") != [] or result.get("notFound") != [credential_id]:
        raise RenewalRefused("destroyed credential notFound proof failed")


def create_replacement(
    *,
    transport: Any,
    jmap_url: str,
    authorization: str,
    owner_account_id: str,
    expires_at: str,
) -> tuple[str, str, dict[str, Any]]:
    payload = PROVISIONING.api_key_payload(expires_at)
    response = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=authorization,
        payload=payload,
        action="create-certification-key",
    )
    try:
        credential_id, secret = PROVISIONING.extract_created_credential(response)
    except PROVISIONING.Refused as exc:
        raise RenewalRefused("Stalwart did not return one replacement credential") from exc
    details = _strict_jmap(
        transport=transport,
        jmap_url=jmap_url,
        authorization=authorization,
        payload=_get_payload([credential_id], "verify-created-certification-key"),
        action="verify-created-certification-key",
    )
    result = _method_result(details, "x:ApiKey/get")
    records = result.get("list")
    if not isinstance(records, list) or len(records) != 1 or result.get("notFound", []):
        raise RenewalRefused("replacement credential could not be read back exactly once")
    record = records[0]
    if (
        not isinstance(record, dict)
        or record.get("id") != credential_id
        or record.get("description") != KEY_DESCRIPTION
        or record.get("expiresAt") != expires_at
        or (record.get("accountId") is not None and record.get("accountId") != owner_account_id)
    ):
        raise RenewalRefused("replacement credential identity or ownership was not proven")
    permissions = _record_permissions(record)
    return credential_id, secret, {
        "id": credential_id,
        "description": KEY_DESCRIPTION,
        "expiresAt": expires_at,
        "owner": ADMIN_ADDRESS,
        "permissions": list(permissions),
        "state": "ACTIVE",
    }


def _candidate_bytes(api_key: str, app_password: str) -> bytes:
    basic = base64.b64encode(f"{GATEWAY_ADDRESS}:{app_password}".encode()).decode("ascii")
    return (
        f"STALWART_API_KEY={api_key}\n"
        f"STALWART_JMAP_SERVICE_TOKEN=Basic {basic}\n"
    ).encode()


def _service_token(app_password: str) -> str:
    encoded = base64.b64encode(f"{GATEWAY_ADDRESS}:{app_password}".encode()).decode(
        "ascii"
    )
    return f"Basic {encoded}"


def validate_service_before_mutation(
    *, app_password: str, jmap_url: str
) -> tuple[int, list[dict[str, str]]]:
    token = _service_token(app_password)
    transport = VALIDATOR.HttpTransport([app_password, token])
    try:
        count = VALIDATOR.validate_mail_access(
            token,
            ACCOUNT_ID,
            transport,
            base_url=LOCAL_URL,
            jmap_url=jmap_url,
        )
        if count != 0:
            raise RenewalRefused("EmailSubmission inventory is not zero")
        return count, list(transport.attempts)
    except VALIDATOR.Refused as exc:
        raise RenewalRefused("gateway service credential validation failed") from exc
    finally:
        transport.sensitive_values.clear()


def write_candidate(path: Path, value: bytes) -> Path:
    current = _require_root_regular(path)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise RenewalRefused("protected credential directory is unsafe")
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.renewal.", dir=path.parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        candidate = Path(temporary)
        _require_root_regular(candidate)
        if current.st_dev != candidate.lstat().st_dev:
            raise RenewalRefused("credential replacement is not on the same filesystem")
        return candidate
    except RenewalRefused:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
        raise
    except OSError as exc:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
        raise RenewalRefused("protected credential candidate could not be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def atomic_replace(candidate: Path, destination: Path) -> None:
    _require_root_regular(candidate)
    _require_root_regular(destination)
    directory_fd = -1
    try:
        directory_fd = os.open(
            destination.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(directory_fd)
        os.replace(candidate, destination)
        # The rename is the transaction's terminal commit point. A directory
        # fsync is attempted for durability, but no fallible work follows a
        # successful replacement because the old secret is never retained.
        with contextlib.suppress(OSError):
            os.fsync(directory_fd)
    except OSError as exc:
        raise RenewalRefused("atomic protected credential replacement failed") from exc
    finally:
        if directory_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(directory_fd)


def _file_evidence(path: Path, value: bytes | None = None) -> dict[str, Any]:
    details = _require_root_regular(path)
    content = _read_protected_bytes(path) if value is None else value
    return {
        "name": path.name,
        "owner": "root:root",
        "mode": f"{stat.S_IMODE(details.st_mode):04o}",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _create_evidence_dir() -> Path:
    try:
        parent = EVIDENCE_PARENT.lstat()
    except OSError as exc:
        raise RenewalRefused("renewal evidence parent is unavailable") from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != 0
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise RenewalRefused("renewal evidence parent must be root:root mode 0700")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = EVIDENCE_PARENT / f"certification-renewal-{stamp}-{os.getpid()}"
    try:
        directory.mkdir(mode=0o700)
    except OSError as exc:
        raise RenewalRefused("renewal evidence directory could not be created") from exc
    return directory


def _write_evidence(directory: Path, name: str, value: dict[str, Any]) -> None:
    if re.fullmatch(r"[a-z0-9-]+\.json", name) is None:
        raise RenewalRefused("renewal evidence filename is invalid")
    path = directory / name
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise RenewalRefused("sanitized renewal evidence could not be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _container_snapshot() -> dict[str, str]:
    template = (
        "{{.Id}}\t{{.Config.Image}}\t{{.State.Running}}\t{{.State.OOMKilled}}\t"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    )
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", template, PROVISIONING.STALWART_CONTAINER],
            cwd=WORKSPACE,
            env={
                "PATH": os.environ.get(
                    "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
                "LANG": "C",
                "LC_ALL": "C",
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RenewalRefused("Stalwart container could not be inspected") from exc
    fields = result.stdout.strip().split("\t") if result.returncode == 0 else []
    if len(fields) != 5:
        raise RenewalRefused("Stalwart container inspection failed")
    snapshot = {
        "id": fields[0],
        "image": fields[1],
        "running": fields[2],
        "oom_killed": fields[3],
        "health": fields[4],
    }
    if snapshot["running"] != "true" or snapshot["oom_killed"] != "false":
        raise RenewalRefused("Stalwart container is not healthy for renewal")
    if snapshot["health"] not in {"healthy", "none"}:
        raise RenewalRefused("Stalwart container health check is not passing")
    return snapshot


def _acquire_shared_lock() -> Any:
    handle = None
    try:
        existing = SHARED_LOCK_FILE.lstat()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise RenewalRefused("shared activation lock is unavailable") from exc
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        raise RenewalRefused("shared activation lock may not be a symbolic link")
    try:
        descriptor = os.open(SHARED_LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError as exc:
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
        raise RenewalRefused("renewal or route activation is already running") from exc


def _validate_candidate(path: Path) -> tuple[int, list[dict[str, str]]]:
    credentials, _raw = read_credential_file(path)
    transport = VALIDATOR.HttpTransport(list(credentials.values()))
    try:
        count = VALIDATOR.validate_live(credentials, ACCOUNT_ID, transport, base_url=LOCAL_URL)
        if count != 0:
            raise RenewalRefused("EmailSubmission inventory is not zero")
        return count, list(transport.attempts)
    except VALIDATOR.Refused as exc:
        raise RenewalRefused("replacement certification credential validation failed") from exc
    finally:
        credentials.clear()
        transport.sensitive_values.clear()


def _new_result() -> dict[str, Any]:
    return {
        "diagnosis": "OTHER_PROVEN_REASON",
        "old_matching_key_count": 0,
        "old_key_state": "UNKNOWN",
        "old_stale_key_revocation": "NOT_REQUIRED",
        "new_certification_key_creation": "NOT_RUN",
        "new_key_expiry": "NOT_CREATED",
        "new_key_permissions": list(KEY_PERMISSIONS),
        "new_key_jmap_session": "NOT_RUN",
        "new_key_account_introspection": "NOT_RUN",
        "service_credential_validation": "NOT_RUN",
        "email_submission_count": "UNKNOWN",
        "protected_file_replacement": "NOT_RUN",
        "route_inspect": "NOT_RUN",
        "evidence_directory": "NOT_CREATED",
    }


def renew(
    *,
    control_file: Path,
    admin_source_file: Path,
    credential_file: Path,
    expires_in_hours: int,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RenewalRefused("run certification credential renewal as root")
    if not 1 <= expires_in_hours <= 168:
        raise RenewalRefused("--expires-in-hours must be between 1 and 168")
    parse_control_file(control_file)
    lock = _acquire_shared_lock()
    evidence_dir: Path | None = None
    candidate: Path | None = None
    created_id: str | None = None
    replacement_complete = False
    session: dict[str, Any] | None = None
    credentials: dict[str, str] = {}
    admin_password = ""
    app_password = ""
    result = _new_result()
    try:
        evidence_dir = _create_evidence_dir()
        result["evidence_directory"] = str(evidence_dir)
        container_before = _container_snapshot()
        credentials, original = read_credential_file(credential_file)
        original_evidence = _file_evidence(credential_file, original)
        admin_password, app_password = read_admin_credentials(admin_source_file)
        session = establish_admin_session(admin_password)
        now = datetime.now(UTC)
        records = query_matching_records(
            transport=session["transport"],
            jmap_url=session["jmap_url"],
            admin_authorization=session["authorization"],
            owner_account_id=session["owner_account_id"],
            now=now,
        )
        old_jmap_status = probe_bearer_status(
            credentials["STALWART_API_KEY"], "/jmap/session"
        )
        old_account_status = probe_bearer_status(
            credentials["STALWART_API_KEY"], "/api/account"
        )
        diagnosis, old_state = determine_diagnosis(
            records,
            old_jmap_status=old_jmap_status,
            old_account_status=old_account_status,
        )
        result.update(
            {
                "diagnosis": diagnosis,
                "old_matching_key_count": len(records),
                "old_key_state": old_state,
            }
        )
        phase_one = {
            "version": 1,
            "diagnosis": diagnosis,
            "old_matching_key_count": len(records),
            "old_key_state": old_state,
            "matching_records": records,
            "old_bearer": {
                "jmap_session_http_status": old_jmap_status,
                "account_http_status": old_account_status,
            },
            "credential_file": original_evidence,
            "container": container_before,
            "route_mutation": "NOT_PERFORMED",
            "email_submission_mutation": "NOT_PERFORMED",
        }
        _write_evidence(evidence_dir, "diagnosis.json", phase_one)
        if old_state in {"AMBIGUOUS", "UNMATCHED_ACCEPTED_BEARER"}:
            raise RenewalRefused("certification credential state is ambiguous")
        initial_submission_count, service_attempts = validate_service_before_mutation(
            app_password=app_password,
            jmap_url=session["jmap_url"],
        )
        result["service_credential_validation"] = "PASS"
        result["email_submission_count"] = initial_submission_count
        _write_evidence(
            evidence_dir,
            "service-preflight.json",
            {
                "version": 1,
                "service_credential_validation": "PASS",
                "account_id": ACCOUNT_ID,
                "email_submission_count": initial_submission_count,
                "validation_attempts": service_attempts,
                "route_mutation": "NOT_PERFORMED",
                "email_submission_mutation": "NOT_PERFORMED",
            },
        )
        if old_state == "VALID":
            count, attempts = _validate_candidate(credential_file)
            result.update(
                {
                    "new_key_jmap_session": "PASS",
                    "new_key_account_introspection": "PASS",
                    "service_credential_validation": "PASS",
                    "email_submission_count": count,
                    "protected_file_replacement": "NOT_REQUIRED",
                }
            )
            _write_evidence(
                evidence_dir,
                "result.json",
                {
                    **phase_one,
                    "final_status": "ALREADY_VALID",
                    "validation_attempts": attempts,
                },
            )
            return result
        if records:
            destroy_and_prove(
                transport=session["transport"],
                jmap_url=session["jmap_url"],
                authorization=session["authorization"],
                credential_id=records[0]["id"],
            )
            result["old_stale_key_revocation"] = "PASS"
        if probe_bearer_status(credentials["STALWART_API_KEY"], "/jmap/session") not in {
            401,
            403,
        } or probe_bearer_status(credentials["STALWART_API_KEY"], "/api/account") not in {
            401,
            403,
        }:
            raise RenewalRefused("old certification bearer rejection was not proven")
        expires_at = (now + timedelta(hours=expires_in_hours)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        created_id, api_key, created_record = create_replacement(
            transport=session["transport"],
            jmap_url=session["jmap_url"],
            authorization=session["authorization"],
            owner_account_id=session["owner_account_id"],
            expires_at=expires_at,
        )
        session["diagnostic"].sensitive_values.append(api_key)
        result.update(
            {
                "new_certification_key_creation": "PASS",
                "new_key_expiry": expires_at,
                "new_key_permissions": list(created_record["permissions"]),
            }
        )
        candidate = write_candidate(credential_file, _candidate_bytes(api_key, app_password))
        count, attempts = _validate_candidate(candidate)
        result.update(
            {
                "new_key_jmap_session": "PASS",
                "new_key_account_introspection": "PASS",
                "service_credential_validation": "PASS",
                "email_submission_count": count,
            }
        )
        if _container_snapshot() != container_before:
            raise RenewalRefused("Stalwart container changed during credential renewal")
        _write_evidence(
            evidence_dir,
            "result.json",
            {
                **phase_one,
                "final_status": "COMMIT_READY",
                "stale_key_revocation": result["old_stale_key_revocation"],
                "created_record": created_record,
                "validation_attempts": attempts,
                "email_submission_count": count,
                "credential_file_candidate": _file_evidence(candidate),
                "container_after": container_before,
                "route_mutation": "NOT_PERFORMED",
                "email_submission_mutation": "NOT_PERFORMED",
                "sys_api_key_create_removed": "NO",
            },
        )
        atomic_replace(candidate, credential_file)
        candidate = None
        replacement_complete = True
        result["protected_file_replacement"] = "PASS"
        return result
    except Exception as exc:
        cleanup = "NOT_REQUIRED"
        if created_id is not None and session is not None and not replacement_complete:
            try:
                destroy_and_prove(
                    transport=session["transport"],
                    jmap_url=session["jmap_url"],
                    authorization=session["authorization"],
                    credential_id=created_id,
                )
                cleanup = "PASS"
            except RenewalRefused:
                cleanup = "BLOCKED"
        if candidate is not None:
            with contextlib.suppress(OSError):
                candidate.unlink()
        if evidence_dir is not None:
            with contextlib.suppress(RenewalRefused):
                _write_evidence(
                    evidence_dir,
                    "failure.json",
                    {
                        "version": 1,
                        "final_status": "BLOCKED",
                        "reason": _safe_reason(exc),
                        "diagnosis": result["diagnosis"],
                        "old_matching_key_count": result["old_matching_key_count"],
                        "old_key_state": result["old_key_state"],
                        "new_key_cleanup": cleanup,
                        "route_mutation": "NOT_PERFORMED",
                        "email_submission_mutation": "NOT_PERFORMED",
                        "sys_api_key_create_removed": "NO",
                    },
                )
        if isinstance(exc, RenewalRefused):
            raise
        raise RenewalRefused("unexpected certification renewal failure") from exc
    finally:
        credentials.clear()
        admin_password = ""
        app_password = ""
        if session is not None:
            session["diagnostic"].sensitive_values.clear()
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-file", type=Path, required=True)
    parser.add_argument(
        "--admin-source-file", type=Path, default=DEFAULT_ADMIN_SOURCE_FILE
    )
    parser.add_argument("--credential-file", type=Path, default=DEFAULT_CREDENTIAL_FILE)
    parser.add_argument("--expires-in-hours", type=int, required=True)
    return parser


def _print_result(result: dict[str, Any]) -> None:
    print("FINAL_STATUS=PASS")
    print(f"DIAGNOSIS={result['diagnosis']}")
    print(f"OLD_MATCHING_KEY_COUNT={result['old_matching_key_count']}")
    print(f"OLD_KEY_STATE={result['old_key_state']}")
    print(f"OLD_STALE_KEY_REVOCATION={result['old_stale_key_revocation']}")
    print(f"NEW_CERTIFICATION_KEY_CREATION={result['new_certification_key_creation']}")
    print(f"NEW_KEY_EXPIRY={result['new_key_expiry']}")
    print("NEW_KEY_PERMISSIONS=" + ",".join(result["new_key_permissions"]))
    print(f"NEW_KEY_JMAP_SESSION={result['new_key_jmap_session']}")
    print(f"NEW_KEY_ACCOUNT_INTROSPECTION={result['new_key_account_introspection']}")
    print(f"SERVICE_CREDENTIAL_VALIDATION={result['service_credential_validation']}")
    print(f"EMAIL_SUBMISSION_COUNT={result['email_submission_count']}")
    print(f"PROTECTED_FILE_REPLACEMENT={result['protected_file_replacement']}")
    print("PROTECTED_FILE_OWNER=root:root")
    print("PROTECTED_FILE_MODE=0600")
    print(f"EVIDENCE_DIRECTORY={result['evidence_directory']}")
    print("ROUTE_MUTATION=NOT_PERFORMED")
    print("EMAIL_SUBMISSION_MUTATION=NOT_PERFORMED")
    print("MESSAGE_SENT=NO")
    print("SYS_API_KEY_CREATE_REMOVED=NO")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = renew(
            control_file=args.control_file.absolute(),
            admin_source_file=args.admin_source_file.absolute(),
            credential_file=args.credential_file.absolute(),
            expires_in_hours=args.expires_in_hours,
        )
    except RenewalRefused as exc:
        print("FINAL_STATUS=BLOCKED")
        print(f"BLOCK_REASON={_safe_reason(exc)}")
        print("ROUTE_MUTATION=NOT_PERFORMED")
        print("EMAIL_SUBMISSION_MUTATION=NOT_PERFORMED")
        print("MESSAGE_SENT=NO")
        print("SYS_API_KEY_CREATE_REMOVED=NO")
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
