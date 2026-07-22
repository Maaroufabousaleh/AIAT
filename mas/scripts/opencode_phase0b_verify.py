"""Live Phase 0B contract discovery for the pinned OpenCode runtime.

This script is intentionally evidence-first: it uses native Basic Auth, reads
the machine-readable ``/doc`` contract exposed by OpenCode 1.17.13, derives
the endpoint manifest from operation IDs, and never writes credentials or
request contents to the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ENDPOINT_OPERATIONS = {
    "health": "global.health",
    "openapi": "",
    "project_current": "project.current",
    "session_create": "session.create",
    "session_list": "session.list",
    "session_get": "session.get",
    "session_delete": "session.delete",
    "session_status": "session.status",
    "messages": "session.messages",
    "prompt": "session.prompt",
    "prompt_async": "session.prompt_async",
    "events": "global.event",
    "abort": "session.abort",
    "diff": "session.diff",
    "file_content": "file.read",
    "file_status": "file.status",
    "vcs_diff": "vcs.diff",
    "vcs_status": "vcs.status",
    "permission_reply": "permission.respond",
    "mcp_add": "mcp.add",
    "mcp_status": "mcp.status",
}
PINNED_BINARY_SHA256 = "f8c45bae73a8f1e2088023fdd34dc2fe0a7f93f505f073e0703e4e1a19afe8ff"
EXPECTED_MIGRATION_HEAD = "0019_block_universal_contract"
REQUIRED_LIVE_GATES = {
    "services_healthy",
    "migration_head",
    "model_governance",
    "unsafe_worker_count",
    "binary_pin",
    "image_pin",
    "openapi_pin",
    "authentication",
    "allowed_tool",
    "denied_tool",
    "bridge_boundary",
    "native_tool_bypass",
    "coding",
    "containment",
    "cleanup",
    "event_evidence",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize(value: Any, key: str = "") -> Any:
    sensitive = re.compile(r"(?:password|secret|token|authorization|cookie|api[_-]?key|private[_-]?key)", re.I)
    if sensitive.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, key) for item in value]
    return value


def _operation_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() not in {"get", "post", "patch", "delete", "put"} or not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "")
            if operation_id:
                result[operation_id] = {"path": path, "method": method.upper(), "operationId": operation_id}
    return result


def _derive_manifest(document: dict[str, Any]) -> dict[str, Any]:
    operations = _operation_map(document)
    manifest: dict[str, Any] = {}
    for name, operation_id in ENDPOINT_OPERATIONS.items():
        if name == "openapi":
            manifest[name] = {"path": "/doc", "method": "GET", "operationId": None, "source": "runtime-documentation-route"}
            continue
        if operation_id not in operations:
            raise RuntimeError(f"certified OpenCode contract is missing operation {operation_id!r}")
        manifest[name] = operations[operation_id]
    return manifest


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validated_live_evidence(output: Path, *, binary_sha256: str, openapi_sha256: str, image_digest: str) -> dict[str, Any]:
    path = output / "live-certification-evidence.json"
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise SystemExit("genuine live-certification evidence is required before verification approval") from exc
    gates = evidence.get("gates") if isinstance(evidence, dict) else None
    failed = sorted(name for name in REQUIRED_LIVE_GATES if not isinstance(gates, dict) or gates.get(name) is not True)
    if failed:
        raise SystemExit(f"mandatory live-certification gates did not pass: {', '.join(failed)}")
    migration = evidence.get("migration") or {}
    database = evidence.get("database") or {}
    pins = evidence.get("pins") or {}
    authentication = evidence.get("authentication") or {}
    required_facts = {
        "migration heads": migration.get("source_head") == migration.get("live_database_head") == EXPECTED_MIGRATION_HEAD,
        "approved model profile": database.get("model_status") == "approved"
        and database.get("model_version_status") == "approved"
        and database.get("model_exact_id") == "aiat/omniroute-coding",
        "unsafe worker count": database.get("unsafe_active_external_workers") == 0,
        "binary pin": pins.get("binary_sha256") == binary_sha256,
        "running image pin": pins.get("running_image_digest") == image_digest == pins.get("tagged_image_digest"),
        "OpenAPI pin": pins.get("openapi_sha256") == openapi_sha256,
        "authentication": authentication == {
            "unauthenticated_health": 401,
            "invalid_auth_health": 401,
            "authenticated_health": 200,
            "authenticated_openapi": 200,
        },
        "sanitized evidence": evidence.get("sensitive_values_persisted") is False
        and evidence.get("workspace_contents_persisted") is False,
    }
    invalid = sorted(name for name, passed in required_facts.items() if not passed)
    if invalid:
        raise SystemExit(f"live-certification evidence facts do not match the pinned runtime: {', '.join(invalid)}")
    fixtures = evidence.get("fixtures") if isinstance(evidence, dict) else None
    required_fixture_paths = {
        "event-fixtures/live-event-summary.json",
        "request-response-fixtures/live-interface-summary.json",
    }
    fixture_hashes = fixtures.get("sha256") if isinstance(fixtures, dict) else None
    if not isinstance(fixtures, dict) or set(fixtures.get("refs") or ()) != required_fixture_paths or not isinstance(fixture_hashes, dict):
        raise SystemExit("live-certification evidence is missing the required sanitized fixture set")
    for relative_path in sorted(required_fixture_paths):
        path = output / relative_path
        expected_hash = fixture_hashes.get(relative_path)
        if not path.is_file() or not isinstance(expected_hash, str) or _sha256_file(path) != expected_hash:
            raise SystemExit(f"live-certification fixture integrity failed: {relative_path}")
    encoded = json.dumps(evidence, sort_keys=True)
    forbidden = [
        r"(?i)authorization",
        r"(?i)bearer\s+",
        r"(?i)basic\s+[a-z0-9+/=]{8,}",
        r"(?i)(?:password|secret|token|api[_-]?key)\s*[=:]",
        r"X-AIAT-OpenCode-Grant",
        r"def\s+add\(",
    ]
    if any(re.search(pattern, encoded) for pattern in forbidden):
        raise SystemExit("live-certification evidence failed the secret/workspace sanitization scan")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:8091"))
    parser.add_argument("--output", type=Path, default=Path("mas/docs/opencode/phase0b/1.17.13"))
    parser.add_argument("--release", default="1.17.13")
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--binary")
    parser.add_argument(
        "--binary-sha256",
        help="SHA-256 measured from the already-running pinned binary when its host path is unavailable.",
    )
    parser.add_argument("--expected-binary-sha256", default=os.getenv("OPENCODE_BINARY_SHA256", ""))
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--container-image-digest", default=os.getenv("OPENCODE_CONTAINER_IMAGE_DIGEST", ""))
    args = parser.parse_args()
    if not args.binary and not args.binary_sha256:
        raise SystemExit("either --binary or --binary-sha256 is required")
    if args.binary:
        binary_path = Path(args.binary)
        if not binary_path.is_file():
            raise SystemExit("the pinned OpenCode binary path does not exist")
        binary_sha256 = _sha256_file(binary_path)
        if args.binary_sha256 and binary_sha256.lower() != args.binary_sha256.lower():
            raise SystemExit("the supplied OpenCode binary hash does not match the binary path")
    else:
        binary_sha256 = str(args.binary_sha256).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", binary_sha256):
            raise SystemExit("--binary-sha256 must be a SHA-256 hex digest")
    expected_binary_sha256 = args.expected_binary_sha256 or (PINNED_BINARY_SHA256 if args.release == "1.17.13" else "")
    if expected_binary_sha256 and binary_sha256.lower() != expected_binary_sha256.lower():
        raise SystemExit("the OpenCode binary hash does not match the pinned Phase 0B hash")
    username = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
    password = os.getenv("OPENCODE_SERVER_PASSWORD")
    if not password:
        raise SystemExit("OPENCODE_SERVER_PASSWORD must be supplied through the environment")
    auth = httpx.BasicAuth(username, password)
    base = args.base_url.rstrip("/")
    with httpx.Client(base_url=base, auth=auth, timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False) as client:
        health = client.get("/global/health")
        if health.status_code != 200:
            raise SystemExit(f"authenticated health failed: {health.status_code}")
        document_response = client.get("/doc")
        document_response.raise_for_status()
        document = document_response.json()
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise SystemExit("OpenCode /doc did not return an OpenAPI document")
    sanitized = _sanitize(document)
    canonical = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode()
    openapi_sha256 = _sha256_bytes(canonical)
    manifest = _derive_manifest(sanitized)
    live_evidence = _validated_live_evidence(
        args.output,
        binary_sha256=binary_sha256,
        openapi_sha256=openapi_sha256,
        image_digest=args.container_image_digest,
    )
    evidence_sha256 = _sha256_bytes(
        json.dumps(live_evidence, sort_keys=True, separators=(",", ":")).encode()
    )
    approval_status = "APPROVED"
    approval_record_id = f"phase0b-live-{evidence_sha256[:20]}"
    capabilities = {
        "session": {"status": "certified_interface", "operations": ["session_create", "session_get", "session_delete", "messages", "prompt_async"]},
        "events": {"status": "certified_interface", "mode": "sse", "operation": "events"},
        "cancellation": {"status": "certified_interface", "mode": "cooperative", "operation": "abort"},
        "diff_artifacts": {"status": "certified_interface", "operations": ["diff", "file_content", "vcs_diff"]},
        "checkpoint": {"status": "certified", "mode": "restart_only"},
        "native_permissions": {"status": "certified", "mode": "deny_native_allow_governed_mcp", "operation": "permission_reply"},
        "model_governance": {"status": "certified", "mode": "aiat_gateway"},
        "tool_mediation": {"status": "certified", "mode": "aiat_mediated"},
        "coding_task": {"status": "certified", "test_count": live_evidence["coding"]["pytest_passed"]},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    _write_json(args.output / "openapi.sanitized.json", sanitized)
    _write_json(args.output / "endpoint-manifest.json", {"release": args.release, "openapi_sha256": openapi_sha256, "endpoints": manifest})
    _write_json(args.output / "capability-matrix.json", capabilities)
    provenance = {
        "release": args.release,
        "package": "opencode-ai",
        "binary": args.binary or "runtime-verified-binary",
        "commit_sha": args.commit_sha,
        "binary_sha256": binary_sha256,
        "config_sha256": args.config_sha256,
        "os": platform.platform(),
        "architecture": platform.machine(),
        "auth_mode": "native_basic_auth",
        "launch_command": [args.binary or "runtime-verified-binary", "serve", "--hostname", "127.0.0.1", "--port", "8091"],
        "working_directory": "disposable_phase0b_workspace",
        "configuration": {
            "hostname": "127.0.0.1",
            "port": 8091,
            "username_env": "OPENCODE_SERVER_USERNAME",
            "password_env": "OPENCODE_SERVER_PASSWORD",
        },
        "credential_recorded": False,
    }
    if args.container_image_digest:
        provenance["container_image"] = "mas/opencode-runtime:1.17.13"
        provenance["container_image_digest"] = args.container_image_digest
    _write_json(args.output / "binary-provenance.json", provenance)
    report = {
        "report_id": "opencode-phase0b-1.17.13",
        "report_version": "2",
        "release": args.release,
        "commit_sha": args.commit_sha,
        "approved": True,
        "approval_status": approval_status,
        "approval_record_id": approval_record_id,
        "auth_mode": "aiat_gateway",
        "openapi_sha256": openapi_sha256,
        "config_schema_sha256": args.config_sha256,
        "checkpoint_mode": "restart_only",
        "cancellation_mode": "cooperative",
        "streaming_mode": "event_stream",
        "supported_model_pattern": r"^[^/\\s]+/[^/\\s]+$",
        "endpoints": manifest,
        "evidence": {
            "discovery_route": "/doc",
            "health_route": "/global/health",
            "captured_at": datetime.now(UTC).isoformat(),
            "credentials_written": False,
            "capability_matrix": "capability-matrix.json",
            "binary_provenance": "binary-provenance.json",
            "live_evidence_sha256": evidence_sha256,
            "all_mandatory_live_gates_passed": True,
            "endpoint_paths_derived_from_live_openapi": True,
            "endpoint_override_allowed": False,
        },
        "fixture_refs": [
            "openapi.sanitized.json",
            "endpoint-manifest.json",
            "capability-matrix.json",
            "binary-provenance.json",
            "event-fixtures/live-event-summary.json",
            "request-response-fixtures/live-interface-summary.json",
        ],
        "fixture_sha256": dict((live_evidence.get("fixtures") or {}).get("sha256") or {}),
    }
    if (args.output / "live-certification-evidence.json").exists():
        report["evidence"]["live_certification"] = "live-certification-evidence.json"
        report["fixture_refs"].append("live-certification-evidence.json")
    _write_json(args.output / "interface-verification-report.json", report)
    markdown = "# OpenCode Phase 0B 1.17.13\n\n" + f"Status: **{approval_status}**\n\n" + f"OpenAPI SHA-256: `{openapi_sha256}`\n\n" + "The schema was obtained from the authenticated `/doc` route. Approval is derived only from the complete sanitized live evidence; credentials and request payloads are not persisted.\n"
    (args.output / "interface-verification-report.md").write_text(markdown, encoding="utf-8")
    checksums = []
    for path in sorted(args.output.rglob("*")):
        if path.name == "checksums.sha256" or not path.is_file():
            continue
        relative = path.relative_to(args.output).as_posix()
        checksums.append(f"{_sha256_bytes(path.read_bytes())}  {relative}")
    (args.output / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(json.dumps({"approval_status": approval_status, "openapi_sha256": openapi_sha256, "endpoint_count": len(manifest), "live_gate_count": len(REQUIRED_LIVE_GATES), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except httpx.HTTPError as exc:
        raise SystemExit(f"OpenCode Phase 0B HTTP failure: {type(exc).__name__}") from exc
