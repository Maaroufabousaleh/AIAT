"""Run the real loopback Stalwart/identity-service acceptance matrix.

This is intentionally a host-side test: it sends SMTP to the loopback port and
uses the signed public identity API.  It never talks to Internet SMTP, DNS,
Resend, public TLS, or a public gateway.
"""

from __future__ import annotations

import base64
import json
import os
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


COMPOSE_DIR = Path(__file__).resolve().parents[1]
IDENTITY_URL = os.environ.get("LOCAL_IDENTITY_URL", "http://127.0.0.1:8011").rstrip("/")
SMTP_HOST = os.environ.get("LOCAL_SMTP_HOST", "127.0.0.1")
SMTP_PORT = int(os.environ.get("LOCAL_SMTP_PORT", "2525"))


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def signed_headers(private_key_b64: str, client_id: str, method: str, path: str, body: bytes) -> dict[str, str]:
    import hashlib

    raw = base64.b64decode(private_key_b64, validate=True)
    private = Ed25519PrivateKey.from_private_bytes(raw)
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    version = "aiat.identity.v1"
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"{version}\n{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()
    return {
        "X-AIAT-Signature-Version": version,
        "X-AIAT-Client-ID": client_id,
        "X-AIAT-Timestamp": timestamp,
        "X-AIAT-Nonce": nonce,
        "X-AIAT-Signature": base64.b64encode(private.sign(canonical)).decode("ascii"),
        "Content-Type": "application/json",
    }


def identity_request(
    *,
    env: dict[str, str],
    client_id: str,
    private_key: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    expected: set[int] | None = None,
) -> tuple[int, dict[str, Any]]:
    raw_body = json.dumps(body or {}, separators=(",", ":")).encode("utf-8") if method != "GET" else b""
    headers = signed_headers(private_key, client_id, method, path, raw_body)
    request = urllib.request.Request(f"{IDENTITY_URL}{path}", data=raw_body if method != "GET" else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = {}
    if expected is not None and status not in expected:
        raise RuntimeError(f"identity request {method} {path} returned {status}")
    return status, payload if isinstance(payload, dict) else {}


def wait_ready() -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{IDENTITY_URL}/readyz", timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError("identity-service did not become ready")


def send_local_message(*, recipient: str, code: str, marker: str) -> None:
    message = EmailMessage()
    message["From"] = "verification@sender.test"
    message["To"] = recipient
    message["Subject"] = f"AIAT local verification {marker}"
    message.set_content(f"Your AIAT verification code is {code}.\nMarker: {marker}\n")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.send_message(message)


def compose_restart() -> None:
    command = [
        "docker", "compose",
        "--env-file", "../../../.env",
        "--env-file", ".env.stalwart-local",
        "-f", "docker-compose.yml",
        "-f", "docker-compose.stalwart-local.yml",
        "--profile", "mail-local",
        "restart", "stalwart", "identity-postgres", "identity-service",
    ]
    subprocess.run(command, cwd=COMPOSE_DIR, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_ready()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:18080/healthz/ready", timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError("Stalwart did not become ready after restart")


def main() -> int:
    env = read_env(COMPOSE_DIR / ".env.stalwart-local")
    required = [
        "IDENTITY_CLIENT_PUBLIC_KEYS_JSON",
        "AIAT_IDENTITY_CLIENT_PRIVATE_KEY",
        "AIAT_IDENTITY_TOOL_PRIVATE_KEY",
    ]
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise RuntimeError("local identity env is incomplete; run bootstrap-stalwart-local.py first")
    clients = {
        "operator": (env.get("AIAT_IDENTITY_CLIENT_ID", "operator-laptop"), env["AIAT_IDENTITY_CLIENT_PRIVATE_KEY"]),
        "tool": (env.get("AIAT_IDENTITY_TOOL_CLIENT_ID", "tool-service"), env["AIAT_IDENTITY_TOOL_PRIVATE_KEY"]),
    }
    operator_id, operator_key = clients["operator"]
    domain = env.get("AGENT_MAIL_DOMAIN", "agents.aiat.local")
    company_id = uuid.uuid4()
    worker_a = uuid.uuid4()
    worker_b = uuid.uuid4()

    evidence: dict[str, Any] = {"provider": "stalwart-local", "internet_delivery_tested": False}

    status, _ = identity_request(
        env=env, client_id=operator_id, private_key=operator_key, method="POST", path="/v1/domains",
        body={"domain": domain, "actor": {"actor_id": "local-acceptance", "purpose": "local domain acceptance"}}, expected={200, 201, 409},
    )
    evidence["domain_create_status"] = status

    def provision(worker_id: uuid.UUID, key_suffix: str) -> dict[str, Any]:
        body = {
            "company_id": str(company_id),
            "worker_id": str(worker_id),
            "actor": {"actor_id": "local-acceptance", "purpose": "local mailbox acceptance"},
            "idempotency_key": f"mailbox:{company_id}:{worker_id}",
            "mailbox_class": "permanent",
        }
        _, value = identity_request(env=env, client_id=operator_id, private_key=operator_key, method="POST", path="/v1/worker-identities/provision", body=body, expected={200, 201})
        return value

    identity_a = provision(worker_a, "a")
    identity_a_retry = provision(worker_a, "a-retry")
    identity_b = provision(worker_b, "b")
    assert identity_a.get("provider_account_id") == identity_a_retry.get("provider_account_id"), "idempotent provisioning changed provider account"
    assert identity_a.get("address") != identity_b.get("address"), "worker mailboxes are not isolated"
    evidence["worker_mailboxes"] = {
        "a": {"worker_id": str(worker_a), "address": identity_a.get("address"), "provider_account_id": identity_a.get("provider_account_id")},
        "b": {"worker_id": str(worker_b), "address": identity_b.get("address"), "provider_account_id": identity_b.get("provider_account_id")},
        "idempotent": True,
    }

    marker_a = uuid.uuid4().hex[:12]
    marker_b = uuid.uuid4().hex[:12]
    send_local_message(recipient=str(identity_a["address"]), code="482913", marker=marker_a)
    send_local_message(recipient=str(identity_b["address"]), code="731604", marker=marker_b)

    def mail_list(worker_id: uuid.UUID, actor_id: str, expected: set[int] = {200}) -> tuple[int, dict[str, Any]]:
        return identity_request(
            env=env, client_id=operator_id, private_key=operator_key, method="POST", path="/v1/mail/list",
            body={"worker_id": str(worker_id), "actor": {"actor_id": actor_id, "purpose": "local mail list"}, "limit": 25}, expected=expected,
        )

    # Exercise both the explicit list/read routes and the sender-filtered wait.
    status, listed_a = mail_list(worker_a, str(worker_a))
    ids_a = (((listed_a.get("result") or {}).get("ids")) or [])
    if not ids_a:
        _, waited = identity_request(
            env=env, client_id=operator_id, private_key=operator_key, method="POST", path="/v1/mail/wait-for-verification",
            body={"worker_id": str(worker_a), "actor": {"actor_id": str(worker_a), "purpose": "local verification wait"}, "sender_domain": "sender.test", "timeout_seconds": 30}, expected={200},
        )
        message = waited.get("message") or {}
        message_list = ((message.get("result") or {}).get("list")) or []
        ids_a = [str(message_list[0]["id"])] if message_list and message_list[0].get("id") else []
    assert ids_a, "SMTP delivery did not produce a JMAP message"
    message_id_a = str(ids_a[0])
    _, read_a = identity_request(
        env=env, client_id=operator_id, private_key=operator_key, method="POST", path="/v1/mail/read",
        body={"worker_id": str(worker_a), "actor": {"actor_id": str(worker_a), "purpose": "local mail read"}, "message_id": message_id_a}, expected={200},
    )
    _, extracted_a = identity_request(
        env=env, client_id=operator_id, private_key=operator_key, method="POST", path="/v1/mail/extract-code",
        body={"worker_id": str(worker_a), "actor": {"actor_id": str(worker_a), "purpose": "local code extraction"}, "message_id": message_id_a}, expected={200},
    )
    assert extracted_a.get("code") == "482913", "verification-code extraction did not return the expected code"
    evidence["mail"] = {"smtp_loopback": True, "jmap_list_count": len(ids_a), "jmap_read": bool(read_a), "verification_code_extracted": True}

    # A signed client may not use a different worker's mailbox, even when the
    # request carries the target worker UUID.
    cross_status, _ = mail_list(worker_b, str(worker_a), expected={403})
    evidence["cross_worker_denied"] = cross_status == 403

    def verify(worker_id: uuid.UUID, message_id: str) -> dict[str, Any]:
        _, value = identity_request(
            env=env, client_id=operator_id, private_key=operator_key, method="POST", path=f"/v1/worker-identities/{worker_id}/verify",
            body={"actor": {"actor_id": str(worker_id), "purpose": "local JMAP delivery evidence"}, "provider_message_id": message_id}, expected={200},
        )
        return value

    active_a = verify(worker_a, message_id_a)
    status_b, listed_b = mail_list(worker_b, str(worker_b))
    ids_b = (((listed_b.get("result") or {}).get("ids")) or [])
    assert ids_b, "second mailbox did not receive its local SMTP message"
    active_b = verify(worker_b, str(ids_b[0]))
    assert active_a.get("state") == "IDENTITY_ACTIVE" and active_b.get("state") == "IDENTITY_ACTIVE"

    # Restart both durable providers, then prove the state and JMAP message
    # still exist. This is the persistence gate, not merely a health check.
    compose_restart()
    _, after_restart_a = identity_request(
        env=env, client_id=operator_id, private_key=operator_key, method="GET",
        path=f"/v1/worker-identities/{worker_a}?actor_id={worker_a}&purpose=local%20persistence%20check", expected={200},
    )
    _, after_restart_a_mail = mail_list(worker_a, str(worker_a))
    assert after_restart_a.get("state") == "IDENTITY_ACTIVE"
    assert message_id_a in (((after_restart_a_mail.get("result") or {}).get("ids")) or [])
    evidence["restart_persistence"] = {
        "containers": ["stalwart", "identity-postgres", "identity-service"],
        "identity_state": after_restart_a.get("state"),
        "mail_query_succeeded": True,
    }

    _, suspended = identity_request(
        env=env, client_id=operator_id, private_key=operator_key, method="POST", path=f"/v1/worker-identities/{worker_a}/suspend",
        body={"actor": {"actor_id": str(worker_a), "purpose": "local suspension gate"}}, expected={200},
    )
    denied_after_suspend, _ = mail_list(worker_a, str(worker_a), expected={403})
    _, archived = identity_request(
        env=env, client_id=operator_id, private_key=operator_key, method="POST", path=f"/v1/worker-identities/{worker_b}/archive",
        body={"actor": {"actor_id": str(worker_b), "purpose": "local revocation gate"}}, expected={200},
    )
    denied_after_archive, _ = mail_list(worker_b, str(worker_b), expected={403})
    assert suspended.get("state") == "SUSPENDED" and archived.get("state") == "ARCHIVED"
    evidence["suspension_and_revocation"] = {
        "suspended": suspended.get("state"),
        "archived": archived.get("state"),
        "suspended_mail_denied": denied_after_suspend == 403,
        "archived_mail_denied": denied_after_archive == 403,
    }

    print(json.dumps({"ok": True, "evidence": evidence}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, RuntimeError, ValueError, urllib.error.URLError, smtplib.SMTPException) as exc:
        print(f"local identity acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
