"""Verify the disposable OpenHands gateway's six scalar provenance checks.

The certification workflow used to run these checks as a sequence of shell
``test`` commands.  A failed assertion therefore discarded the successful
observations and did not say which provenance primitive failed.  This module
runs every check independently, retains only scalar observations, and exits
non-zero only after writing the complete diagnostic report.

Release tags are resolved from the exact tag refs requested from Git.  An
annotated tag has both a tag-object ref and a peeled commit ref; a lightweight
tag has only the direct commit ref.  Neither case follows a branch or a
moving ``HEAD``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

SCHEMA = "aiat.openhands-certification-gateway-version-verification.v2"
_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SCALAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")

LITELLM = {
    "component": "litellm",
    "version": "1.90.0",
    "repo": "https://github.com/BerriAI/litellm.git",
    "tag": "v1.90.0",
    "source_archive": "https://github.com/BerriAI/litellm/archive/refs/tags/v1.90.0.tar.gz",
    "source_archive_sha256": "3e6474f2d7f507b124158291e327f995886756573d90dc641c04d73afea45ede",
    "source_commit": "6e8282d40655d47ed1557f030e53d6819e464e79",
    "image": "ghcr.io/berriai/litellm@sha256:a50b02a6056095da29308310bb608f0509e08ddcd1d105bae9c21007d82b0e95",
}
OMNIROUTE = {
    "component": "omniroute",
    "version": "3.8.38",
    "repo": "https://github.com/diegosouzapw/OmniRoute.git",
    "tag": "v3.8.38",
    "source_archive": "https://github.com/diegosouzapw/OmniRoute/archive/refs/tags/v3.8.38.tar.gz",
    "source_archive_sha256": "e81fc85f47204ffe09cd283a56cfce92f109a6f13de7d3bef3f4057f7f43d2e6",
    "source_commit": "7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8",
    "image": "diegosouzapw/omniroute@sha256:ceae8d9da0acf075dbf5905b61c9ae32e749112650fcf7f4434c8d96ac6d3ebb",
}


class ProvenanceCheckError(RuntimeError):
    """A scalar provenance check could not be completed."""

    def __init__(self, failure_class: str, *, tag_type: str | None = None, target: str | None = None) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.tag_type = tag_type
        self.target = target


def _result_ok(result: Any) -> bool:
    return int(getattr(result, "returncode", 1)) == 0


def _stdout(result: Any) -> str:
    value = getattr(result, "stdout", "")
    return value if isinstance(value, str) else ""


def _safe_scalar(value: str) -> str:
    """Return a bounded scalar or an explicit non-payload marker."""

    value = value.strip()
    if _SCALAR_RE.fullmatch(value):
        return value
    return "UNPARSEABLE_OUTPUT"


def _parse_ls_remote(output: str, *, tag: str, expected_commit: str) -> tuple[str, str]:
    """Resolve a direct or peeled exact tag ref from ``git ls-remote`` output."""

    direct_ref = f"refs/tags/{tag}"
    peeled_ref = f"{direct_ref}^{{}}"
    refs: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise ProvenanceCheckError("RELEASE_TAG_MALFORMED_RESPONSE")
        target, ref = fields
        if ref not in {direct_ref, peeled_ref} or not _SHA1_RE.fullmatch(target):
            raise ProvenanceCheckError("RELEASE_TAG_MALFORMED_RESPONSE")
        if ref in refs:
            raise ProvenanceCheckError("RELEASE_TAG_MALFORMED_RESPONSE")
        refs[ref] = target.lower()

    if direct_ref not in refs:
        raise ProvenanceCheckError("RELEASE_TAG_MISSING")
    if peeled_ref in refs:
        tag_type = "ANNOTATED"
        target = refs[peeled_ref]
    else:
        tag_type = "LIGHTWEIGHT"
        target = refs[direct_ref]
    if target != expected_commit.lower():
        raise ProvenanceCheckError("RELEASE_TAG_TARGET_MISMATCH", tag_type=tag_type, target=target)
    return tag_type, target


def resolve_release_tag(
    *,
    repo: str,
    tag: str,
    expected_commit: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Resolve one exact tag, accepting annotated and lightweight tags."""

    report: dict[str, Any] = {
        "repository": repo,
        "tag": tag,
        "expected_target": expected_commit,
        "tag_type": None,
        "target": None,
        "resolution_status": "FAILED",
        "failure_class": None,
        "response_payload_retained": False,
    }
    try:
        result = runner(
            ["git", "ls-remote", repo, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if not _result_ok(result):
            raise ProvenanceCheckError("RELEASE_TAG_LOOKUP_FAILED")
        tag_type, target = _parse_ls_remote(_stdout(result), tag=tag, expected_commit=expected_commit)
        report.update(tag_type=tag_type, target=target, resolution_status="PASS")
    except ProvenanceCheckError as exc:
        report.update(
            tag_type=exc.tag_type,
            target=exc.target,
            failure_class=exc.failure_class,
        )
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        report["failure_class"] = "RELEASE_TAG_LOOKUP_FAILED"
    return report


def _runtime_version(
    *,
    component: dict[str, str],
    runner: Callable[..., Any],
) -> dict[str, Any]:
    if component["component"] == "litellm":
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            component["image"],
            "-c",
            'import importlib.metadata; print(importlib.metadata.version("litellm"))',
        ]
        extraction = "importlib.metadata.version"
    else:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "node",
            component["image"],
            "-e",
            'console.log(require("/app/package.json").version)',
        ]
        extraction = "/app/package.json"
    result: dict[str, Any] = {
        "expected": component["version"],
        "observed": None,
        "status": "FAILED",
        "extraction": extraction,
        "failure_class": None,
        "payload_retained": False,
    }
    try:
        completed = runner(command, check=False, capture_output=True, text=True)
        observed = _safe_scalar(_stdout(completed))
        result["observed"] = observed
        if not _result_ok(completed):
            result["failure_class"] = f"{component['component'].upper()}_RUNTIME_VERSION_EXTRACTION_FAILED"
        elif observed != component["version"]:
            result["failure_class"] = f"{component['component'].upper()}_RUNTIME_VERSION_MISMATCH"
        else:
            result["status"] = "PASS"
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        result["failure_class"] = f"{component['component'].upper()}_RUNTIME_VERSION_EXTRACTION_FAILED"
    return result


def _hash_archive(
    url: str,
    *,
    runner: Callable[..., Any],
) -> tuple[str | None, str | None]:
    """Download to a disposable file and hash it without retaining payloads."""

    with tempfile.TemporaryDirectory(prefix="aiat-openhands-provenance-") as directory:
        archive = Path(directory) / "source.tar.gz"
        try:
            result = runner(
                [
                    "curl",
                    "-fsSL",
                    "--retry",
                    "3",
                    "--max-time",
                    "120",
                    url,
                    "-o",
                    str(archive),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return None, "SOURCE_ARCHIVE_DOWNLOAD_FAILED"
        if not _result_ok(result) or not archive.is_file():
            return None, "SOURCE_ARCHIVE_DOWNLOAD_FAILED"
        digest = hashlib.sha256()
        try:
            with archive.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None, "SOURCE_ARCHIVE_HASH_FAILED"
        return digest.hexdigest(), None


def _archive_attestation(
    *,
    component: dict[str, str],
    runner: Callable[..., Any],
    archive_hasher: Callable[[str], tuple[str | None, str | None]] | None,
) -> dict[str, Any]:
    observed, error = (
        archive_hasher(component["source_archive"])
        if archive_hasher is not None
        else _hash_archive(component["source_archive"], runner=runner)
    )
    result: dict[str, Any] = {
        "expected_sha256": component["source_archive_sha256"],
        "observed_sha256": observed,
        "status": "PASS" if observed == component["source_archive_sha256"] else "FAILED",
        "failure_class": error,
        "payload_retained": False,
    }
    if observed is not None and observed != component["source_archive_sha256"]:
        result["failure_class"] = f"{component['component'].upper()}_SOURCE_ATTESTATION_MISMATCH"
    return result


def evaluate(
    *,
    runner: Callable[..., Any] = subprocess.run,
    archive_hasher: Callable[[str], tuple[str | None, str | None]] | None = None,
) -> dict[str, Any]:
    """Run all six subchecks and return a payload-free scalar report."""

    components: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for component in (LITELLM, OMNIROUTE):
        name = component["component"]
        runtime = _runtime_version(component=component, runner=runner)
        tag = resolve_release_tag(
            repo=component["repo"],
            tag=component["tag"],
            expected_commit=component["source_commit"],
            runner=runner,
        )
        archive = _archive_attestation(component=component, runner=runner, archive_hasher=archive_hasher)
        component_report = {
            "runtime_version": runtime,
            "release_tag": tag,
            "source_archive": archive,
        }
        components[name] = component_report
        for check in (runtime, tag, archive):
            if check.get("status") not in {"PASS"} and check.get("resolution_status") != "PASS":
                failure = str(check.get("failure_class") or f"{name.upper()}_PROVENANCE_CHECK_FAILED")
                failures.append(failure)
    status = "PASS" if not failures else "FAILED_CERTIFICATION_IMPLEMENTATION"
    return {
        "schema_version": SCHEMA,
        "status": status,
        "failure_class": failures[0] if failures else None,
        "failure_classes": failures,
        "components": components,
        "floating_tags_used": False,
        "source_or_payload_retained": False,
        "response_payload_retained": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    # Images are fixed by the candidate pin table.  The arguments make the
    # workflow binding explicit and fail closed if it passes a different ref.
    parser.add_argument("--litellm-image", required=True)
    parser.add_argument("--omniroute-image", required=True)
    args = parser.parse_args(argv)
    if args.litellm_image != LITELLM["image"] or args.omniroute_image != OMNIROUTE["image"]:
        report = {
            "schema_version": SCHEMA,
            "status": "FAILED_CERTIFICATION_IMPLEMENTATION",
            "failure_class": "GATEWAY_IMAGE_PIN_INPUT_MISMATCH",
            "failure_classes": ["GATEWAY_IMAGE_PIN_INPUT_MISMATCH"],
            "components": {},
            "floating_tags_used": False,
            "source_or_payload_retained": False,
            "response_payload_retained": False,
        }
    else:
        try:
            report = evaluate()
        except Exception as exc:  # pragma: no cover - defensive evidence boundary
            report = {
                "schema_version": SCHEMA,
                "status": "FAILED_CERTIFICATION_IMPLEMENTATION",
                "failure_class": "GATEWAY_PROVENANCE_HELPER_FAILURE",
                "failure_classes": ["GATEWAY_PROVENANCE_HELPER_FAILURE"],
                "failure_type": type(exc).__name__,
                "components": {},
                "floating_tags_used": False,
                "source_or_payload_retained": False,
                "response_payload_retained": False,
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failure_class": report.get("failure_class")}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
