"""Validate immutable production image inputs.

The production Compose file accepts application images only through required
``*_IMAGE_REF`` variables.  The value supplied to those variables must end in
an OCI ``@sha256:<64 hex>`` digest.  Fixed infrastructure images and every
Dockerfile ``FROM`` line are checked by the same rule.  Development overlays
are intentionally not scanned here; they may use convenience tags.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "infra" / "compose" / "docker-compose.yml"
DOCKER_DIR = Path(__file__).resolve().parents[1] / "infra" / "docker"
REDIS_INIT_DOCKERFILE = Path(__file__).resolve().parents[1] / "infra" / "compose" / "Dockerfile.redis-acl-init"
IMAGE_INVENTORY_PATH = Path(__file__).resolve().parents[1] / "docs" / "provenance" / "production_images.yaml"
DIGEST_RE = re.compile(r"@sha256:[0-9a-fA-F]{64}(?:$|[\s\"'])")
VARIABLE_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::[-?][^}]*)?\}")
DIGEST_VALUE_RE = re.compile(r"@(?P<digest>sha256:[0-9a-fA-F]{64})$")
SCHEMA_VERSION = "aiat.image-provenance.v1"


def _env_file(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _compose_variables() -> set[str]:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    return {
        variable
        for line in compose_text.splitlines()
        if re.match(r"^\s*image:\s*", line)
        for variable in VARIABLE_RE.findall(line.split("image:", 1)[1])
        if variable.endswith("_IMAGE_REF")
    }


def _load_inventory() -> dict[str, Any]:
    value = yaml.safe_load(IMAGE_INVENTORY_PATH.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def _check_compose(env: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for line_no, raw in enumerate(COMPOSE_PATH.read_text(encoding="utf-8").splitlines(), 1):
        if not re.match(r"^\s*image:\s*", raw):
            continue
        value = raw.split("image:", 1)[1].strip().strip('"').strip("'")
        variables = VARIABLE_RE.findall(value)
        if variables:
            # Production application images are deliberately required refs;
            # an optional variable with a mutable fallback is not acceptable.
            expressions = list(VARIABLE_RE.finditer(value))
            required_expression = expressions[0].group(0) if len(expressions) == 1 else ""
            if (
                len(variables) != 1
                or not variables[0].endswith("_IMAGE_REF")
                or ":?" not in required_expression
            ):
                errors.append(f"compose:{line_no}: image variable must be one required *_IMAGE_REF")
                continue
            resolved = env.get(variables[0])
            if resolved and not DIGEST_RE.search(resolved):
                errors.append(f"compose:{line_no}: {variables[0]} is not digest-pinned")
            continue
        if not DIGEST_RE.search(value):
            errors.append(f"compose:{line_no}: mutable image reference {value!r}")
    return errors


def _check_dockerfile(path: Path) -> list[str]:
    errors: list[str] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.lstrip().upper().startswith("FROM "):
            continue
        image = raw.split(None, 2)[1]
        if image.lower() == "scratch":
            continue
        if not DIGEST_RE.search(image):
            errors.append(f"{path.relative_to(COMPOSE_PATH.parents[1])}:{line_no}: FROM is not digest-pinned")
    return errors


def _check_inventory(compose_variables: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        inventory = yaml.safe_load(IMAGE_INVENTORY_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"image inventory could not be read: {exc}"]
    if inventory.get("image_identity") != "immutable-oci-digest":
        errors.append("production image inventory must declare immutable-oci-digest identity")
    rows = inventory.get("images") or []
    inventory_variables: set[str] = set()
    required_fields = {
        "id", "ref_env", "build_recipe", "source_revision", "lock_hash",
        "oci_digest", "sbom", "scan",
    }
    for row in rows:
        if not isinstance(row, dict) or not required_fields.issubset(row):
            errors.append("every production image row requires id, ref_env, recipe, provenance, SBOM, and scan fields")
            continue
        inventory_variables.add(str(row["ref_env"]))
    if inventory_variables != compose_variables:
        errors.append(
            "production image inventory ref_env values must match Compose image variables: "
            f"missing={sorted(compose_variables - inventory_variables)}, "
            f"extra={sorted(inventory_variables - compose_variables)}"
        )
    return errors


def inspect_static(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return the deterministic source/image contract report."""
    values = env or {}
    compose_variables = _compose_variables()
    errors = _check_compose(values)
    errors.extend(_check_inventory(compose_variables))
    for dockerfile in sorted(DOCKER_DIR.glob("Dockerfile*")):
        errors.extend(_check_dockerfile(dockerfile))
    errors.extend(_check_dockerfile(REDIS_INIT_DOCKERFILE))
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "static",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "compose_variables": sorted(compose_variables),
        "inventory": str(IMAGE_INVENTORY_PATH),
    }


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _docker_engine_available() -> bool:
    """Return whether the Docker CLI can reach an Engine without leaking output."""
    if shutil.which("docker") is None:
        return False
    try:
        result = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    except OSError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _digest_value(reference: str) -> str | None:
    match = DIGEST_VALUE_RE.search(reference.strip())
    return match.group("digest").lower() if match else None


def _inspect_image_digests(reference: str) -> tuple[list[str], str | None]:
    """Inspect one local image; return normalized RepoDigests and safe error text."""
    try:
        result = _run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                reference,
            ]
        )
    except OSError as exc:
        return [], f"docker image inspect unavailable: {type(exc).__name__}"
    if result.returncode != 0:
        return [], "image is not available to the local Docker Engine"
    try:
        values = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return [], "docker image inspect returned invalid RepoDigests JSON"
    if not isinstance(values, list):
        return [], "docker image inspect returned no RepoDigests list"
    digests = sorted(
        {
            digest
            for value in values
            if isinstance(value, str)
            for digest in [_digest_value(value)]
            if digest is not None
        }
    )
    return digests, None


def _inventory_rows() -> list[dict[str, Any]]:
    try:
        inventory = _load_inventory()
    except (OSError, yaml.YAMLError):
        return []
    rows = inventory.get("images") or []
    return [row for row in rows if isinstance(row, dict)]


def _metadata_artifact_status(row: dict[str, Any], field: str) -> str | None:
    value = str(row.get(field, "")).strip()
    if not value or value.lower().startswith("pending"):
        return f"{field} metadata is pending-release-ledger"
    path = Path(value)
    if not path.is_absolute():
        path = IMAGE_INVENTORY_PATH.parent.parent / path
    if not path.is_file():
        return f"{field} artifact is not present at the declared path"
    return None


def inspect_live(
    env: dict[str, str],
    *,
    require_sbom: bool = False,
) -> dict[str, Any]:
    """Check only local immutable image identity; build/scan evidence stays separate."""
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "live",
        "scope": "local Docker RepoDigests identity only",
        "status": "blocked",
        "errors": [],
        "images": [],
        "sbom_status": "required" if require_sbom else "not_checked",
    }
    if not _docker_engine_available():
        base["reason"] = "Docker Engine is unavailable to the Docker CLI"
        return base

    errors: list[str] = []
    failures: list[str] = []
    rows = _inventory_rows()
    if not rows:
        base["reason"] = "production image inventory could not be loaded"
        return base
    for row in rows:
        image_id = str(row.get("id", "unknown"))
        ref_env = str(row.get("ref_env", ""))
        reference = env.get(ref_env, "").strip() if ref_env else ""
        expected_digest = _digest_value(reference)
        image_result: dict[str, Any] = {
            "id": image_id,
            "ref_env": ref_env,
            "status": "blocked",
            "expected_digest": expected_digest,
            "observed_digests": [],
        }
        if not reference:
            image_result["reason"] = "deployment-supplied immutable image ref is missing"
            errors.append(f"{image_id}: missing {ref_env} deployment image ref")
            base["images"].append(image_result)
            continue
        if expected_digest is None:
            image_result["reason"] = "deployment image ref is not an OCI sha256 digest"
            errors.append(f"{image_id}: {ref_env} is not digest-pinned")
            base["images"].append(image_result)
            continue
        observed, inspect_error = _inspect_image_digests(reference)
        image_result["observed_digests"] = observed
        if inspect_error:
            image_result["reason"] = inspect_error
            errors.append(f"{image_id}: {inspect_error}")
        elif expected_digest not in observed:
            image_result["status"] = "fail"
            image_result["reason"] = "local RepoDigests do not match deployment digest"
            failures.append(f"{image_id}: local RepoDigests do not match deployment digest")
        else:
            image_result["status"] = "pass"
        if require_sbom:
            for field in ("sbom", "scan"):
                metadata_error = _metadata_artifact_status(row, field)
                if metadata_error:
                    errors.append(f"{image_id}: {metadata_error}")
        base["images"].append(image_result)

    base["errors"] = errors + failures
    if failures:
        base["status"] = "fail"
        base["reason"] = "one or more local image identities failed"
    elif errors:
        base["status"] = "blocked"
        base["reason"] = "live image identity evidence is incomplete"
    else:
        base["status"] = "pass"
        base["reason"] = (
            "all deployment image refs match local RepoDigests; SBOM, scan, build, and "
            "clean-room evidence are not asserted"
        )
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Resolve and validate supplied *_IMAGE_REF values")
    parser.add_argument("--live", action="store_true", help="inspect local Docker image RepoDigests")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument(
        "--require-sbom",
        action="store_true",
        help="also require declared local SBOM and scan artifacts for every inventory row",
    )
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        env.update(_env_file(args.env_file))
    except OSError as exc:
        static = {
            "schema_version": SCHEMA_VERSION,
            "mode": "static",
            "status": "fail",
            "errors": [f"image lock file could not be read: {exc}"],
        }
        if args.json:
            print(json.dumps(static, sort_keys=True, indent=2))
        else:
            print(f"image-provenance: {static['errors'][0]}", file=sys.stderr)
        return 1

    static = inspect_static(env)
    report: dict[str, Any] = static
    if args.live:
        report = {**static, "live": inspect_live(env, require_sbom=args.require_sbom)}
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        if static["status"] == "fail":
            for error in static["errors"]:
                print(f"image-provenance: {error}", file=sys.stderr)
        else:
            print("image-provenance: production Compose and Dockerfiles are structurally immutable")
        if args.live:
            live = report["live"]
            status = str(live.get("status", "blocked")).upper()
            print(f"image-provenance: live local identity {status} — {live.get('reason', 'unknown reason')}")
    if static["status"] == "fail":
        return 1
    if args.live:
        live_status = report["live"].get("status")
        return 2 if live_status == "blocked" else (1 if live_status == "fail" else 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
