"""Prepare the loopback-only Stalwart credentials used by the local profile.

The script is intentionally idempotent and writes only the ignored
``.env.stalwart-local`` file.  It never prints a provider credential.  The
recovery administrator is used only to finish first boot and to create the
dedicated local JMAP service account; the identity-service receives a scoped
management API key and a Basic credential for that service account.

Run from ``mas/infra/compose`` after starting ``stalwart``.  If this is the
first run, the script applies the bootstrap settings and exits with a restart
instruction.  Run the same command again after ``docker compose restart
stalwart``.
"""

from __future__ import annotations

import base64
import json
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


COMPOSE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[3]
ROOT_ENV = ROOT_DIR / ".env"
LOCAL_ENV = COMPOSE_DIR / ".env.stalwart-local"
BASE_URL = "http://127.0.0.1:18080"
JMAP_URL = f"{BASE_URL}/jmap"

# Keep this list in lockstep with the identity-service Stalwart adapter.  The
# management key may grant these capabilities to a passwordless worker account
# without inheriting Stalwart's full default User role.
WORKER_PERMISSION_NAMES = (
    "emailReceive",
    "jmapMailboxGet",
    "jmapEmailGet",
    "jmapEmailQuery",
    "jmapEmailUpdate",
    "jmapEmailDestroy",
    "jmapIdentityGet",
    "jmapEmailSubmissionGet",
    "jmapEmailSubmissionQuery",
    "jmapEmailSubmissionCreate",
    "jmapEmailSubmissionUpdate",
    "jmapEmailSubmissionDestroy",
    "jmapEmailCreate",
)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def write_env(values: dict[str, str]) -> None:
    """Update the ignored local env without echoing values."""
    existing = LOCAL_ENV.read_text(encoding="utf-8").splitlines() if LOCAL_ENV.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen and not any(line.split("=", 1)[0].strip() == key for line in output if "=" in line):
            output.append(f"{key}={value}")
    LOCAL_ENV.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8", newline="\n")


def auth_header(credential: str) -> dict[str, str]:
    value = credential.strip()
    if value.lower().startswith(("basic ", "bearer ", "oauth ")):
        authorization = value
    else:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        authorization = f"Basic {encoded}"
    return {"Authorization": authorization, "Content-Type": "application/json"}


def request(credential: str, calls: list[list[Any]], *, management: bool = True) -> list[list[Any]]:
    using = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]
    if not management:
        using += ["urn:ietf:params:jmap:mail", "urn:ietf:params:jmap:submission"]
    payload = {"using": using, "methodCalls": calls}
    req = urllib.request.Request(
        JMAP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=auth_header(credential),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Stalwart HTTP request failed ({exc.code})") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Stalwart is not reachable on the loopback admin port") from exc
    responses = body.get("methodResponses")
    if not isinstance(responses, list):
        raise RuntimeError("Stalwart returned an invalid JMAP response")
    for response_item in responses:
        if isinstance(response_item, list) and response_item and response_item[0] == "error":
            details = response_item[1] if len(response_item) > 1 and isinstance(response_item[1], dict) else {}
            raise RuntimeError(f"Stalwart rejected a local bootstrap operation ({details.get('type', 'error')})")
    return responses


def response_body(responses: list[list[Any]], method: str) -> dict[str, Any]:
    for item in responses:
        if len(item) >= 2 and item[0] == method and isinstance(item[1], dict):
            return item[1]
    raise RuntimeError(f"Stalwart did not return {method}")


def bootstrap_if_needed(recovery: str, env: dict[str, str]) -> None:
    try:
        body = response_body(request(recovery, [["x:Bootstrap/get", {"ids": ["singleton"]}, "bootstrap"]]), "x:Bootstrap/get")
    except RuntimeError:
        # A configured server no longer exposes the bootstrap singleton.
        return
    if not body.get("list"):
        return
    domain = env.get("AGENT_MAIL_DOMAIN", "agents.aiat.local")
    hostname = env.get("MAIL_HOSTNAME", "mail.localhost")
    request(
        recovery,
        [[
            "x:Bootstrap/set",
            {"update": {"singleton": {
                "serverHostname": hostname,
                "defaultDomain": domain,
                "requestTlsCertificate": False,
                "generateDkimKeys": False,
                "dnsServer": {"@type": "Manual"},
            }}},
            "bootstrap-set",
        ]],
    )
    raise SystemExit("Bootstrap settings applied; restart stalwart, then run this script again.")


def ensure_domain(recovery: str, domain: str) -> str:
    query = response_body(request(recovery, [["x:Domain/query", {"filter": {"name": domain}, "limit": 2}, "domain-query"]]), "x:Domain/query")
    ids = query.get("ids") or []
    if ids:
        return str(ids[0])
    value = {
        "name": domain,
        "aliases": {},
        "certificateManagement": {"@type": "Manual"},
        "dkimManagement": {"@type": "Manual"},
        "dnsManagement": {"@type": "Manual"},
        "subAddressing": {"@type": "Enabled"},
    }
    created = response_body(request(recovery, [["x:Domain/set", {"create": {"local-domain": value}}, "domain-create"]]), "x:Domain/set")
    item = (created.get("created") or {}).get("local-domain") or {}
    if not item.get("id"):
        raise RuntimeError("Stalwart did not return the local mail domain id")
    return str(item["id"])


def ensure_service_account(recovery: str, domain_id: str, env: dict[str, str]) -> tuple[str, str]:
    password = env.get("STALWART_LOCAL_SERVICE_PASSWORD", "")
    password_needs_update = not password or password.startswith("generated-") or "change-me" in password.lower()
    if password_needs_update:
        password = secrets.token_urlsafe(32)
    query = response_body(
        request(recovery, [["x:Account/query", {"filter": {"name": "aiat-service", "domainId": domain_id}, "limit": 2}, "service-query"]]),
        "x:Account/query",
    )
    ids = query.get("ids") or []
    account_id = str(ids[0]) if ids else ""
    account = {
        "@type": "User",
        "name": "aiat-service",
        "domainId": domain_id,
        "credentials": {"0": {"@type": "Password", "secret": password}},
        "memberGroupIds": {},
        "roles": {"@type": "Admin"},
        "permissions": {"@type": "Inherit"},
        "quotas": {},
        "aliases": {},
        "encryptionAtRest": {"@type": "Disabled"},
        "description": "AIAT local JMAP service",
    }
    if account_id:
        details = response_body(
            request(recovery, [["x:Account/get", {"ids": [account_id]}, "service-get"]]),
            "x:Account/get",
        )
        existing = (details.get("list") or [{}])[0]
        existing_role = (existing.get("roles") or {}).get("@type") if isinstance(existing, dict) else None
        existing_permissions = (existing.get("permissions") or {}) if isinstance(existing, dict) else {}
        already_configured = (
            not password_needs_update
            and existing_role == "Admin"
            and existing_permissions.get("@type") == "Inherit"
            and existing.get("description") == account["description"]
        )
        if not already_configured:
            request(recovery, [["x:Account/set", {"update": {account_id: {"credentials": account["credentials"], "roles": account["roles"], "permissions": account["permissions"], "description": account["description"]}}}, "service-update"]])
    else:
        created = response_body(request(recovery, [["x:Account/set", {"create": {"local-service": account}}, "service-create"]]), "x:Account/set")
        item = (created.get("created") or {}).get("local-service") or {}
        account_id = str(item.get("id") or "")
    if not account_id:
        raise RuntimeError("Stalwart did not return the local JMAP service account id")
    return account_id, password


def ensure_management_key(service_credential: str, env: dict[str, str]) -> str:
    current = env.get("STALWART_API_KEY", "")
    permissions = {
        name: True
        for name in (
            "authenticate", "sysJmapGet",
            "sysDomainQuery", "sysDomainCreate", "sysDomainUpdate",
            "sysAccountQuery", "sysAccountGet", "sysAccountCreate", "sysAccountUpdate",
            *WORKER_PERMISSION_NAMES,
        )
    }
    query = response_body(request(service_credential, [["x:ApiKey/query", {"limit": 100}, "api-key-query"]]), "x:ApiKey/query")
    ids = query.get("ids") or []
    details: dict[str, Any] = {}
    if ids:
        details = response_body(request(service_credential, [["x:ApiKey/get", {"ids": ids}, "api-key-get"]]), "x:ApiKey/get")
    managed = [item for item in details.get("list") or [] if item.get("description") == "AIAT local identity management" and item.get("id")]
    if current and not current.startswith("generated-"):
        try:
            response_body(request(f"Bearer {current}", [["x:Domain/query", {"filter": {"name": env.get("AGENT_MAIL_DOMAIN", "agents.aiat.local")}, "limit": 1}, "health"]]), "x:Domain/query")
            if managed:
                existing = managed[0].get("permissions") or {}
                existing_permissions = existing.get("permissions") if isinstance(existing, dict) else {}
                if isinstance(existing_permissions, dict) and all(existing_permissions.get(name) is True for name in permissions):
                    return current
                request(service_credential, [["x:ApiKey/set", {"update": {str(managed[0]["id"]): {"permissions": {"@type": "Replace", "permissions": permissions}}}}, "api-key-update"]])
            return current
        except RuntimeError:
            pass
    stale = [str(item["id"]) for item in managed]
    if stale:
        request(service_credential, [["x:ApiKey/set", {"destroy": stale}, "api-key-destroy"]])
    created = response_body(
        request(service_credential, [["x:ApiKey/set", {"create": {"identity-management": {
            "description": "AIAT local identity management",
            "allowedIps": {},
            "permissions": {"@type": "Replace", "permissions": permissions},
        }}}, "api-key-create"]]),
        "x:ApiKey/set",
    )
    item = (created.get("created") or {}).get("identity-management") or {}
    secret = str(item.get("secret") or "")
    if not secret:
        raise RuntimeError("Stalwart did not return the local management key secret")
    return secret


def generate_identity_clients(env: dict[str, str]) -> dict[str, str]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        public = json.loads(env.get("IDENTITY_CLIENT_PUBLIC_KEYS_JSON", "{}"))
        scopes = json.loads(env.get("IDENTITY_CLIENT_SCOPES_JSON", "{}"))
        private_values = [env.get("AIAT_IDENTITY_CLIENT_PRIVATE_KEY", ""), env.get("AIAT_IDENTITY_TOOL_PRIVATE_KEY", "")]
        valid_private = all(len(base64.b64decode(value, validate=True)) == 32 for value in private_values)
        valid_clients = isinstance(public, dict) and set(public) == {"operator-laptop", "tool-service"} and isinstance(scopes, dict)
        valid_public = valid_clients and all(len(base64.b64decode(public[key], validate=True)) == 32 for key in public)
        if valid_private and valid_public and env.get("IDENTITY_CONTENT_ENCRYPTION_KEY") and env.get("IDENTITY_SERVICE_SECRET"):
            return {}
    except (ValueError, TypeError, json.JSONDecodeError):
        pass

    values: dict[str, str] = {}
    public: dict[str, str] = {}
    scopes = {
        "operator-laptop": ["identity:admin", "identity:delegate", "identity:browser-broker"],
        "tool-service": ["identity:delegate", "identity:browser-broker"],
    }
    for client_id, private_name in (("operator-laptop", "AIAT_IDENTITY_CLIENT_PRIVATE_KEY"), ("tool-service", "AIAT_IDENTITY_TOOL_PRIVATE_KEY")):
        private = Ed25519PrivateKey.generate()
        private_raw = private.private_bytes_raw()
        public_raw = private.public_key().public_bytes_raw()
        values[private_name] = base64.b64encode(private_raw).decode("ascii")
        public[client_id] = base64.b64encode(public_raw).decode("ascii")
    values["IDENTITY_CLIENT_PUBLIC_KEYS_JSON"] = json.dumps(public, separators=(",", ":"))
    values["IDENTITY_CLIENT_SCOPES_JSON"] = json.dumps(scopes, separators=(",", ":"))
    values["IDENTITY_CONTENT_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    values["IDENTITY_SERVICE_SECRET"] = secrets.token_urlsafe(32)
    values["IDENTITY_DATABASE_PASSWORD"] = env.get("IDENTITY_DATABASE_PASSWORD", "") or secrets.token_urlsafe(24)
    return values


def main() -> int:
    root_env = read_env(ROOT_ENV)
    local_env = read_env(LOCAL_ENV)
    if not root_env.get("STALWART_RECOVERY_ADMIN"):
        raise SystemExit("STALWART_RECOVERY_ADMIN must be present in the ignored root .env for local bootstrap")
    if not LOCAL_ENV.exists():
        example = COMPOSE_DIR / "stalwart-local.env.example"
        LOCAL_ENV.write_text(example.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        local_env = read_env(LOCAL_ENV)
    bootstrap_if_needed(root_env["STALWART_RECOVERY_ADMIN"], local_env)
    domain = local_env.get("AGENT_MAIL_DOMAIN", "agents.aiat.local")
    domain_id = ensure_domain(root_env["STALWART_RECOVERY_ADMIN"], domain)
    _account_id, service_password = ensure_service_account(root_env["STALWART_RECOVERY_ADMIN"], domain_id, local_env)
    service_credential = f"aiat-service@{domain}:{service_password}"
    management_key = ensure_management_key(service_credential, local_env)
    generated = generate_identity_clients(local_env)
    generated.update({
        "STALWART_LOCAL_SERVICE_PASSWORD": service_password,
        "STALWART_API_KEY": management_key,
        "STALWART_JMAP_SERVICE_TOKEN": "Basic " + base64.b64encode(service_credential.encode("utf-8")).decode("ascii"),
    })
    write_env(generated)
    print(json.dumps({
        "ok": True,
        "env_file": str(LOCAL_ENV),
        "domain": domain,
        "management_key": "configured",
        "jmap_service_credential": "configured",
        "secret_refs": {
            name: "configured"
            for name in (
                "IDENTITY_DATABASE_PASSWORD",
                "IDENTITY_SERVICE_SECRET",
                "IDENTITY_CONTENT_ENCRYPTION_KEY",
                "IDENTITY_CLIENT_PUBLIC_KEYS_JSON",
                "IDENTITY_CLIENT_SCOPES_JSON",
                "STALWART_API_KEY",
                "STALWART_JMAP_SERVICE_TOKEN",
                "AIAT_IDENTITY_CLIENT_PRIVATE_KEY",
                "AIAT_IDENTITY_TOOL_PRIVATE_KEY",
            )
        },
        "identity_clients": ["operator-laptop", "tool-service"],
        "secrets_printed": False,
    }))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
