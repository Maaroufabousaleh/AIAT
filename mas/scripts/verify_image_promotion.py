#!/usr/bin/env python3
"""Fail-closed preflight for promoting pinned AIAT image evidence to GHCR.

This program is shipped with the trusted promotion workflow. It never loads or
executes code from the candidate checkout; it validates run metadata and the
contents of GitHub Actions evidence archives only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from verify_image_candidate_evidence import verify_evidence
except ImportError:  # pragma: no cover - exercised by script invocation
    verify_evidence = None  # type: ignore[assignment]

REPOSITORY = "Maaroufabousaleh/AIAT"
TRUSTED_WORKFLOW = ".github/workflows/aiat-image-candidate-promotion.yml"
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
ROLE_RANK = {"read": 1, "triage": 1, "write": 2, "maintain": 3, "admin": 4}
MAX_ARCHIVE_ENTRIES = 2000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class PromotionError(ValueError):
    """A required release precondition was missing or did not match."""


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError("promotion candidate manifest is unavailable or invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "aiat.image-promotion-candidate.v1":
        raise PromotionError("promotion candidate manifest schema is unsupported")
    if value.get("repository") != REPOSITORY:
        raise PromotionError("promotion manifest targets a different repository")
    source = value.get("source")
    if not isinstance(source, dict) or not SOURCE_SHA_RE.fullmatch(str(source.get("sha", ""))):
        raise PromotionError("promotion manifest source SHA is invalid")
    if not isinstance(value.get("runs"), dict) or not isinstance(value.get("images"), dict):
        raise PromotionError("promotion manifest is incomplete")
    return value


def _run(manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    try:
        result = manifest["runs"][kind]
    except (KeyError, TypeError) as exc:
        raise PromotionError(f"expected {kind} evidence run is not pinned") from exc
    if not isinstance(result, dict):
        raise PromotionError(f"expected {kind} evidence run is malformed")
    return result


def validate_dispatch(
    manifest: dict[str, Any],
    *,
    candidate_sha: str,
    image: str,
    ci_run_id: str,
    worker_run_id: str,
    redis_run_id: str,
    run_ref: str,
    workflow_ref: str,
    workflow_sha: str,
) -> dict[str, Any]:
    """Require the trusted default-branch workflow and exact reviewed inputs."""
    if image not in {"redis", "worker"}:
        raise PromotionError("promote one image at a time; valid values are redis or worker")
    expected_sha = manifest["source"]["sha"]
    if not SOURCE_SHA_RE.fullmatch(candidate_sha) or candidate_sha != expected_sha:
        raise PromotionError("candidate SHA differs from the reviewed evidence manifest")
    if run_ref != "refs/heads/main":
        raise PromotionError("promotion workflow must run from the reviewed main branch")
    expected_workflow_ref = f"{REPOSITORY}/{TRUSTED_WORKFLOW}@refs/heads/main"
    if workflow_ref != expected_workflow_ref:
        raise PromotionError("workflow code is not the trusted main-branch promotion workflow")
    if workflow_sha != os.environ.get("GITHUB_SHA", workflow_sha):
        raise PromotionError("workflow checkout does not match the trusted dispatch revision")
    supplied = {"repository_ci": ci_run_id, "worker": worker_run_id, "redis": redis_run_id}
    for name, run_id in supplied.items():
        expected = str(_run(manifest, name).get("id", ""))
        if not run_id.isdigit() or run_id != expected:
            raise PromotionError(f"{name} evidence run ID differs from the pinned manifest")
    return manifest["images"][image]


def validate_run_record(run: dict[str, Any], expected: dict[str, Any], source_sha: str) -> None:
    checks = {
        "id": str(expected["id"]),
        "head_sha": source_sha,
        "head_branch": expected["branch"],
        "event": expected["event"],
        "status": "completed",
        "conclusion": "success",
        "path": expected["path"],
        "name": expected["name"],
    }
    for field, value in checks.items():
        actual = str(run.get(field, ""))
        if actual != str(value):
            raise PromotionError(f"{expected['kind']} evidence run {field} does not match the reviewed manifest")


def validate_artifact_record(
    artifact: dict[str, Any],
    expected: dict[str, Any],
    source_sha: str,
    *,
    http_status: int = 200,
) -> None:
    if http_status != 200:
        raise PromotionError(f"evidence artifact metadata is not verifiable (HTTP {http_status})")
    if str(artifact.get("id")) != str(expected["artifact_id"]):
        raise PromotionError("evidence artifact ID does not match the pinned run")
    if artifact.get("name") != expected["artifact_name"]:
        raise PromotionError("evidence artifact name does not match the pinned run")
    if artifact.get("expired") is not False:
        raise PromotionError("evidence artifact is expired or its expiry state is unknown")
    if artifact.get("digest") != expected["artifact_digest"]:
        raise PromotionError("GitHub artifact archive digest differs from the pinned manifest")
    owner = artifact.get("workflow_run") or {}
    if str(owner.get("id")) != str(expected["id"]):
        raise PromotionError("evidence artifact belongs to a different workflow run")
    if owner.get("head_sha") != source_sha or owner.get("head_branch") != expected["branch"]:
        raise PromotionError("evidence artifact source revision differs from the reviewed candidate")


def verify_outer_artifact(archive: Path, expected_digest: str) -> None:
    match = re.fullmatch(r"sha256:([0-9a-f]{64})", expected_digest)
    if not match or not archive.is_file():
        raise PromotionError("downloaded evidence artifact is missing or its digest is malformed")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != match.group(1):
        raise PromotionError("downloaded GitHub Actions artifact ZIP checksum does not match metadata")


def safe_extract_artifact(archive: Path, destination: Path) -> None:
    """Extract a bounded evidence ZIP without path traversal or symlinks."""
    if destination.exists() and any(destination.iterdir()):
        raise PromotionError("evidence extraction destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise PromotionError("evidence ZIP has too many entries")
            total_size = sum(item.file_size for item in entries)
            if total_size > MAX_ARCHIVE_BYTES:
                raise PromotionError("evidence ZIP expands beyond the configured size bound")
            seen: set[str] = set()
            for item in entries:
                raw_name = item.filename
                name = PurePosixPath(raw_name)
                if (
                    not raw_name
                    or "\\" in raw_name
                    or name.is_absolute()
                    or any(part in {"", ".", ".."} for part in name.parts)
                    or name.parts[0].endswith(":")
                ):
                    raise PromotionError("evidence ZIP contains an unsafe path")
                normalized = name.as_posix()
                if normalized in seen:
                    raise PromotionError("evidence ZIP contains duplicate paths")
                seen.add(normalized)
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PromotionError("evidence ZIP contains a symbolic link")
                target = destination.joinpath(*name.parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PromotionError("evidence ZIP could not be safely extracted") from exc


def verify_candidate_evidence(manifest: dict[str, Any], image: str, root: Path) -> dict[str, Any]:
    if verify_evidence is None:
        raise PromotionError("trusted candidate evidence verifier is unavailable")
    source_sha = manifest["source"]["sha"]
    expected = manifest["images"][image]
    try:
        result = verify_evidence(image, root, source_sha)
    except (ValueError, OSError) as exc:
        raise PromotionError(f"{image} candidate evidence failed validation: {exc}") from exc
    for field in ("archive_sha256", "config_digest"):
        if result.get(field) != expected.get(field):
            raise PromotionError(f"{image} candidate {field} differs from the reviewed manifest")
    if image == "worker":
        for field in ("platform_manifest_digest",):
            if result.get(field) != expected.get(field):
                raise PromotionError(f"worker candidate {field} differs from the reviewed manifest")
        candidate = json.loads((root / "candidate-image.json").read_text(encoding="utf-8"))
        if candidate.get("buildkit_builder_authenticated") is not expected.get("buildkit_builder_authenticated"):
            raise PromotionError("worker BuildKit-authentication evidence changed from the reviewed manifest")
    return result


def _api_get(api_base: str, token: str, path: str) -> tuple[int, dict[str, Any]]:
    url = api_base.rstrip("/") + path
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AIAT-image-promotion-preflight",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        # A 404 can mean missing or inaccessible. Never interpret it as absent.
        raise PromotionError(f"read-only GitHub control check is unverifiable (HTTP {exc.code})") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise PromotionError("read-only GitHub control check could not reach the API") from exc
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PromotionError("read-only GitHub control API returned invalid JSON") from exc
    if status != 200 or not isinstance(data, dict):
        raise PromotionError(f"read-only GitHub control check is unverifiable (HTTP {status})")
    return status, data


def _permission_for_reviewer(api_base: str, token: str, reviewer: dict[str, Any]) -> str:
    kind = reviewer.get("type")
    identity = reviewer.get("reviewer") or {}
    if kind == "User":
        login = identity.get("login")
        if not isinstance(login, str) or not login:
            raise PromotionError("required environment reviewer identity is unavailable")
        path = f"/repos/{REPOSITORY}/collaborators/{urllib.parse.quote(login, safe='')}/permission"
        _, data = _api_get(api_base, token, path)
        return str(data.get("permission", ""))
    if kind == "Team":
        slug = identity.get("slug")
        if not isinstance(slug, str) or not slug:
            raise PromotionError("required environment team reviewer identity is unavailable")
        owner, repo = REPOSITORY.split("/", 1)
        path = f"/orgs/{owner}/teams/{urllib.parse.quote(slug, safe='')}/repos/{owner}/{repo}"
        _, data = _api_get(api_base, token, path)
        return str(data.get("permission", ""))
    raise PromotionError("environment contains an unsupported required reviewer type")


def _registry_token(owner: str, package: str, read_token: str) -> str:
    """Exchange a read:packages PAT for a pull-only GHCR bearer token."""
    scope = urllib.parse.urlencode({"scope": f"repository:{owner.lower()}/{package}:pull"})
    request = urllib.request.Request(
        f"https://ghcr.io/token?{scope}",
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"{owner}:{read_token}".encode("utf-8")).decode("ascii"),
            "Accept": "application/json",
            "User-Agent": "AIAT-image-promotion-preflight",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise PromotionError("read-only GHCR package access could not be verified") from exc
    token = data.get("token") or data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise PromotionError("GHCR did not return a read-only package token")
    return token


def _require_candidate_tag_absent(owner: str, package: str, source_sha: str, read_token: str) -> None:
    """Reject a pre-existing candidate tag so retries cannot silently retag it."""
    bearer = _registry_token(owner, package, read_token)
    url = (
        f"https://ghcr.io/v2/{owner.lower()}/{package}/manifests/"
        f"candidate-{source_sha}"
    )
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": ", ".join(
                (
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                )
            ),
            "User-Agent": "AIAT-image-promotion-preflight",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if 200 <= response.status < 300:
                raise PromotionError("candidate tag already exists; refusing to overwrite it")
            raise PromotionError("GHCR candidate-tag preflight returned an unexpected status")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # This is a manifest lookup only, after package metadata and pull
            # authorization both succeeded; metadata 404s always fail closed.
            return
        raise PromotionError("GHCR candidate-tag preflight could not be verified") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise PromotionError("GHCR candidate-tag preflight could not reach the registry") from exc


def verify_release_controls(
    manifest: dict[str, Any],
    image: str,
    *,
    token: str,
    package_metadata_token: str,
    api_base: str,
    package_access_mode: str,
    package_access_verified_sha: str,
    redis_promoted_digest: str = "",
    redis_promoted_source_sha: str = "",
) -> dict[str, Any]:
    """Read current GitHub protections and package metadata; every unknown blocks."""
    if not token:
        raise PromotionError("read-only release-control GitHub App credentials are not configured")
    if not package_metadata_token:
        raise PromotionError("read-only GHCR metadata credential is not configured")
    source_sha = manifest["source"]["sha"]
    _, environment = _api_get(
        api_base,
        token,
        f"/repos/{REPOSITORY}/environments/aiat-image-candidate-release",
    )
    if environment.get("name") != "aiat-image-candidate-release":
        raise PromotionError("release environment identity does not match")
    rules = environment.get("protection_rules")
    required = [rule for rule in rules or [] if isinstance(rule, dict) and rule.get("type") == "required_reviewers"]
    if len(required) != 1:
        raise PromotionError("release environment must have exactly one required-reviewer rule")
    rule = required[0]
    reviewers = rule.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers or rule.get("prevent_self_review") is not True:
        raise PromotionError("release environment reviewer protection is missing or allows self-review")
    policy = environment.get("deployment_branch_policy") or {}
    if policy.get("protected_branches") is not True:
        raise PromotionError("release environment must be restricted to protected branches")
    for reviewer in reviewers:
        if not isinstance(reviewer, dict):
            raise PromotionError("release environment reviewer record is malformed")
        permission = _permission_for_reviewer(api_base, token, reviewer)
        if ROLE_RANK.get(permission, 0) < ROLE_RANK["write"]:
            raise PromotionError("a required release reviewer lacks repository write access")

    _, branch = _api_get(api_base, token, f"/repos/{REPOSITORY}/branches/main/protection")
    reviews = branch.get("required_pull_request_reviews") or {}
    approvals = reviews.get("required_approving_review_count", 0)
    enforce_admins = branch.get("enforce_admins") or {}
    if not isinstance(approvals, int) or approvals < 1 or enforce_admins.get("enabled") is not True:
        raise PromotionError("main branch must enforce at least one pull-request review for all writers")

    if package_access_mode != "repository_inherited" or package_access_verified_sha != source_sha:
        raise PromotionError("private GHCR package inheritance has not been attested for this exact candidate")
    package_names = manifest["images"][image]["package_name"]
    if not isinstance(package_names, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", package_names):
        raise PromotionError("permitted GHCR package name is invalid")
    owner = REPOSITORY.split("/", 1)[0]
    package_path = (
        f"/users/{owner}/packages/container/{urllib.parse.quote(package_names, safe='')}"
    )
    # GitHub documents classic PAT authentication for Packages REST metadata.
    # This credential is read-only (read:packages) and never reaches publish.
    _, package = _api_get(api_base, package_metadata_token, package_path)
    if package.get("name", "").lower() != package_names.lower():
        raise PromotionError("GHCR package name differs from the exact permitted name")
    if (package.get("owner") or {}).get("login", "").lower() != owner.lower():
        raise PromotionError("GHCR package owner differs from the authorized namespace")
    if package.get("package_type") != "container" or package.get("visibility") != "private":
        raise PromotionError("GHCR destination package is not a verified private container package")
    linked_repo = (package.get("repository") or {}).get("full_name")
    if linked_repo != REPOSITORY:
        raise PromotionError("GHCR package is not linked to the AIAT repository")

    _require_candidate_tag_absent(owner, package_names, source_sha, package_metadata_token)

    if image == "worker":
        if not SHA256_RE.fullmatch(redis_promoted_digest):
            raise PromotionError("Redis candidate must be promoted and digest-pinned before the worker")
        if redis_promoted_source_sha != source_sha:
            raise PromotionError("Redis prerequisite digest is not bound to this candidate source SHA")
    return {
        "status": "controls_verified_read_only",
        "image": image,
        "package": f"ghcr.io/{owner.lower()}/{package_names}",
        "visibility": "private",
        "repository_link": REPOSITORY,
        "reviewers_checked": len(reviewers),
        "main_pull_request_reviews": approvals,
        "source_sha": source_sha,
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError("input JSON is missing or invalid") from exc
    if not isinstance(value, dict):
        raise PromotionError("input JSON must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--candidate-sha", required=True)
    dispatch.add_argument("--image", choices=("redis", "worker"), required=True)
    dispatch.add_argument("--ci-run-id", required=True)
    dispatch.add_argument("--worker-run-id", required=True)
    dispatch.add_argument("--redis-run-id", required=True)
    dispatch.add_argument("--run-ref", required=True)
    dispatch.add_argument("--workflow-ref", required=True)
    dispatch.add_argument("--workflow-sha", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--kind", choices=("repository_ci", "worker", "redis"), required=True)
    run_parser.add_argument("--json", type=Path, required=True)

    artifact_parser = sub.add_parser("artifact")
    artifact_parser.add_argument("--kind", choices=("worker", "redis"), required=True)
    artifact_parser.add_argument("--json", type=Path, required=True)
    artifact_parser.add_argument("--http-status", type=int, default=200)

    extract_parser = sub.add_parser("extract")
    extract_parser.add_argument("--kind", choices=("worker", "redis"), required=True)
    extract_parser.add_argument("--zip", type=Path, required=True)
    extract_parser.add_argument("--destination", type=Path, required=True)

    evidence_parser = sub.add_parser("evidence")
    evidence_parser.add_argument("--kind", choices=("worker", "redis"), required=True)
    evidence_parser.add_argument("--root", type=Path, required=True)

    controls_parser = sub.add_parser("controls")
    controls_parser.add_argument("--kind", choices=("worker", "redis"), required=True)

    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "dispatch":
            validate_dispatch(
                manifest,
                candidate_sha=args.candidate_sha,
                image=args.image,
                ci_run_id=args.ci_run_id,
                worker_run_id=args.worker_run_id,
                redis_run_id=args.redis_run_id,
                run_ref=args.run_ref,
                workflow_ref=args.workflow_ref,
                workflow_sha=args.workflow_sha,
            )
            result: dict[str, Any] = {"status": "dispatch_inputs_verified", "image": args.image}
        elif args.command == "run":
            expected = _run(manifest, args.kind) | {"kind": args.kind}
            validate_run_record(_load_json(args.json), expected, manifest["source"]["sha"])
            result = {"status": "evidence_run_verified", "kind": args.kind, "id": expected["id"]}
        elif args.command == "artifact":
            expected = manifest["images"][args.kind]
            validate_artifact_record(
                _load_json(args.json), expected, manifest["source"]["sha"], http_status=args.http_status
            )
            result = {"status": "evidence_artifact_verified", "kind": args.kind}
        elif args.command == "extract":
            expected = manifest["images"][args.kind]
            verify_outer_artifact(args.zip, expected["artifact_digest"])
            safe_extract_artifact(args.zip, args.destination)
            result = {"status": "artifact_hash_and_safe_extract_verified", "kind": args.kind}
        elif args.command == "evidence":
            result = verify_candidate_evidence(manifest, args.kind, args.root)
        else:
            result = verify_release_controls(
                manifest,
                args.kind,
                token=os.environ.get("AIAT_RELEASE_CONTROL_TOKEN", ""),
                package_metadata_token=os.environ.get("AIAT_GHCR_METADATA_TOKEN", ""),
                api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                package_access_mode=os.environ.get("AIAT_GHCR_PACKAGE_ACCESS_MODE", ""),
                package_access_verified_sha=os.environ.get("AIAT_GHCR_PACKAGE_ACCESS_VERIFIED_SHA", ""),
                redis_promoted_digest=os.environ.get("AIAT_GHCR_REDIS_PROMOTED_DIGEST", ""),
                redis_promoted_source_sha=os.environ.get("AIAT_GHCR_REDIS_PROMOTED_SOURCE_SHA", ""),
            )
    except PromotionError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
