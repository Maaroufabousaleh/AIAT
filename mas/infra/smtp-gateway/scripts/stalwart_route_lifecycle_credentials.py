#!/usr/bin/env python3
"""Secure helpers for the temporary Stalwart route-lifecycle API key.

The route key is deliberately separate from the read-only certification key.
This module is also used by the provision, validate, and revoke entry points;
none of those entry points accept a secret on their command line.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any


def _load_module(name: str, filename: str) -> Any:
    specification = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"{filename} is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


PROVISIONING = _load_module(
    "stalwart_certification_provisioning",
    "provision-stalwart-certification-api-key.py",
)
JMAP_RESPONSE = _load_module(
    "stalwart_jmap_response",
    "stalwart_jmap_response.py",
)

LOCAL_URL = PROVISIONING.LOCAL_URL
PERMANENT_ADMINISTRATOR_ADDRESS = PROVISIONING.PERMANENT_ADMINISTRATOR_ADDRESS
STALWART_CONTAINER = PROVISIONING.STALWART_CONTAINER
ROUTE_KEY_DESCRIPTION = "AIAT Stalwart route lifecycle temporary"
ROUTE_KEY_CREATE_ID = "route-lifecycle-key"
ROUTE_KEY_VARIABLE = "STALWART_API_KEY"
ROUTE_KEY_METADATA_VERSION = 1
ROUTE_KEY_PERMISSIONS = (
    "authenticate",
    "sysMtaRouteGet",
    "sysMtaRouteCreate",
    "sysMtaRouteUpdate",
    "sysMtaRouteDestroy",
    "sysMtaOutboundStrategyGet",
    "sysMtaOutboundStrategyUpdate",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")


class Refused(RuntimeError):
    """A safe, user-facing refusal that contains no secret material."""


def default_metadata_path(secret_file: Path) -> Path:
    if secret_file.name.endswith(".env"):
        return secret_file.with_name(secret_file.name[:-4] + ".meta")
    return secret_file.with_name(secret_file.name + ".meta")


def _require_root_owned(path: Path, *, allow_current_user: bool = False) -> None:
    try:
        stat_result = path.stat()
    except OSError as exc:
        raise Refused("protected route credential file is unavailable") from exc
    owner_is_allowed = stat_result.st_uid == 0 or (
        allow_current_user and stat_result.st_uid == os.geteuid()
    )
    if not owner_is_allowed or stat_result.st_mode & 0o777 != 0o600:
        raise Refused("route credential files must be root-owned mode 0600")


def _read_text(path: Path, *, allow_current_user: bool = False) -> str:
    _require_root_owned(path, allow_current_user=allow_current_user)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise Refused("protected route credential file could not be read") from exc


def read_secret_file(path: Path, *, allow_current_user: bool = False) -> str:
    text = _read_text(path, allow_current_user=allow_current_user)
    lines = text.splitlines()
    if text.endswith("\n") is False or len(lines) != 1:
        raise Refused("route credential file must contain exactly one protected variable")
    key, separator, value = lines[0].partition("=")
    if separator != "=" or key != ROUTE_KEY_VARIABLE or not value:
        raise Refused("route credential file contains an unexpected variable")
    if any(character.isspace() for character in value) or not value.startswith("API_"):
        raise Refused("route credential file contains an invalid API key")
    return value


def read_metadata_file(path: Path, *, allow_current_user: bool = False) -> dict[str, Any]:
    try:
        value = json.loads(_read_text(path, allow_current_user=allow_current_user))
    except (json.JSONDecodeError, TypeError) as exc:
        raise Refused("route credential metadata is malformed") from exc
    if not isinstance(value, dict):
        raise Refused("route credential metadata is malformed")
    expected_keys = {
        "version",
        "purpose",
        "credentialId",
        "owner",
        "description",
        "expiresAt",
        "permissions",
    }
    if set(value) != expected_keys:
        raise Refused("route credential metadata contains unexpected fields")
    if value["version"] != ROUTE_KEY_METADATA_VERSION:
        raise Refused("route credential metadata version is unsupported")
    if value["purpose"] != "stalwart-route-lifecycle":
        raise Refused("route credential metadata purpose is invalid")
    if value["owner"] != PERMANENT_ADMINISTRATOR_ADDRESS:
        raise Refused("route credential owner is not the permanent administrator")
    if value["description"] != ROUTE_KEY_DESCRIPTION:
        raise Refused("route credential description is invalid")
    if not isinstance(value["credentialId"], str) or not _SAFE_ID.fullmatch(
        value["credentialId"]
    ):
        raise Refused("route credential identifier is malformed")
    if not isinstance(value["expiresAt"], str) or not value["expiresAt"]:
        raise Refused("route credential expiry is malformed")
    permissions = value["permissions"]
    if not isinstance(permissions, list) or any(
        not isinstance(permission, str) for permission in permissions
    ):
        raise Refused("route credential permissions are malformed")
    if tuple(permissions) != ROUTE_KEY_PERMISSIONS:
        raise Refused("route credential permissions are not least privilege")
    return value


def validate_local_files(
    secret_file: Path,
    metadata_file: Path,
    *,
    allow_current_user: bool = False,
) -> tuple[str, dict[str, Any]]:
    secret = read_secret_file(secret_file, allow_current_user=allow_current_user)
    metadata = read_metadata_file(metadata_file, allow_current_user=allow_current_user)
    return secret, metadata


def _write_reserved(fd: int, path: Path, text: str) -> None:
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
    except OSError as exc:
        raise Refused(f"protected output could not be written: {path.name}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _reserve_pair(secret_file: Path, metadata_file: Path) -> tuple[int, int]:
    if os.geteuid() != 0:
        raise Refused("run as root so route credential files are root-owned")
    secret_fd = -1
    metadata_fd = -1
    try:
        secret_fd = PROVISIONING.reserve_output(secret_file)
        metadata_fd = PROVISIONING.reserve_output(metadata_file)
        return secret_fd, metadata_fd
    except Exception:
        if secret_fd >= 0:
            os.close(secret_fd)
        if metadata_fd >= 0:
            os.close(metadata_fd)
        secret_file.unlink(missing_ok=True)
        metadata_file.unlink(missing_ok=True)
        raise


def _payload(
    *, expires_at: str, description: str = ROUTE_KEY_DESCRIPTION, create_id: str = ROUTE_KEY_CREATE_ID
) -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", expires_at):
        raise ValueError("expires_at must be an RFC 3339 UTC timestamp without fractions")
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [
            [
                "x:ApiKey/set",
                {
                    "create": {
                        create_id: {
                            "description": description,
                            "expiresAt": expires_at,
                            "permissions": {
                                "@type": "Replace",
                                "permissions": {
                                    permission: True for permission in ROUTE_KEY_PERMISSIONS
                                },
                            },
                        }
                    }
                },
                "create-route-lifecycle-api-key",
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
                "destroy-route-lifecycle-api-key",
            ]
        ],
    }


def _get_payload(credential_id: str) -> dict[str, Any]:
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [
            [
                "x:ApiKey/get",
                {
                    "ids": [credential_id],
                    "properties": ["description", "permissions", "expiresAt"],
                },
                "route-lifecycle-key-details",
            ]
        ],
    }


def _query_payload() -> dict[str, Any]:
    return {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [["x:ApiKey/query", {"limit": 100}, "route-lifecycle-key-query"]],
    }


def _strict_jmap(
    *,
    transport: Any,
    url: str,
    authorization: str,
    payload: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    method = payload["methodCalls"][0][0]
    try:
        response = transport.json(
            url,
            authorization,
            payload=payload,
            endpoint_path=PROVISIONING.endpoint_path(url),
            jmap_method=method,
            authentication_mechanism="oauth2-bearer-management-jmap",
        )
        JMAP_RESPONSE.validate_jmap_response(
            payload,
            response,
            action=action,
            http_status="200",
            endpoint_path=PROVISIONING.endpoint_path(url),
        )
    except JMAP_RESPONSE.JmapResponseError as exc:
        raise Refused("JMAP response validation failed") from exc
    except PROVISIONING.Refused as exc:
        raise Refused("Stalwart rejected the route credential operation") from exc
    return response


def _method_result(response: dict[str, Any], method: str) -> dict[str, Any]:
    try:
        return PROVISIONING.method_result(response, method)
    except PROVISIONING.Refused as exc:
        raise Refused("Stalwart returned an unexpected JMAP result") from exc


def _record_permissions(record: dict[str, Any]) -> tuple[str, ...]:
    permissions = record.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("@type") != "Replace":
        raise Refused("route credential does not use Replace permissions")
    values = permissions.get("permissions")
    if not isinstance(values, dict) or set(values) != set(ROUTE_KEY_PERMISSIONS):
        raise Refused("route credential has unexpected effective permissions")
    if any(value is not True for value in values.values()):
        raise Refused("route credential has disabled or malformed permissions")
    return tuple(permission for permission in ROUTE_KEY_PERMISSIONS if values[permission])


def _record_for_admin(
    *, transport: Any, jmap_url: str, admin_authorization: str, credential_id: str, action: str
) -> dict[str, Any]:
    response = _strict_jmap(
        transport=transport,
        url=jmap_url,
        authorization=admin_authorization,
        payload=_get_payload(credential_id),
        action=action,
    )
    result = _method_result(response, "x:ApiKey/get")
    records = result.get("list")
    not_found = result.get("notFound", [])
    if not isinstance(records, list) or not isinstance(not_found, list):
        raise Refused("Stalwart returned malformed API-key details")
    if len(records) != 1 or not_found:
        raise Refused("temporary route credential was not found exactly once")
    record = records[0]
    if not isinstance(record, dict) or record.get("id") != credential_id:
        raise Refused("temporary route credential identity did not match")
    if record.get("description") != ROUTE_KEY_DESCRIPTION:
        raise Refused("the persisted API key is not the AIAT route credential")
    _record_permissions(record)
    return record


def _refuse_duplicate(
    *, transport: Any, jmap_url: str, admin_authorization: str
) -> None:
    response = _strict_jmap(
        transport=transport,
        url=jmap_url,
        authorization=admin_authorization,
        payload=_query_payload(),
        action="provision-duplicate-check",
    )
    result = _method_result(response, "x:ApiKey/query")
    ids = result.get("ids")
    if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
        raise Refused("Stalwart returned malformed API-key query results")
    if not ids:
        return
    get_payload = {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [
            [
                "x:ApiKey/get",
                {"ids": ids, "properties": ["description", "expiresAt"]},
                "route-lifecycle-duplicate-details",
            ]
        ],
    }
    details = _strict_jmap(
        transport=transport,
        url=jmap_url,
        authorization=admin_authorization,
        payload=get_payload,
        action="provision-duplicate-check",
    )
    records = _method_result(details, "x:ApiKey/get").get("list")
    if isinstance(records, list) and any(
        isinstance(record, dict) and record.get("description") == ROUTE_KEY_DESCRIPTION
        for record in records
    ):
        raise Refused("an AIAT route-lifecycle API key already exists")


def _route_gets(*, transport: Any, jmap_url: str, route_authorization: str, action: str) -> None:
    for method, tag, arguments in (
        ("x:MtaRoute/get", "routes", {}),
        ("x:MtaOutboundStrategy/get", "strategy", {"ids": ["singleton"]}),
    ):
        payload = {
            "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
            "methodCalls": [[method, arguments, tag]],
        }
        _strict_jmap(
            transport=transport,
            url=jmap_url,
            authorization=route_authorization,
            payload=payload,
            action=action,
        )


def _discover(
    *, transport: Any, base_url: str, authorization: str, diagnostic: Any
) -> str:
    try:
        return PROVISIONING.discover_jmap_api_url(
            transport=transport,
            base_url=base_url,
            authorization=authorization,
            diagnostic=diagnostic,
        )
    except PROVISIONING.Refused as exc:
        raise Refused("local Stalwart JMAP endpoint discovery failed") from exc


def _admin_login(
    *, transport: Any, base_url: str, administrator_address: str, password: str, diagnostic: Any
) -> tuple[str, str]:
    if administrator_address != PERMANENT_ADMINISTRATOR_ADDRESS:
        raise Refused("only the permanent directory administrator may own this key")
    try:
        access_token = PROVISIONING.authenticate_administrator(
            transport=transport,
            base_url=base_url,
            administrator_address=administrator_address,
            administrator_password=password,
            diagnostic=diagnostic,
        )
    except PROVISIONING.Refused as exc:
        raise Refused("permanent administrator authentication failed") from exc
    return access_token, f"Bearer {access_token}"


def provision(
    *,
    base_url: str,
    administrator_address: str,
    administrator_password: str,
    output: Path,
    metadata_file: Path,
    expires_at: str,
    server_image: str,
    diagnostic: Any,
) -> None:
    secret_fd = metadata_fd = -1
    created_id = ""
    completed = False
    transport = PROVISIONING.HttpTransport(diagnostic)
    try:
        secret_fd, metadata_fd = _reserve_pair(output, metadata_file)
        PROVISIONING.require_patched_server(server_image, diagnostic)
        _access_token, admin_authorization = _admin_login(
            transport=transport,
            base_url=base_url,
            administrator_address=administrator_address,
            password=administrator_password,
            diagnostic=diagnostic,
        )
        jmap_url = _discover(
            transport=transport,
            base_url=base_url,
            authorization=admin_authorization,
            diagnostic=diagnostic,
        )
        _refuse_duplicate(
            transport=transport,
            jmap_url=jmap_url,
            admin_authorization=admin_authorization,
        )
        PROVISIONING.prove_persisted_create_permission(
            transport=transport,
            jmap_url=jmap_url,
            authorization=admin_authorization,
            administrator_address=administrator_address,
            diagnostic=diagnostic,
        )
        response = _strict_jmap(
            transport=transport,
            url=jmap_url,
            authorization=admin_authorization,
            payload=_payload(expires_at=expires_at),
            action="provision-route-lifecycle-key",
        )
        result = _method_result(response, "x:ApiKey/set")
        created = result.get("created")
        created = created.get(ROUTE_KEY_CREATE_ID) if isinstance(created, dict) else None
        created_id = created.get("id") if isinstance(created, dict) else ""
        api_key = created.get("secret") if isinstance(created, dict) else ""
        if (
            not isinstance(created_id, str)
            or not _SAFE_ID.fullmatch(created_id)
            or not isinstance(api_key, str)
            or not api_key.startswith("API_")
            or any(character.isspace() for character in api_key)
        ):
            raise Refused("Stalwart did not return a valid temporary route credential")
        diagnostic.sensitive_values.append(api_key)
        _record_for_admin(
            transport=transport,
            jmap_url=jmap_url,
            admin_authorization=admin_authorization,
            credential_id=created_id,
            action="provision-route-lifecycle-key",
        )
        metadata = {
            "version": ROUTE_KEY_METADATA_VERSION,
            "purpose": "stalwart-route-lifecycle",
            "credentialId": created_id,
            "owner": PERMANENT_ADMINISTRATOR_ADDRESS,
            "description": ROUTE_KEY_DESCRIPTION,
            "expiresAt": expires_at,
            "permissions": list(ROUTE_KEY_PERMISSIONS),
        }
        reserved_secret_fd = secret_fd
        secret_fd = -1
        _write_reserved(reserved_secret_fd, output, f"{ROUTE_KEY_VARIABLE}={api_key}\n")
        reserved_metadata_fd = metadata_fd
        metadata_fd = -1
        _write_reserved(
            reserved_metadata_fd,
            metadata_file,
            json.dumps(metadata, sort_keys=True) + "\n",
        )
        completed = True
    except Exception:
        if created_id:
            try:
                # Cleanup uses the same strict response validator. A failed
                # cleanup is intentionally not hidden from the caller.
                if "jmap_url" in locals() and "admin_authorization" in locals():
                    _strict_jmap(
                        transport=transport,
                        url=jmap_url,
                        authorization=admin_authorization,
                        payload=_destroy_payload(created_id),
                        action="provision-route-lifecycle-key-cleanup",
                    )
            except Exception as cleanup_error:
                raise Refused("temporary route credential cleanup could not be proven") from cleanup_error
        raise
    finally:
        for descriptor in (secret_fd, metadata_fd):
            if descriptor >= 0:
                os.close(descriptor)
        if not completed:
            output.unlink(missing_ok=True)
            metadata_file.unlink(missing_ok=True)


def validate(
    *,
    base_url: str,
    administrator_address: str,
    administrator_password: str,
    secret_file: Path,
    metadata_file: Path,
    diagnostic: Any,
) -> None:
    api_key, metadata = validate_local_files(secret_file, metadata_file)
    if metadata["owner"] != administrator_address:
        raise Refused("route credential metadata owner does not match the administrator")
    transport = PROVISIONING.HttpTransport(diagnostic)
    _access_token, admin_authorization = _admin_login(
        transport=transport,
        base_url=base_url,
        administrator_address=administrator_address,
        password=administrator_password,
        diagnostic=diagnostic,
    )
    jmap_url = _discover(
        transport=transport,
        base_url=base_url,
        authorization=admin_authorization,
        diagnostic=diagnostic,
    )
    _record_for_admin(
        transport=transport,
        jmap_url=jmap_url,
        admin_authorization=admin_authorization,
        credential_id=metadata["credentialId"],
        action="validate-route-lifecycle-key",
    )
    route_jmap_url = _discover(
        transport=transport,
        base_url=base_url,
        authorization=f"Bearer {api_key}",
        diagnostic=diagnostic,
    )
    _route_gets(
        transport=transport,
        jmap_url=route_jmap_url,
        route_authorization=f"Bearer {api_key}",
        action="validate-route-lifecycle-key",
    )


def revoke(
    *,
    base_url: str,
    administrator_address: str,
    administrator_password: str,
    secret_file: Path,
    metadata_file: Path,
    diagnostic: Any,
) -> None:
    api_key, metadata = validate_local_files(secret_file, metadata_file)
    transport = PROVISIONING.HttpTransport(diagnostic)
    _access_token, admin_authorization = _admin_login(
        transport=transport,
        base_url=base_url,
        administrator_address=administrator_address,
        password=administrator_password,
        diagnostic=diagnostic,
    )
    jmap_url = _discover(
        transport=transport,
        base_url=base_url,
        authorization=admin_authorization,
        diagnostic=diagnostic,
    )
    try:
        _record_for_admin(
            transport=transport,
            jmap_url=jmap_url,
            admin_authorization=admin_authorization,
            credential_id=metadata["credentialId"],
            action="revoke-route-lifecycle-key",
        )
    except Refused as exc:
        if "was not found exactly once" not in str(exc):
            raise
        # A missing exact ID is safe only after the old bearer is rejected.
        try:
            _discover(
                transport=transport,
                base_url=base_url,
                authorization=f"Bearer {api_key}",
                diagnostic=diagnostic,
            )
        except PROVISIONING.Refused:
            secret_file.unlink()
            metadata_file.unlink(missing_ok=True)
            return
        raise Refused("temporary route credential state was ambiguous") from exc
    _strict_jmap(
        transport=transport,
        url=jmap_url,
        authorization=admin_authorization,
        payload=_destroy_payload(metadata["credentialId"]),
        action="revoke-route-lifecycle-key",
    )
    after = _strict_jmap(
        transport=transport,
        url=jmap_url,
        authorization=admin_authorization,
        payload=_get_payload(metadata["credentialId"]),
        action="revoke-route-lifecycle-key",
    )
    after_result = _method_result(after, "x:ApiKey/get")
    if after_result.get("list") or after_result.get("notFound") != [metadata["credentialId"]]:
        raise Refused("server-side route credential revocation was not proven")
    try:
        _discover(
            transport=transport,
            base_url=base_url,
            authorization=f"Bearer {api_key}",
            diagnostic=diagnostic,
        )
    except PROVISIONING.Refused:
        secret_file.unlink()
        metadata_file.unlink(missing_ok=True)
        return
    raise Refused("revoked route credential still authenticated")


def print_result(action: str, secret_file: Path, metadata_file: Path) -> None:
    print(f"ROUTE_LIFECYCLE_{action.upper()}=PASS")
    print(f"PROTECTED_CREDENTIAL_FILE={secret_file}")
    print(f"PROTECTED_METADATA_FILE={metadata_file}")
    print("ROUTE_LIFECYCLE_SECRET_PRINTED=NONE")
