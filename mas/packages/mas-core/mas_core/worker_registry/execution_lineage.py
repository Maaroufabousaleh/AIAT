"""Secret-safe immutable execution configuration for governed worker runs.

Worker registry configuration is mutable control-plane state.  A certified
shell therefore carries the exact adapter configuration used for conformance
inside its immutable provenance snapshot.  Runtime secret material is never
stored here: adapters may name environment references, which are resolved only
when the runtime is constructed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

EXECUTION_CONFIG_SCHEMA = "aiat.worker-execution-config.v1"
SHELL_EXECUTION_LINEAGE_KEY = "runtime_execution_config"

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SECRET_FIELD = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"credential|authorization|cookie)",
    re.IGNORECASE,
)
_SENSITIVE_HEADER = re.compile(
    r"(?:authorization|cookie|password|secret|token|api[_-]?key|credential)",
    re.IGNORECASE,
)
_INLINE_SECRET_VALUE = re.compile(
    r"(?i)(?:\bbearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,}|"
    r"(?:api[_-]?key|password|passwd|secret|token)\s*[:=]\s*\S+)"
)
_SENSITIVE_ARGUMENT_OPTION = re.compile(
    r"(?i)^--?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|authorization|cookie)$"
)
_SENSITIVE_ARGUMENT_IN_COMMAND = re.compile(
    r"(?i)(?:^|\s)--?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|authorization|cookie)(?:\s|=|$)"
)
_OPAQUE_SECRET_REFERENCE = re.compile(r"^[A-Za-z0-9_./:-]+$")
_EMBEDDED_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")

_COMMON_CONFIG_KEYS = {
    "workspace_path",
    "artifact_workspace_path",
    "runtime_version",
    "environment_refs",
    "header_secret_refs",
}
_TRANSPORT_CONFIG_KEYS: dict[str, set[str]] = {
    "process": {
        "command", "entrypoint", "cwd", "bidirectional_tool_protocol",
        "tool_response_timeout_seconds",
    },
    "wasmtime": {
        "module_path", "module_sha256", "wasmtime_binary", "arguments", "cwd",
    },
    "http": {"base_url", "endpoints", "headers", "timeout_seconds"},
    "mcp": {"base_url", "endpoints", "headers", "timeout_seconds"},
    "oci": {
        "image", "sandbox_profile", "sandbox_runtime", "execution_policy",
        "launcher", "memory_limit", "cpu_limit", "pids_limit", "cwd",
        "bidirectional_tool_protocol", "tool_response_timeout_seconds",
        "legacy_direct_firecracker", "kernel_path", "kernel_sha256",
        "rootfs_path", "rootfs_sha256", "artifact_dir", "network_mode",
        "egress_allowlist", "vcpu_count", "memory_mib", "disk_limit_mb",
        "output_limit_bytes", "wall_clock_seconds", "secret_refs",
    },
    "aiat_gateway": {
        "gateway_url", "default_model", "backend", "timeout_s", "max_retries",
        "provider_id",
    },
    "openhands": {
        "interface_verification_ref", "base_url", "timeout_seconds",
    },
    "opencode": {
        "base_url", "endpoints", "interface_verification", "auth_password_env",
        "auth_username_env", "timeout_seconds",
    },
    "native": {"implementation_ref", "entrypoint", "call_style"},
    "langgraph": {"implementation_ref", "entrypoint", "call_style"},
    "crewai": {"implementation_ref", "entrypoint", "call_style"},
}


class WorkerExecutionLineageError(ValueError):
    """A fixed, value-free error for an invalid or unavailable pinned config."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical_transport(transport: Any) -> str:
    value = str(transport or "").strip().lower()
    return {
        "wasm": "wasmtime",
        "gateway": "aiat_gateway",
        "openhands_agent_server": "openhands",
    }.get(value, value)


def worker_execution_transport_supported(transport: Any) -> bool:
    """Return whether a transport has an explicit immutable-config schema."""

    return _canonical_transport(transport) in _TRANSPORT_CONFIG_KEYS


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise WorkerExecutionLineageError("worker_runtime_config_invalid") from exc


def _validate_env_refs(value: Any, *, reason_code: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise WorkerExecutionLineageError(reason_code)
    result: dict[str, str] = {}
    for target, source in value.items():
        if (
            not isinstance(target, str)
            or not _ENV_NAME.fullmatch(target)
            or not isinstance(source, str)
            or not _ENV_NAME.fullmatch(source)
        ):
            raise WorkerExecutionLineageError(reason_code)
        result[target] = source
    return dict(sorted(result.items()))


def _validate_header_secret_refs(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise WorkerExecutionLineageError("worker_runtime_config_reference_invalid")
    result: dict[str, str] = {}
    for header, source in value.items():
        if (
            not isinstance(header, str)
            or not _HEADER_NAME.fullmatch(header)
            or not isinstance(source, str)
            or not _ENV_NAME.fullmatch(source)
        ):
            raise WorkerExecutionLineageError("worker_runtime_config_reference_invalid")
        result[header] = source
    return dict(sorted(result.items()))


def _reject_secret_fields(value: Any, *, parent_key: str = "") -> None:
    if parent_key in {"environment_refs", "header_secret_refs", "secret_refs"}:
        # These map only opaque names to opaque reference IDs / environment
        # variable names. Their values are not runtime secret material.
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            if name in {"command", "entrypoint"}:
                if isinstance(item, str) and _SENSITIVE_ARGUMENT_IN_COMMAND.search(item):
                    raise WorkerExecutionLineageError(
                        "worker_runtime_config_inline_secret_rejected"
                    )
                if isinstance(item, (list, tuple)) and any(
                    isinstance(argument, str)
                    and _SENSITIVE_ARGUMENT_OPTION.fullmatch(argument.strip())
                    for argument in item
                ):
                    raise WorkerExecutionLineageError(
                        "worker_runtime_config_inline_secret_rejected"
                    )
            reference_only = (
                name.endswith(("_env", "_ref", "_refs"))
                or name in {"environment_refs", "header_secret_refs", "secret_refs"}
            )
            if _SECRET_FIELD.search(name) and not reference_only:
                raise WorkerExecutionLineageError("worker_runtime_config_inline_secret_rejected")
            if name == "headers" and isinstance(item, Mapping) and any(
                _SENSITIVE_HEADER.search(str(header)) for header in item
            ):
                raise WorkerExecutionLineageError("worker_runtime_config_inline_secret_rejected")
            _reject_secret_fields(item, parent_key=name)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_secret_fields(item, parent_key=parent_key)
        return
    if isinstance(value, str):
        if _INLINE_SECRET_VALUE.search(value):
            raise WorkerExecutionLineageError("worker_runtime_config_inline_secret_rejected")
        for endpoint in _EMBEDDED_URL.findall(value):
            _validate_endpoint(endpoint)


def _validate_endpoint(value: Any) -> None:
    if not isinstance(value, str):
        return
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise WorkerExecutionLineageError("worker_runtime_config_invalid") from exc
    if parsed.username is not None or parsed.password is not None:
        raise WorkerExecutionLineageError("worker_runtime_config_inline_secret_rejected")
    if any(_SECRET_FIELD.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise WorkerExecutionLineageError("worker_runtime_config_inline_secret_rejected")


def build_worker_execution_config_snapshot(
    transport: Any,
    adapter_config: Mapping[str, Any] | None,
    *,
    sandbox_profile: Any = None,
) -> dict[str, Any]:
    """Build the allowlisted config snapshot; refuse inline secret values.

    ``environment`` remains a supported adapter constructor input for legacy
    callers, but it is intentionally not pinnable because its values may be
    credentials. Governed snapshots use ``environment_refs`` instead.
    """

    normalized = _canonical_transport(transport)
    if normalized not in _TRANSPORT_CONFIG_KEYS:
        raise WorkerExecutionLineageError("worker_runtime_transport_unsupported")
    source = adapter_config if isinstance(adapter_config, Mapping) else {}
    if "environment" in source:
        raise WorkerExecutionLineageError("worker_runtime_config_inline_environment_rejected")
    allowed = _COMMON_CONFIG_KEYS | _TRANSPORT_CONFIG_KEYS[normalized]
    projected = {str(key): value for key, value in source.items() if str(key) in allowed}
    if "environment_refs" in projected:
        projected["environment_refs"] = _validate_env_refs(
            projected["environment_refs"],
            reason_code="worker_runtime_config_reference_invalid",
        )
    if "header_secret_refs" in projected:
        projected["header_secret_refs"] = _validate_header_secret_refs(
            projected["header_secret_refs"]
        )
    headers = projected.get("headers")
    if headers is not None:
        if not isinstance(headers, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in headers.items()
        ):
            raise WorkerExecutionLineageError("worker_runtime_config_invalid")
        projected["headers"] = dict(headers)
    secret_refs = projected.get("secret_refs")
    if secret_refs is not None and (
        not isinstance(secret_refs, (list, tuple))
        or any(
            not isinstance(reference, str)
            or not _OPAQUE_SECRET_REFERENCE.fullmatch(reference)
            for reference in secret_refs
        )
    ):
        raise WorkerExecutionLineageError("worker_runtime_config_reference_invalid")
    if secret_refs is not None:
        for reference in secret_refs:
            if _INLINE_SECRET_VALUE.search(reference):
                raise WorkerExecutionLineageError(
                    "worker_runtime_config_inline_secret_rejected"
                )
            for endpoint in _EMBEDDED_URL.findall(reference):
                _validate_endpoint(endpoint)
    for key in ("base_url", "gateway_url"):
        if key in projected:
            _validate_endpoint(projected[key])
    _reject_secret_fields(projected)
    config = _json_copy(projected)
    profile = str(sandbox_profile or source.get("sandbox_profile") or "").strip().lower() or None
    return {
        "schema": EXECUTION_CONFIG_SCHEMA,
        "transport": normalized,
        "sandbox_profile": profile,
        "config": config,
    }


def worker_execution_config_snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    """Hash only the sanitized snapshot, never a resolved runtime secret."""

    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_worker_execution_config_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping) or snapshot.get("schema") != EXECUTION_CONFIG_SCHEMA:
        raise WorkerExecutionLineageError("worker_runtime_lineage_unavailable")
    canonical = build_worker_execution_config_snapshot(
        snapshot.get("transport"),
        snapshot.get("config"),
        sandbox_profile=snapshot.get("sandbox_profile"),
    )
    if canonical != _json_copy(dict(snapshot)):
        raise WorkerExecutionLineageError("worker_runtime_lineage_integrity_invalid")
    return canonical


def resolve_worker_execution_config_snapshot(
    snapshot: Any,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Resolve named secret references in memory for one adapter instance.

    The returned config contains resolved values for adapter construction;
    the returned maps are runtime-only and must never be serialized.
    """

    checked = validate_worker_execution_config_snapshot(snapshot)
    env = environment if environment is not None else __import__("os").environ
    config = dict(checked["config"])
    resolved_values: dict[str, str] = {}
    refs: dict[str, str] = {}
    environment_refs = config.pop("environment_refs", {})
    if environment_refs:
        child_environment: dict[str, str] = {}
        for target, source in environment_refs.items():
            value = env.get(source)
            if not isinstance(value, str) or not value:
                raise WorkerExecutionLineageError("worker_runtime_secret_reference_unavailable")
            child_environment[target] = value
            resolved_values[f"environment:{target}"] = value
            refs[f"environment:{target}"] = source
        config["environment"] = child_environment
    header_refs = config.pop("header_secret_refs", {})
    if header_refs:
        headers = dict(config.get("headers") or {})
        normalized_headers = {str(key).lower() for key in headers}
        for header, source in header_refs.items():
            if header.lower() in normalized_headers:
                raise WorkerExecutionLineageError("worker_runtime_config_reference_invalid")
            value = env.get(source)
            if not isinstance(value, str) or not value:
                raise WorkerExecutionLineageError("worker_runtime_secret_reference_unavailable")
            headers[header] = value
            normalized_headers.add(header.lower())
            resolved_values[f"header:{header}"] = value
            refs[f"header:{header}"] = source
        config["headers"] = headers
    if checked.get("sandbox_profile") is not None:
        config["sandbox_profile"] = checked["sandbox_profile"]
    return config, resolved_values, refs


def worker_shell_content_hash(shell: Mapping[str, Any]) -> str:
    """Recompute the immutable shell hash from its persisted public fields."""

    payload = {
        "version": shell.get("version"),
        "identity": shell.get("identity_json") or {},
        "capabilities": shell.get("capabilities_json") or {},
        "permissions": shell.get("permissions_json") or {},
        "model_mode": shell.get("model_mode") or "none",
        "provenance": shell.get("provenance_json") or {},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def worker_shell_execution_lineage_blocker(
    *,
    shell: Mapping[str, Any] | None,
    adapter: Mapping[str, Any] | None,
    worker_id: Any,
    adapter_id: Any,
) -> str | None:
    """Return a fixed blocker when a shell does not authenticate its config pin."""

    if not isinstance(shell, Mapping) or not isinstance(adapter, Mapping):
        return "worker_runtime_lineage_unavailable"
    provenance = shell.get("provenance_json")
    if not isinstance(provenance, Mapping):
        return "worker_runtime_lineage_unavailable"
    adapter_pin = provenance.get("runtime_adapter")
    if not isinstance(adapter_pin, Mapping):
        return "worker_runtime_lineage_unavailable"
    binding = provenance.get(SHELL_EXECUTION_LINEAGE_KEY)
    if not isinstance(binding, Mapping):
        return "worker_runtime_lineage_unavailable"
    snapshot = binding.get("snapshot")
    try:
        checked = validate_worker_execution_config_snapshot(snapshot)
        snapshot_digest = worker_execution_config_snapshot_sha256(checked)
    except WorkerExecutionLineageError as exc:
        return exc.reason_code
    if (
        str(shell.get("worker_id") or "") != str(worker_id)
        or str(adapter.get("worker_id") or "") != str(worker_id)
        or str(provenance.get("runtime_adapter_id") or "") != str(adapter_id)
        or str(adapter_pin.get("id") or "") != str(adapter.get("id") or "")
        or str(adapter_pin.get("version") or "") != str(adapter.get("version") or "")
        or str(adapter_pin.get("transport_type") or "").lower()
        != str(adapter.get("transport_type") or "").lower()
        or adapter_pin.get("adapter_type") != adapter.get("adapter_type")
        or adapter_pin.get("runtime_api_version") != adapter.get("runtime_api_version")
        or adapter_pin.get("implementation_ref") != adapter.get("implementation_ref")
        or str(adapter_pin.get("content_hash") or "")
        != str(adapter.get("content_hash") or "")
        or checked.get("transport") != _canonical_transport(adapter.get("transport_type"))
    ):
        return "worker_runtime_lineage_identity_mismatch"
    if (
        str(binding.get("sha256") or "") != snapshot_digest
        or str(shell.get("content_hash") or "") != worker_shell_content_hash(shell)
    ):
        return "worker_runtime_lineage_integrity_invalid"
    return None
