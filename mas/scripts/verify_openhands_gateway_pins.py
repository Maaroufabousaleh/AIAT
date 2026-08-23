"""Verify immutable gateway image identity and record scalar provenance."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = "aiat.openhands-certification-gateway-pins.v1"
LITELLM = {
    "component": "litellm",
    "version": "1.90.0",
    "source": "https://github.com/BerriAI/litellm",
    "source_commit": "6e8282d40655d47ed1557f030e53d6819e464e79",
    "image": "ghcr.io/berriai/litellm@sha256:a50b02a6056095da29308310bb608f0509e08ddcd1d105bae9c21007d82b0e95",
    "image_digest": "sha256:a50b02a6056095da29308310bb608f0509e08ddcd1d105bae9c21007d82b0e95",
}
OMNIROUTE = {
    "component": "omniroute",
    "version": "3.8.38",
    "source": "https://github.com/diegosouzapw/OmniRoute",
    "source_commit": "7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8",
    "image": "diegosouzapw/omniroute@sha256:ceae8d9da0acf075dbf5905b61c9ae32e749112650fcf7f4434c8d96ac6d3ebb",
    "image_digest": "sha256:ceae8d9da0acf075dbf5905b61c9ae32e749112650fcf7f4434c8d96ac6d3ebb",
}


class PinVerificationError(RuntimeError):
    """A gateway image is not the exact immutable candidate."""


def _inspect(image: str, runner: Any = subprocess.run) -> dict[str, Any]:
    result = runner(
        ["docker", "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        raise PinVerificationError("docker_image_inspect_invalid")
    return value[0]


def verify(*, runner: Any = subprocess.run) -> dict[str, Any]:
    components = []
    for expected in (LITELLM, OMNIROUTE):
        inspected = _inspect(expected["image"], runner)
        repo_digests = inspected.get("RepoDigests") or []
        if expected["image"] not in repo_digests:
            raise PinVerificationError(f"{expected['component']}_digest_mismatch")
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        image_os = str(inspected.get("Os") or "")
        image_architecture = str(inspected.get("Architecture") or "")
        if image_os != "linux" or image_architecture != "amd64":
            raise PinVerificationError(f"{expected['component']}_platform_not_linux_amd64")
        revision_label = labels.get("org.opencontainers.image.revision")
        components.append(
            {
                **expected,
                "repo_digest_verified": True,
                "image_os": image_os,
                "image_architecture": image_architecture,
                "image_platform": f"{image_os}/{image_architecture}",
                "image_platform_verified": True,
                "image_revision_label": revision_label,
                "source_revision_label_matches": revision_label == expected["source_commit"],
                "source_revision_attestation": (
                    "image-label"
                    if revision_label == expected["source_commit"]
                    else "exact-release-tag-dereference; image-label-absent-or-different"
                ),
            }
        )
    return {
        "schema_version": SCHEMA,
        "status": "PASS",
        "components": components,
        "floating_tags_used": False,
        "local_cache_only": False,
        "sbom_or_scan_not_claimed": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify()
    except (PinVerificationError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        report = {"schema_version": SCHEMA, "status": "BLOCKED", "failure": str(exc)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
