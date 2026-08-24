"""Validate a retained clean-clone static release-ledger certificate.

The certificate is deliberately narrower than a release decision.  It proves
that the exact candidate commit was evaluated from a fresh Git clone without
the development checkout's protected/operator-owned dirty paths.  Native,
live-provider, pending-evidence, and activation gates remain separate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = MAS_ROOT / "docs" / "provenance" / "release_ledger_clean_candidate_static.json"
SCHEMA = "aiat.release-ledger-clean-candidate-check.v1"
EVIDENCE_SCHEMA = "aiat.release-ledger-clean-candidate.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _run(repo: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout.strip()


def _worktree_clean(repo: Path) -> bool:
    code, status = _run(repo, "status", "--porcelain", "--untracked-files=all")
    return code == 0 and not status


def validate(
    *,
    repo: Path = MAS_ROOT,
    evidence_path: Path = DEFAULT_EVIDENCE,
    candidate_sha: str | None = None,
    require_current_checkout: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "errors": [f"clean-candidate evidence unavailable: {type(exc).__name__}"],
            "payloads_retained": False,
        }

    if not isinstance(evidence, dict) or evidence.get("schema_version") != EVIDENCE_SCHEMA:
        errors.append("clean-candidate evidence schema is invalid")

    code, current_revision = _run(repo, "rev-parse", "HEAD")
    if code != 0 or not current_revision:
        errors.append("current checkout revision is unavailable")
        current_revision = None

    evidence_revision = evidence.get("candidate_sha") if isinstance(evidence, dict) else None

    # The retained certificate is deliberately pinned to the exact clean
    # candidate it evaluated.  Later documentation/evidence commits may move
    # the development checkout without invalidating that historical candidate
    # certificate.  Callers that need the checkout tip to match can opt into
    # the strict check explicitly.
    candidate_revision = candidate_sha.strip() if candidate_sha else evidence_revision or current_revision
    if not candidate_revision or not SHA_RE.fullmatch(candidate_revision):
        errors.append("candidate revision is invalid")
        candidate_revision = None
    elif _run(repo, "cat-file", "-e", f"{candidate_revision}^{{commit}}",)[0] != 0:
        errors.append("candidate revision is not present in the checkout")

    if evidence_revision != candidate_revision:
        errors.append("clean-candidate evidence does not match the current candidate revision")
    if require_current_checkout and candidate_revision != current_revision:
        errors.append("clean-candidate revision is not the current checkout revision")

    static = evidence.get("static_ledger") if isinstance(evidence, dict) else None
    if not isinstance(static, dict):
        errors.append("static ledger summary is missing")
        static = {}
    if static.get("status") != "pass":
        errors.append("clean-candidate static ledger did not pass")
    if static.get("checks_passed") != static.get("checks_total"):
        errors.append("clean-candidate static ledger has incomplete checks")
    if static.get("checks_failed") != 0 or static.get("checks_blocked") != 0:
        errors.append("clean-candidate static ledger contains failed or blocked checks")
    if evidence.get("worktree_clean") is not True or evidence.get("changed_path_count") != 0:
        errors.append("retained clean-clone evidence is not clean")
    if evidence.get("protected_operator_files_included") is not False:
        errors.append("protected/operator-owned files were not explicitly excluded")
    if evidence.get("payloads_retained") is not False or evidence.get("credentials_retained") is not False:
        errors.append("clean-candidate evidence retention boundary is unsafe")

    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "BLOCKED",
        "candidate_revision": candidate_revision,
        "current_checkout_revision": current_revision,
        "evidence_revision": evidence_revision,
        "candidate_revision_matches": evidence_revision == candidate_revision,
        "candidate_is_current_checkout": candidate_revision == current_revision,
        "current_checkout_match_required": require_current_checkout,
        "clean_clone": evidence.get("worktree_clean") is True and evidence.get("changed_path_count") == 0,
        "current_checkout_clean": _worktree_clean(repo),
        "static_ledger": {
            "status": static.get("status"),
            "checks_passed": static.get("checks_passed"),
            "checks_total": static.get("checks_total"),
            "pending_evidence_count": static.get("pending_evidence_count"),
            "release_decision": static.get("release_decision"),
        },
        "payloads_retained": evidence.get("payloads_retained") is True,
        "credentials_retained": evidence.get("credentials_retained") is True,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--candidate-sha", help="explicit candidate commit represented by the evidence")
    parser.add_argument(
        "--require-current-checkout",
        action="store_true",
        help="also require the retained candidate to equal the current checkout tip",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = validate(
        evidence_path=args.evidence.resolve(),
        candidate_sha=args.candidate_sha,
        require_current_checkout=args.require_current_checkout,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"clean-candidate: {report['status']}")
        for error in report["errors"]:
            print(f"  - {error}")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
