#!/usr/bin/env python3
"""Governed, root-only completion of the local Resend route activation.

This command deliberately stops after a read-only certification preflight. It
never calls ``certify-resend.sh`` and never submits a JMAP Email or
EmailSubmission mutation.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

WORKSPACE = Path("/mnt/c/projects/AIAT")
GATEWAY = WORKSPACE / "mas/infra/smtp-gateway"
PROFILE = GATEWAY / "profiles/oci-e2.1-micro-host.env.active"
ROUTE_SCRIPT = GATEWAY / "scripts/configure-stalwart-resend-route.sh"
CERTIFICATION_VALIDATOR = GATEWAY / "scripts/validate-stalwart-certification-credentials.py"
PREFLIGHT_SCRIPT = GATEWAY / "scripts/preflight-resend-certification.sh"

DEFAULT_CONTROL_FILE = Path("/etc/aiat/email-route-finish.env")
ROUTE_SECRET_FILE = Path("/etc/aiat/stalwart-route-lifecycle.env")
ROUTE_METADATA_FILE = Path("/etc/aiat/stalwart-route-lifecycle.meta")
CERTIFICATION_SECRET_FILE = Path("/etc/aiat/resend-certification.env")
RELAY_SECRET_FILE = Path("/etc/aiat/stalwart-resend.env")
BACKUP_FILE = Path("/secure/rollback/stalwart-resend-route-20260731T205925Z.json")
ADMIN_SOURCE_FILE = Path("/etc/aiat/stalwart-admin-source.env")
EVIDENCE_PARENT = Path("/secure/rollback")
LOCK_FILE = Path("/run/lock/aiat-resend-route-finish.lock")
STALWART_CONTAINER = "mas-stalwart-1"
LOCAL_URL = "http://127.0.0.1:18080"
ACCOUNT_ID = "w"
SENDER = "gateway-test@agents.aiat.ca"
PERMANENT_ADMINISTRATOR_ADDRESS = "admin@agents.aiat.local"
PINNED_IMAGE = (
    "ghcr.io/stalwartlabs/stalwart:v0.16.15@"
    "sha256:4f926193e5dd9ceb1e24ba48160702310381b12e51972c2fb0cc9de020388136"
)

CONTROL_KEYS = (
    "AIAT_EMAIL_ROUTE_FINISH_APPROVED",
    "APPROVE_TEMPORARY_ROUTE_KEY_CREATION",
    "APPROVE_RESEND_ROUTE_CHANGE",
    "APPROVE_TEMPORARY_ROUTE_KEY_REVOCATION",
    "APPROVE_REMOVE_SYS_API_KEY_CREATE",
    "APPROVE_CERTIFICATION_MESSAGE",
    "APPROVE_EXTERNAL_MAILBOX_ACCESS",
    "APPROVE_EXTERNAL_REPLY",
    "STOP_AFTER_CERTIFICATION_PREFLIGHT",
)
REQUIRED_TRUE_CONTROLS = {
    "AIAT_EMAIL_ROUTE_FINISH_APPROVED",
    "APPROVE_TEMPORARY_ROUTE_KEY_CREATION",
    "APPROVE_RESEND_ROUTE_CHANGE",
    "APPROVE_TEMPORARY_ROUTE_KEY_REVOCATION",
    "APPROVE_REMOVE_SYS_API_KEY_CREATE",
    "STOP_AFTER_CERTIFICATION_PREFLIGHT",
}
REQUIRED_FALSE_CONTROLS = {
    "APPROVE_CERTIFICATION_MESSAGE",
    "APPROVE_EXTERNAL_MAILBOX_ACCESS",
    "APPROVE_EXTERNAL_REPLY",
}
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
CONTROL_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(true|false)$")
SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _load_module(name: str, filename: str) -> Any:
    specification = importlib.util.spec_from_file_location(name, GATEWAY / "scripts" / filename)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"required local helper {filename} is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


CREDENTIALS = _load_module(
    "stalwart_route_lifecycle_credentials_for_finish",
    "stalwart_route_lifecycle_credentials.py",
)
ADMIN_SOURCE = _load_module(
    "stalwart_admin_source_for_finish",
    "stalwart_admin_source.py",
)
PROVISIONING = CREDENTIALS.PROVISIONING
JMAP_RESPONSE = CREDENTIALS.JMAP_RESPONSE


class FinishRefused(RuntimeError):
    """A fail-closed refusal that is safe to show to the operator."""


def _safe_message(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"API_[A-Za-z0-9_-]+", "<redacted-api-key>", text)
    text = re.sub(r"(?i)\b(?:basic|bearer|oauth)\s+[^\s,;]+", "<redacted-auth>", text)
    text = re.sub(
        r"(?i)(password|secret|token|authorization)\s*[:=]\s*[^,\s]+",
        r"\1=<redacted>",
        text,
    )
    text = "".join(character for character in text if character.isprintable())
    return (text or "operation refused")[:240]


def _require_root_file(path: Path, *, mode: int = 0o600) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise FinishRefused(f"protected file {path.name} is unavailable") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise FinishRefused(f"protected file {path.name} must be a regular file")
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != mode:
        raise FinishRefused(f"protected file {path.name} must be root-owned mode {mode:o}")
    return details


def _read_protected_text(path: Path, *, mode: int = 0o600) -> str:
    _require_root_file(path, mode=mode)
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FinishRefused(f"protected file {path.name} could not be read") from exc
    if "\x00" in value:
        raise FinishRefused(f"protected file {path.name} contains invalid data")
    return value


def parse_control_file(path: Path) -> dict[str, bool]:
    value = _read_protected_text(path)
    if not value.endswith("\n"):
        raise FinishRefused("approval control file must end with a newline")
    parsed: dict[str, bool] = {}
    for raw_line in value.splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        match = CONTROL_LINE.fullmatch(raw_line)
        if match is None:
            raise FinishRefused("approval control file contains a malformed line")
        key, raw_value = match.groups()
        if key not in CONTROL_KEYS or key in parsed:
            raise FinishRefused("approval control file contains an unknown or duplicate key")
        parsed[key] = raw_value == "true"
    if set(parsed) != set(CONTROL_KEYS):
        raise FinishRefused("approval control file does not contain the exact control set")
    if any(not parsed[key] for key in REQUIRED_TRUE_CONTROLS):
        raise FinishRefused("required route-activation approval is not true")
    if any(parsed[key] for key in REQUIRED_FALSE_CONTROLS):
        raise FinishRefused("certification or external access approval must remain false")
    return parsed


def parse_env_file(path: Path) -> dict[str, str]:
    """Validate the dedicated source without shell evaluation."""
    try:
        return ADMIN_SOURCE.read_protected_admin_source(path)
    except ADMIN_SOURCE.AdminSourceRefused as exc:
        raise FinishRefused("protected admin source is invalid") from exc


def read_permanent_admin_password(source: Path) -> str:
    try:
        return ADMIN_SOURCE.read_permanent_admin_password(source)
    except ADMIN_SOURCE.AdminSourceRefused as exc:
        raise FinishRefused("protected admin source is invalid") from exc


def _parse_profile(path: Path) -> dict[str, str]:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FinishRefused("active SMTP gateway profile is unavailable") from exc
    parsed: dict[str, str] = {}
    for raw_line in value.splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, item = raw_line.partition("=")
        if separator != "=" or ENV_KEY.fullmatch(key) is None or key in parsed:
            raise FinishRefused("active SMTP gateway profile is malformed")
        parsed[key] = item
    required_values = {
        "DEPLOYMENT_TOPOLOGY": "smtp_gateway_vps_home_stalwart_resend",
        "AGENT_MAIL_DOMAIN": "agents.aiat.ca",
        "DIRECT_MX_OUTBOUND_ENABLED": "false",
        "DEFAULT_OUTBOUND_ENABLED": "false",
        "OUTBOUND_RELAY_CERTIFIED": "false",
        "OUTBOUND_RELAY_HOST": "smtp.resend.com",
        "OUTBOUND_RELAY_PORT": "465",
        "OUTBOUND_RELAY_TLS_MODE": "implicit",
    }
    if any(parsed.get(key) != expected for key, expected in required_values.items()):
        raise FinishRefused(
            "active SMTP gateway profile is not the approved un-certified relay profile"
        )
    return parsed


def _read_certification_values(path: Path) -> dict[str, str]:
    value = _read_protected_text(path)
    lines = value.splitlines()
    expected = ("STALWART_API_KEY", "STALWART_JMAP_SERVICE_TOKEN")
    if not value.endswith("\n") or len(lines) != len(expected):
        raise FinishRefused("certification credential file has an unexpected shape")
    result: dict[str, str] = {}
    for key, line in zip(expected, lines, strict=True):
        prefix = f"{key}="
        if not line.startswith(prefix) or not line[len(prefix) :]:
            raise FinishRefused("certification credential file has an unexpected variable")
        result[key] = line[len(prefix) :]
    return result


def _read_relay_secret(path: Path) -> str:
    value = _read_protected_text(path)
    lines = value.splitlines()
    prefix = "RESEND_API_KEY="
    if not value.endswith("\n") or len(lines) != 1 or not lines[0].startswith(prefix):
        raise FinishRefused("relay secret file has an unexpected shape")
    secret = lines[0][len(prefix) :]
    if len(secret) < 20 or any(character.isspace() for character in secret):
        raise FinishRefused("relay secret file contains an invalid secret")
    return secret


def _artifact_snapshot(path: Path) -> dict[str, Any]:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return {"name": path.name, "exists": False}
    except OSError as exc:
        raise FinishRefused(f"could not inspect protected artifact {path.name}") from exc
    return {
        "name": path.name,
        "exists": True,
        "regular": stat.S_ISREG(details.st_mode),
        "symlink": stat.S_ISLNK(details.st_mode),
        "owner_uid": details.st_uid,
        "mode": f"{stat.S_IMODE(details.st_mode):04o}",
        "size": details.st_size,
    }


def _safe_artifact_name(path: Path) -> str:
    name = path.name
    if name not in {".", ".."} and SAFE_ARTIFACT_NAME.fullmatch(name) is not None:
        return name
    return "temporary-artifact"


def _assert_absent(*paths: Path) -> None:
    for path in paths:
        name = _safe_artifact_name(path)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise FinishRefused(f"could not inspect temporary artifact {name}") from exc
        raise FinishRefused(f"refusing to overwrite existing temporary artifact {name}")


def _repository_source_filename(filename: str) -> str | None:
    try:
        candidate = Path(filename)
        if not candidate.is_absolute():
            candidate = WORKSPACE / candidate
        relative = candidate.absolute().relative_to(WORKSPACE)
    except (OSError, ValueError):
        return None
    name = relative.name
    if name in {".", ".."} or SAFE_ARTIFACT_NAME.fullmatch(name) is None:
        return None
    return name


def _unexpected_exception_evidence(exc: BaseException) -> dict[str, Any]:
    exception_type = type(exc).__name__
    if SAFE_IDENTIFIER.fullmatch(exception_type) is None:
        exception_type = "Exception"
    evidence: dict[str, Any] = {"exception_type": exception_type}
    traceback = exc.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        source_filename = _repository_source_filename(code.co_filename)
        function_name = code.co_name
        if (
            source_filename is not None
            and SAFE_IDENTIFIER.fullmatch(function_name) is not None
            and traceback.tb_lineno > 0
        ):
            evidence.update(
                {
                    "source_filename": source_filename,
                    "line_number": traceback.tb_lineno,
                    "function_name": function_name,
                }
            )
        traceback = traceback.tb_next
    return evidence


def _write_exclusive(path: Path, value: str) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except OSError as exc:
        raise FinishRefused(f"sanitized evidence file {path.name} could not be created") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_evidence_dir() -> Path:
    try:
        parent = EVIDENCE_PARENT.lstat()
    except OSError as exc:
        raise FinishRefused("sanitized evidence parent is unavailable") from exc
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0:
        raise FinishRefused("sanitized evidence parent must be a root-owned directory")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = EVIDENCE_PARENT / f"resend-route-finish-{stamp}-{os.getpid()}"
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise FinishRefused("sanitized evidence directory could not be created") from exc
    return path


def _write_evidence(directory: Path, name: str, document: dict[str, Any]) -> None:
    if not re.fullmatch(r"[a-z0-9-]+\.json", name):
        raise FinishRefused("evidence filename is invalid")
    _write_exclusive(
        directory / name,
        json.dumps(document, indent=2, sort_keys=True) + "\n",
    )


def _safe_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "LANG": "C",
        "LC_ALL": "C",
    }


def _run(label: str, command: Sequence[str], *, timeout: int = 180) -> None:
    try:
        result = subprocess.run(
            list(command),
            cwd=WORKSPACE,
            env=_safe_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinishRefused(f"{label} could not be executed") from exc
    if result.returncode != 0:
        raise FinishRefused(f"{label} failed with exit status {result.returncode}")


def _capture(command: Sequence[str], *, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=WORKSPACE,
            env=_safe_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinishRefused("read-only local inspection could not be executed") from exc
    if result.returncode != 0:
        raise FinishRefused("read-only local inspection failed")
    return result.stdout.strip()


def _git_snapshot() -> dict[str, Any]:
    safe_directory = f"safe.directory={WORKSPACE}"
    commit = _capture(["git", "-c", safe_directory, "rev-parse", "HEAD"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise FinishRefused("repository HEAD is not a valid commit identifier")
    status = _capture(
        ["git", "-c", safe_directory, "status", "--porcelain", "--untracked-files=no"]
    )
    return {"commit": commit, "worktree_dirty": bool(status)}


def _container_snapshot() -> dict[str, Any]:
    template = (
        "{{.Id}}\t{{.Config.Image}}\t{{.State.Running}}\t{{.State.OOMKilled}}\t"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\t"
        "{{range .Mounts}}{{.Destination}}={{.RW}};{{end}}"
    )
    output = _capture(["docker", "inspect", "--format", template, STALWART_CONTAINER])
    fields = output.split("\t")
    if len(fields) != 6 or not fields[0] or not fields[1]:
        raise FinishRefused("running Stalwart container inspection was malformed")
    snapshot = {
        "id": fields[0],
        "image": fields[1],
        "running": fields[2],
        "oom_killed": fields[3],
        "health": fields[4],
        "mounts": fields[5],
    }
    if snapshot["image"] != PINNED_IMAGE:
        raise FinishRefused("running Stalwart image is not the approved pinned digest")
    if snapshot["running"] != "true" or snapshot["oom_killed"] != "false":
        raise FinishRefused("running Stalwart container is not healthy for a local transaction")
    return snapshot


def _service_authorization(value: str) -> str:
    if value.startswith(("Bearer ", "Basic ", "OAuth ")):
        return value
    return f"Bearer {value}"


def _submission_count(certification_values: dict[str, str]) -> int:
    diagnostic = PROVISIONING.DiagnosticState(sensitive_values=list(certification_values.values()))
    transport = PROVISIONING.HttpTransport(diagnostic)
    authorization = _service_authorization(certification_values["STALWART_JMAP_SERVICE_TOKEN"])
    try:
        jmap_url = PROVISIONING.discover_jmap_api_url(
            transport=transport,
            base_url=LOCAL_URL,
            authorization=authorization,
            diagnostic=diagnostic,
        )
        payload = {
            "using": [
                "urn:ietf:params:jmap:core",
                "urn:ietf:params:jmap:submission",
            ],
            "methodCalls": [
                [
                    "EmailSubmission/query",
                    {"accountId": ACCOUNT_ID, "limit": 100},
                    "read-only-certification-submission-count",
                ]
            ],
        }
        response = transport.json(
            jmap_url,
            authorization,
            payload=payload,
            endpoint_path=PROVISIONING.endpoint_path(jmap_url),
            jmap_method="EmailSubmission/query",
            authentication_mechanism="service-token-read-only-jmap",
        )
        JMAP_RESPONSE.validate_jmap_response(
            payload,
            response,
            action="read-only-certification-submission-count",
            http_status="200",
            endpoint_path=PROVISIONING.endpoint_path(jmap_url),
        )
        result = PROVISIONING.method_result(response, "EmailSubmission/query")
        ids = result.get("ids")
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise FinishRefused("read-only EmailSubmission query returned malformed ids")
        total = result.get("total", len(ids))
        if not isinstance(total, int) or isinstance(total, bool) or total < len(ids):
            raise FinishRefused("read-only EmailSubmission query returned malformed total")
        return total
    except (PROVISIONING.Refused, CREDENTIALS.Refused, JMAP_RESPONSE.JmapResponseError) as exc:
        raise FinishRefused("read-only EmailSubmission count could not be proven") from exc
    finally:
        diagnostic.sensitive_values.clear()


def _run_route_command(
    action: str,
    secret_file: Path,
    relay_secret_file: Path,
) -> None:
    command = [
        "sh",
        str(ROUTE_SCRIPT),
        action,
        str(PROFILE),
        "--secret-file",
        str(secret_file),
        "--relay-secret-file",
        str(relay_secret_file),
        "--stalwart-container",
        STALWART_CONTAINER,
        "--backup",
        str(BACKUP_FILE),
        "--admin-url",
        LOCAL_URL,
    ]
    if action == "apply":
        command.extend(["--route-metadata-file", str(ROUTE_METADATA_FILE)])
    _run(f"route {action}", command)


def _run_certification_validator() -> None:
    _run(
        "read-only certification credential validation",
        [
            "python3",
            str(CERTIFICATION_VALIDATOR),
            "--secret-file",
            str(CERTIFICATION_SECRET_FILE),
            "--account-id",
            ACCOUNT_ID,
        ],
    )


def _run_certification_preflight() -> None:
    _run(
        "read-only certification preflight",
        [
            "sh",
            str(PREFLIGHT_SCRIPT),
            str(PROFILE),
            "--secret-file",
            str(CERTIFICATION_SECRET_FILE),
            "--relay-secret-file",
            str(RELAY_SECRET_FILE),
            "--stalwart-container",
            STALWART_CONTAINER,
            "--account-id",
            ACCOUNT_ID,
            "--sender",
            SENDER,
            "--jmap-url",
            LOCAL_URL,
            "--admin-url",
            LOCAL_URL,
        ],
    )


def _provision_route_key(password: str) -> None:
    diagnostic = PROVISIONING.DiagnosticState(sensitive_values=[password])
    try:
        expires_at = (
            (datetime.now(UTC) + timedelta(hours=4))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        CREDENTIALS.provision(
            base_url=LOCAL_URL,
            administrator_address=PERMANENT_ADMINISTRATOR_ADDRESS,
            administrator_password=password,
            output=ROUTE_SECRET_FILE,
            metadata_file=ROUTE_METADATA_FILE,
            expires_at=expires_at,
            server_image=PINNED_IMAGE,
            diagnostic=diagnostic,
        )
    except (CREDENTIALS.Refused, PROVISIONING.Refused) as exc:
        raise FinishRefused("temporary route key provisioning was refused") from exc
    finally:
        diagnostic.sensitive_values.clear()


def _validate_route_key(password: str) -> None:
    diagnostic = PROVISIONING.DiagnosticState(sensitive_values=[password])
    try:
        CREDENTIALS.validate(
            base_url=LOCAL_URL,
            administrator_address=PERMANENT_ADMINISTRATOR_ADDRESS,
            administrator_password=password,
            secret_file=ROUTE_SECRET_FILE,
            metadata_file=ROUTE_METADATA_FILE,
            diagnostic=diagnostic,
        )
    except (CREDENTIALS.Refused, PROVISIONING.Refused) as exc:
        raise FinishRefused("temporary route key validation was refused") from exc
    finally:
        diagnostic.sensitive_values.clear()


def _revoke_route_key(password: str) -> None:
    diagnostic = PROVISIONING.DiagnosticState(sensitive_values=[password])
    try:
        CREDENTIALS.revoke(
            base_url=LOCAL_URL,
            administrator_address=PERMANENT_ADMINISTRATOR_ADDRESS,
            administrator_password=password,
            secret_file=ROUTE_SECRET_FILE,
            metadata_file=ROUTE_METADATA_FILE,
            diagnostic=diagnostic,
        )
    except (CREDENTIALS.Refused, PROVISIONING.Refused) as exc:
        raise FinishRefused("temporary route key revocation was not proven") from exc
    finally:
        diagnostic.sensitive_values.clear()


def _remove_create_permission(password: str) -> None:
    diagnostic = PROVISIONING.DiagnosticState(sensitive_values=[password])
    try:
        CREDENTIALS.remove_sys_api_key_create(
            base_url=LOCAL_URL,
            administrator_address=PERMANENT_ADMINISTRATOR_ADDRESS,
            administrator_password=password,
            diagnostic=diagnostic,
        )
    except (CREDENTIALS.Refused, PROVISIONING.Refused) as exc:
        raise FinishRefused("sysApiKeyCreate removal was not proven") from exc
    finally:
        diagnostic.sensitive_values.clear()


def _run_finish(
    control_file: Path,
    admin_source_file: Path,
) -> tuple[dict[str, Any], Path]:
    controls = parse_control_file(control_file)
    lock_parent = LOCK_FILE.parent
    try:
        lock_parent.lstat()
    except OSError as exc:
        raise FinishRefused("route activation lock directory is unavailable") from exc
    try:
        existing_lock = LOCK_FILE.lstat()
    except FileNotFoundError:
        existing_lock = None
    except OSError as exc:
        raise FinishRefused("route activation lock file is unavailable") from exc
    if existing_lock is not None and stat.S_ISLNK(existing_lock.st_mode):
        raise FinishRefused("route activation lock file may not be a symbolic link")
    try:
        lock_descriptor = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise FinishRefused("route activation lock could not be acquired") from exc
    lock_handle = os.fdopen(lock_descriptor, "r+")
    evidence_dir: Path | None = None
    admin_password = ""
    certification_values: dict[str, str] = {}
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise FinishRefused("another route activation is already running") from exc

        evidence_dir = _create_evidence_dir()
        profile = _parse_profile(PROFILE)
        admin_password = read_permanent_admin_password(admin_source_file)
        certification_values = _read_certification_values(CERTIFICATION_SECRET_FILE)
        _read_relay_secret(RELAY_SECRET_FILE)
        _require_root_file(BACKUP_FILE)
        _assert_absent(ROUTE_SECRET_FILE, ROUTE_METADATA_FILE)

        before = {
            "git": _git_snapshot(),
            "container": _container_snapshot(),
            "artifacts": {
                "route_secret": _artifact_snapshot(ROUTE_SECRET_FILE),
                "route_metadata": _artifact_snapshot(ROUTE_METADATA_FILE),
                "certification": _artifact_snapshot(CERTIFICATION_SECRET_FILE),
                "relay": _artifact_snapshot(RELAY_SECRET_FILE),
                "backup": _artifact_snapshot(BACKUP_FILE),
            },
            "profile": {
                "outbound_relay_certified": profile["OUTBOUND_RELAY_CERTIFIED"],
                "direct_mx_outbound_enabled": profile["DIRECT_MX_OUTBOUND_ENABLED"],
                "default_outbound_enabled": profile["DEFAULT_OUTBOUND_ENABLED"],
            },
            "route_key_permissions": list(CREDENTIALS.ROUTE_KEY_PERMISSIONS),
            "control_approvals": controls,
            "certification_message_submitted": "NO",
            "external_mailbox_access": "NOT_PERFORMED",
            "external_reply": "NOT_PERFORMED",
        }

        # The initial route read and the submission inventory are both
        # independent and read-only. The certificate key is never used to
        # apply or revoke the route key.
        _run_route_command("inspect", CERTIFICATION_SECRET_FILE, RELAY_SECRET_FILE)
        _run_certification_validator()
        initial_submission_count = _submission_count(certification_values)
        if initial_submission_count != 0:
            raise FinishRefused(
                "existing EmailSubmission records prevent a zero-send certification run"
            )
        before["email_submission_count"] = initial_submission_count
        _write_evidence(evidence_dir, "preflight.json", before)

        _provision_route_key(admin_password)
        _validate_route_key(admin_password)
        _run_route_command("apply", ROUTE_SECRET_FILE, RELAY_SECRET_FILE)
        _run_route_command("verify", CERTIFICATION_SECRET_FILE, RELAY_SECRET_FILE)
        _run_certification_validator()
        _run_certification_preflight()
        final_submission_count = _submission_count(certification_values)
        if final_submission_count != 0:
            raise FinishRefused(
                "read-only certification preflight changed the EmailSubmission count"
            )

        _revoke_route_key(admin_password)
        _remove_create_permission(admin_password)
        _run_route_command("verify", CERTIFICATION_SECRET_FILE, RELAY_SECRET_FILE)

        after = {
            "git": _git_snapshot(),
            "container": _container_snapshot(),
            "artifacts": {
                "route_secret": _artifact_snapshot(ROUTE_SECRET_FILE),
                "route_metadata": _artifact_snapshot(ROUTE_METADATA_FILE),
                "certification": _artifact_snapshot(CERTIFICATION_SECRET_FILE),
                "relay": _artifact_snapshot(RELAY_SECRET_FILE),
                "backup": _artifact_snapshot(BACKUP_FILE),
            },
            "email_submission_count": final_submission_count,
            "certification_message_submitted": "NO",
            "external_mailbox_access": "NOT_PERFORMED",
            "external_reply": "NOT_PERFORMED",
            "temporary_route_key_revoked": "PASS",
            "sys_api_key_create_removed": "PASS",
            "profile_preserved_uncertified": profile["OUTBOUND_RELAY_CERTIFIED"] == "false",
        }
        if after["container"] != before["container"]:
            raise FinishRefused(
                "Stalwart container identity or mounts changed during the transaction"
            )
        if (
            after["artifacts"]["route_secret"]["exists"]
            or after["artifacts"]["route_metadata"]["exists"]
        ):
            raise FinishRefused("temporary route credential files remain after proven revocation")
        for artifact_name in ("certification", "relay", "backup"):
            if after["artifacts"][artifact_name] != before["artifacts"][artifact_name]:
                raise FinishRefused(f"protected {artifact_name} artifact changed unexpectedly")
        _write_evidence(
            evidence_dir,
            "result.json",
            {
                "version": 1,
                "final_status": "READY_FOR_MANUAL_CERTIFICATION",
                "before": before,
                "after": after,
            },
        )
        return {
            "evidence_dir": str(evidence_dir),
            "starting_commit": before["git"]["commit"],
            "final_commit": after["git"]["commit"],
            "container_unchanged": True,
            "route_activation": "PASS",
            "read_only_route_verification": "PASS",
            "certification_preflight": "PASS",
            "certification_message_submitted": "NO",
            "temporary_route_key_revoked": "PASS",
            "sys_api_key_create_removed": "PASS",
        }, evidence_dir
    except FinishRefused as exc:
        if evidence_dir is not None:
            with contextlib.suppress(FinishRefused):
                _write_evidence(
                    evidence_dir,
                    "failure.json",
                    {"version": 1, "final_status": "BLOCKED", "reason": _safe_message(exc)},
                )
        raise
    except Exception as exc:
        if evidence_dir is not None:
            with contextlib.suppress(FinishRefused):
                unexpected = _unexpected_exception_evidence(exc)
                _write_evidence(
                    evidence_dir,
                    "failure.json",
                    {
                        "version": 1,
                        "final_status": "BLOCKED",
                        "reason": f"unexpected {unexpected['exception_type']}",
                        **unexpected,
                    },
                )
        raise
    finally:
        admin_password = ""
        certification_values.clear()
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-file", type=Path, required=True)
    parser.add_argument(
        "--admin-source-file",
        type=Path,
        default=ADMIN_SOURCE_FILE,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        print("FINAL_STATUS=BLOCKED", file=sys.stderr)
        print("BLOCK_REASON=run the governed route activation as root", file=sys.stderr)
        return 1
    args = build_parser().parse_args(argv)
    evidence_dir: Path | None = None
    try:
        result, evidence_dir = _run_finish(
            args.control_file.absolute(),
            args.admin_source_file.absolute(),
        )
    except FinishRefused as exc:
        print("FINAL_STATUS=BLOCKED")
        print(f"BLOCK_REASON={_safe_message(exc)}")
        if evidence_dir is not None:
            with contextlib.suppress(FinishRefused):
                _write_evidence(
                    evidence_dir,
                    "failure.json",
                    {"version": 1, "final_status": "BLOCKED", "reason": _safe_message(exc)},
                )
        return 1
    except Exception as exc:  # pragma: no cover - last-resort fail-closed guard
        unexpected = _unexpected_exception_evidence(exc)
        print("FINAL_STATUS=BLOCKED")
        print(f"BLOCK_REASON=unexpected {unexpected['exception_type']}")
        return 1
    print("ROUTE_ACTIVATION=PASS")
    print("READ_ONLY_ROUTE_VERIFICATION=PASS")
    print("CERTIFICATION_PREFLIGHT=PASS")
    print("CERTIFICATION_MESSAGE_SUBMITTED=NO")
    print("READY_FOR_MANUAL_CERTIFICATION=YES")
    print("TEMPORARY_ROUTE_KEY_REVOKED=PASS")
    print("SYS_API_KEY_CREATE_REMOVED=PASS")
    print("CERTIFICATION_SECRETS_PRESERVED=YES")
    print("RELAY_SECRET_PRESERVED=YES")
    print("CONTAINER_UNCHANGED=YES")
    print(f"STARTING_COMMIT={result['starting_commit']}")
    print(f"FINAL_COMMIT={result['final_commit']}")
    print(f"SANITIZED_EVIDENCE_DIR={result['evidence_dir']}")
    print("CERTIFICATION_MESSAGE_NEXT_STEP=MANUAL_OPERATOR_ACTION_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
