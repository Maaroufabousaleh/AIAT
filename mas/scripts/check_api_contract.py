"""Export and verify deterministic AIAT API/protocol contract artifacts.

The orchestrator's OpenAPI document is generated from the running FastAPI
application, canonicalized, and checked against the committed JSON artifact
and its provenance record.  The existing protocol JSON Schema is checked in
the same invocation so API and worker-message contracts cannot drift silently.

``--write`` is intentionally an explicit developer/release action.  CI runs
the default verification mode and fails when the generated contract changes
without a deliberate artifact/provenance update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

MAS_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = MAS_ROOT / "schemas" / "http" / "orchestrator.openapi.json"
PROVENANCE_PATH = MAS_ROOT / "docs" / "provenance" / "api_contract.yaml"
PROTOCOL_SCHEMA_PATH = MAS_ROOT / "packages" / "mas-core" / "schemas" / "protocol" / "aiat.v1.schema.json"
TYPESCRIPT_PATH = MAS_ROOT / "apps" / "mas-dashboard" / "lib" / "generated" / "orchestrator-api.ts"
PYTHON_SDK_PATH = MAS_ROOT / "packages" / "mas-api-sdk" / "mas_api_sdk" / "generated.py"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "utf-8"
    )


def _openapi_schema() -> dict[str, Any]:
    from orchestrator_api.main import app

    schema = app.openapi()
    if not isinstance(schema, dict):
        raise TypeError("orchestrator OpenAPI export must be a JSON object")
    return schema


def _protocol_schema() -> dict[str, Any]:
    from mas_core.protocols import protocol_schema_bundle

    bundle = protocol_schema_bundle()
    if not isinstance(bundle, dict):
        raise TypeError("AIAT protocol schema bundle must be a JSON object")
    return bundle


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a mapping")
    return value


def _verify_artifact(path: Path, expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: unable to read JSON artifact: {exc}"]
    if not isinstance(artifact, dict):
        return [f"{path}: JSON artifact must be an object"]
    actual_hash = hashlib.sha256(_canonical_bytes(artifact)).hexdigest()
    if actual_hash != str(expected.get("sha256", "")):
        errors.append(f"{path}: sha256 mismatch (expected {expected.get('sha256')}, got {actual_hash})")
    expected_paths = int(expected.get("path_count", -1))
    actual_paths = len(artifact.get("paths") or {})
    if expected_paths != actual_paths:
        errors.append(f"{path}: path_count mismatch (expected {expected_paths}, got {actual_paths})")
    if artifact.get("openapi") != expected.get("openapi_version"):
        errors.append(
            f"{path}: OpenAPI version mismatch (expected {expected.get('openapi_version')}, got {artifact.get('openapi')})"
        )
    return errors


def verify(*, write: bool = False) -> tuple[dict[str, Any], list[str]]:
    openapi = _openapi_schema()
    protocol = _protocol_schema()
    openapi_hash = hashlib.sha256(_canonical_bytes(openapi)).hexdigest()
    protocol_hash = hashlib.sha256(_canonical_bytes(protocol)).hexdigest()

    if write:
        _write_json(OPENAPI_PATH, openapi)

    errors: list[str] = []
    try:
        provenance = _read_yaml(PROVENANCE_PATH)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        provenance = {}
        errors.append(f"API contract provenance could not be read: {exc}")

    if provenance.get("schema_version") != "1":
        errors.append("API contract provenance schema_version must be '1'")
    openapi_record = provenance.get("openapi") or {}
    protocol_record = provenance.get("protocol") or {}
    typescript_record = provenance.get("typescript") or {}
    python_record = provenance.get("python_sdk") or {}
    if (
        not isinstance(openapi_record, dict)
        or not isinstance(protocol_record, dict)
        or not isinstance(typescript_record, dict)
        or not isinstance(python_record, dict)
    ):
        errors.append(
            "API contract provenance must contain openapi, protocol, typescript, and python_sdk mappings"
        )
        openapi_record = openapi_record if isinstance(openapi_record, dict) else {}
        protocol_record = protocol_record if isinstance(protocol_record, dict) else {}
        typescript_record = typescript_record if isinstance(typescript_record, dict) else {}
        python_record = python_record if isinstance(python_record, dict) else {}

    errors.extend(_verify_artifact(OPENAPI_PATH, openapi_record))
    try:
        checked_openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checked_openapi = None
        errors.append(f"OpenAPI artifact could not be loaded for runtime comparison: {exc}")
    if checked_openapi is not None and checked_openapi != openapi:
        errors.append("checked-in OpenAPI artifact does not match runtime app.openapi()")
    if openapi_record.get("artifact") != str(OPENAPI_PATH.relative_to(MAS_ROOT)):
        errors.append("API contract provenance openapi.artifact does not match the committed artifact")

    protocol_text = PROTOCOL_SCHEMA_PATH.read_bytes() if PROTOCOL_SCHEMA_PATH.exists() else b""
    if not protocol_text:
        errors.append(f"missing protocol schema artifact: {PROTOCOL_SCHEMA_PATH}")
    else:
        try:
            checked_protocol = json.loads(protocol_text)
        except json.JSONDecodeError as exc:
            errors.append(f"protocol schema artifact is invalid JSON: {exc}")
        else:
            if checked_protocol != protocol:
                errors.append("checked-in protocol schema does not match runtime protocol_schema_bundle()")
            checked_hash = hashlib.sha256(_canonical_bytes(checked_protocol)).hexdigest()
            if checked_hash != str(protocol_record.get("sha256", "")):
                errors.append(
                    f"protocol schema sha256 mismatch (expected {protocol_record.get('sha256')}, got {checked_hash})"
                )
    if protocol_record.get("artifact") != str(PROTOCOL_SCHEMA_PATH.relative_to(MAS_ROOT.parent)):
        errors.append("API contract provenance protocol.artifact does not match the protocol schema path")
    if protocol_record.get("protocol_version") != protocol.get("protocol_version"):
        errors.append("API contract provenance protocol_version does not match runtime protocol schema")

    try:
        from generate_typescript_api import generate

        generated_typescript = generate(openapi)
        checked_typescript = TYPESCRIPT_PATH.read_text(encoding="utf-8")
    except (ImportError, OSError, TypeError, ValueError) as exc:
        generated_typescript = ""
        checked_typescript = ""
        errors.append(f"dashboard TypeScript contract could not be generated: {exc}")
    if generated_typescript != checked_typescript:
        errors.append("checked-in dashboard TypeScript contract does not match the OpenAPI artifact")
    typescript_hash = hashlib.sha256(checked_typescript.encode("utf-8")).hexdigest()
    if typescript_hash != str(typescript_record.get("sha256", "")):
        errors.append(
            f"dashboard TypeScript sha256 mismatch (expected {typescript_record.get('sha256')}, got {typescript_hash})"
        )
    if typescript_record.get("artifact") != str(TYPESCRIPT_PATH.relative_to(MAS_ROOT)):
        errors.append("API contract provenance typescript.artifact does not match the generated dashboard file")
    model_count = len((openapi.get("components") or {}).get("schemas") or {})
    operation_count = sum(
        1
        for item in (openapi.get("paths") or {}).values()
        for method in ("delete", "get", "head", "options", "patch", "post", "put", "trace")
        if isinstance(item, dict) and isinstance(item.get(method), dict)
    )
    if int(typescript_record.get("model_count", -1)) != model_count:
        errors.append("API contract provenance typescript.model_count does not match OpenAPI")
    if int(typescript_record.get("operation_count", -1)) != operation_count:
        errors.append("API contract provenance typescript.operation_count does not match OpenAPI")

    try:
        from generate_python_api import generate as generate_python

        generated_python = generate_python(openapi)
        checked_python = PYTHON_SDK_PATH.read_text(encoding="utf-8")
    except (ImportError, OSError, TypeError, ValueError) as exc:
        generated_python = ""
        checked_python = ""
        errors.append(f"Python SDK contract could not be generated: {exc}")
    if generated_python != checked_python:
        errors.append("checked-in Python SDK contract does not match the OpenAPI artifact")
    python_hash = hashlib.sha256(checked_python.encode("utf-8")).hexdigest()
    if python_hash != str(python_record.get("sha256", "")):
        errors.append(
            f"Python SDK sha256 mismatch (expected {python_record.get('sha256')}, got {python_hash})"
        )
    if python_record.get("artifact") != str(PYTHON_SDK_PATH.relative_to(MAS_ROOT)):
        errors.append("API contract provenance python_sdk.artifact does not match the generated SDK file")
    if int(python_record.get("model_count", -1)) != model_count:
        errors.append("API contract provenance python_sdk.model_count does not match OpenAPI")
    if int(python_record.get("operation_count", -1)) != operation_count:
        errors.append("API contract provenance python_sdk.operation_count does not match OpenAPI")

    report = {
        "openapi": {
            "artifact": str(OPENAPI_PATH.relative_to(MAS_ROOT)),
            "openapi_version": openapi.get("openapi"),
            "path_count": len(openapi.get("paths") or {}),
            "sha256": openapi_hash,
        },
        "protocol": {
            "artifact": str(PROTOCOL_SCHEMA_PATH.relative_to(MAS_ROOT.parent)),
            "protocol_version": protocol.get("protocol_version"),
            "sha256": protocol_hash,
        },
        "typescript": {
            "artifact": str(TYPESCRIPT_PATH.relative_to(MAS_ROOT)),
            "model_count": model_count,
            "operation_count": operation_count,
            "sha256": typescript_hash,
        },
        "python_sdk": {
            "artifact": str(PYTHON_SDK_PATH.relative_to(MAS_ROOT)),
            "model_count": model_count,
            "operation_count": operation_count,
            "sha256": python_hash,
        },
        "status": "fail" if errors else "pass",
    }
    return report, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the generated OpenAPI artifact")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    try:
        report, errors = verify(write=args.write)
    except (ImportError, OSError, TypeError, ValueError) as exc:
        print(f"api-contract: unable to generate contract: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({**report, "errors": errors}, sort_keys=True))
    else:
        print(f"api-contract: {report['status']}")
        print(
            f"api-contract: OpenAPI {report['openapi']['path_count']} paths, "
            f"sha256={report['openapi']['sha256']}"
        )
        print(
            f"api-contract: protocol {report['protocol']['protocol_version']}, "
            f"sha256={report['protocol']['sha256']}"
        )
        for error in errors:
            print(f"api-contract: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
