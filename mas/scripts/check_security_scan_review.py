"""Validate the machine-readable security-finding review register.

The scan evidence and this register are intentionally separate.  A coherent
register is a documentation/control contract, not a passing security scan:
unresolved findings continue to block worker activation.  Licence metadata is
never consulted as a gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAS_ROOT = REPO_ROOT / "mas"
DEFAULT_SCAN = MAS_ROOT / "docs" / "provenance" / "security_scan_evidence.yaml"
DEFAULT_REVIEW = MAS_ROOT / "docs" / "provenance" / "security_scan_review.yaml"
SCHEMA = "aiat.security-scan-review.v1"
SCAN_SCHEMA = "aiat.security-scan-evidence.v1"
REPRO_SCHEMA = "aiat.security-scan-reproduction-local.v1"
SCAN_STATUSES = {"passed", "findings_review_required", "blocked"}
REVIEW_STATUSES = {"open", "in_review", "resolved"}


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _resolve_scan_ref(reference: str, scan_path: Path) -> tuple[str, str] | None:
    raw_path, separator, scan_id = reference.partition("#")
    if not separator or not raw_path.strip() or not scan_id.strip():
        return None
    resolved = (REPO_ROOT / raw_path.strip()).resolve()
    if resolved != scan_path.resolve():
        return None
    return str(resolved), scan_id.strip()


def _load_reproduction(
    review_document: dict[str, Any],
    scan_index: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    """Validate the latest exact-source reproduction without retaining findings."""

    errors: list[str] = []
    summary: dict[str, Any] = {"status": "missing"}
    reference = review_document.get("latest_reproduction")
    if not isinstance(reference, dict):
        return ["security review must declare latest_reproduction metadata"], summary
    raw_path = str(reference.get("path") or "").strip()
    expected_schema = str(reference.get("schema_version") or "").strip()
    if not raw_path or expected_schema != REPRO_SCHEMA:
        return ["latest_reproduction must declare a repository path and reproduction schema"], summary
    path = (REPO_ROOT / raw_path).resolve()
    if REPO_ROOT not in path.parents or not path.is_file():
        return ["latest_reproduction path must identify a repository JSON file"], summary
    try:
        reproduction = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"latest_reproduction could not be loaded: {type(exc).__name__}"], summary
    if not isinstance(reproduction, dict):
        return ["latest_reproduction must contain a JSON object"], summary
    if reproduction.get("schema_version") != REPRO_SCHEMA:
        errors.append("latest_reproduction schema_version is invalid")
    if reproduction.get("programme_scope") != "personal-internal-only":
        errors.append("latest_reproduction must remain personal-internal-only")
    source = reproduction.get("source")
    scanner = reproduction.get("scanner")
    scan = reproduction.get("scan")
    boundary = reproduction.get("aiat_boundary_regression")
    release = reproduction.get("release_boundary")
    if not isinstance(source, dict) or not str(source.get("commit") or "").strip():
        errors.append("latest_reproduction source commit is required")
    if not isinstance(scanner, dict) or not str(scanner.get("version") or "").strip():
        errors.append("latest_reproduction scanner version is required")
    if not isinstance(scan, dict):
        errors.append("latest_reproduction scan summary is required")
        scan = {}
    if not isinstance(boundary, dict) or boundary.get("status") != "pass":
        errors.append("latest_reproduction AIAT boundary regression must pass")
    if not isinstance(release, dict) or release.get("technical_gate_status") != "blocked":
        errors.append("latest_reproduction must preserve a blocked technical gate")
    if isinstance(scanner, dict) and scanner.get("raw_output_retained") is not False:
        errors.append("latest_reproduction must not retain raw scanner output")
    if isinstance(source, dict) and source.get("clone_retained") is not False:
        errors.append("latest_reproduction must not retain the source clone")
    finding_count = _non_negative_int(scan.get("finding_count"))
    scanner_error_count = _non_negative_int(scan.get("scanner_error_count"))
    if finding_count is None:
        errors.append("latest_reproduction finding_count must be a non-negative integer")
    if scanner_error_count is None:
        errors.append("latest_reproduction scanner_error_count must be a non-negative integer")
    if scan.get("rule_total_matches_findings") is not True:
        errors.append("latest_reproduction must assert rule-total/finding parity")
    if len(scan_index) == 1:
        historical = next(iter(scan_index.values()))
        if finding_count is not None and finding_count != _non_negative_int(historical.get("finding_count")):
            errors.append("latest_reproduction finding_count disagrees with the registered scan")
        if isinstance(source, dict) and str(source.get("commit") or "") != str(historical.get("source_commit") or ""):
            errors.append("latest_reproduction source commit disagrees with the registered scan")
    summary = {
        "path": raw_path,
        "status": str(reproduction.get("status") or ""),
        "source_commit": str(source.get("commit") or "") if isinstance(source, dict) else "",
        "scanner_version": str(scanner.get("version") or "") if isinstance(scanner, dict) else "",
        "finding_count": finding_count,
        "scanner_error_count": scanner_error_count,
        "technical_gate_status": str(release.get("technical_gate_status") or "") if isinstance(release, dict) else "",
    }
    return errors, summary


def inspect(
    scan_path: Path = DEFAULT_SCAN,
    review_path: Path = DEFAULT_REVIEW,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        scan_document = _load_yaml(scan_path)
        review_document = _load_yaml(review_path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return {
            "schema_version": SCHEMA,
            "mode": "static",
            "status": "fail",
            "errors": [f"security review documents could not be loaded: {type(exc).__name__}"],
            "licence_metadata_is_gate": False,
        }

    if scan_document.get("schema_version") != SCAN_SCHEMA:
        errors.append("security scan evidence must declare aiat.security-scan-evidence.v1")
    if review_document.get("schema_version") != SCHEMA:
        errors.append("security scan review must declare aiat.security-scan-review.v1")
    for name, document in (("scan evidence", scan_document), ("review register", review_document)):
        if document.get("programme_scope") != "personal-internal-only":
            errors.append(f"{name} must remain personal-internal-only")

    policy = review_document.get("policy")
    if not isinstance(policy, dict):
        errors.append("security review policy must be a mapping")
        policy = {}
    if policy.get("security_scan_is_technical_gate") is not True:
        errors.append("security review policy must retain the technical security gate")
    if policy.get("unresolved_findings_block_activation") is not True:
        errors.append("security review policy must keep unresolved findings blocking activation")
    if policy.get("review_register_is_not_a_waiver") is not True:
        errors.append("security review register must not be treated as a waiver")
    if policy.get("licence_metadata_is_gate") is not False:
        errors.append("security review policy must keep licence_metadata_is_gate false")

    scans = scan_document.get("scans")
    if not isinstance(scans, list) or not scans:
        errors.append("security scan evidence must contain at least one scan")
        scans = []
    scan_index: dict[str, dict[str, Any]] = {}
    total_findings = 0
    total_warnings = 0
    technical_gate_status = "passed"
    for row in scans:
        if not isinstance(row, dict):
            errors.append("security scan evidence rows must be mappings")
            continue
        scan_id = str(row.get("id") or "").strip()
        if not scan_id or scan_id in scan_index:
            errors.append("security scan IDs must be unique and non-blank")
            continue
        scan_index[scan_id] = row
        scan_status = str(row.get("status") or "").strip().lower()
        if scan_status not in SCAN_STATUSES:
            errors.append(f"{scan_id}: unsupported scan status {scan_status!r}")
        if scan_status != "passed":
            technical_gate_status = "blocked"
        finding_count = _non_negative_int(row.get("finding_count"))
        warning_count = _non_negative_int(row.get("engine_warning_count"))
        if finding_count is None:
            errors.append(f"{scan_id}: finding_count must be a non-negative integer")
        else:
            total_findings += finding_count
        if warning_count is None:
            errors.append(f"{scan_id}: engine_warning_count must be a non-negative integer")
        else:
            total_warnings += warning_count
        rule_counts = row.get("rule_counts")
        if not isinstance(rule_counts, dict) or not rule_counts:
            errors.append(f"{scan_id}: rule_counts must be a non-empty mapping")
        else:
            rule_total = 0
            for rule, count in rule_counts.items():
                parsed = _non_negative_int(count)
                if not str(rule).strip() or parsed is None:
                    errors.append(f"{scan_id}: rule_counts must contain named non-negative integers")
                else:
                    rule_total += parsed
            if finding_count is not None and rule_total != finding_count:
                errors.append(
                    f"{scan_id}: rule_counts total {rule_total} disagrees with finding_count {finding_count}"
                )

    reviews = review_document.get("reviews")
    if not isinstance(reviews, list) or not reviews:
        errors.append("security review register must contain at least one review")
        reviews = []
    review_scan_ids: set[str] = set()
    review_rows: list[dict[str, Any]] = []
    review_required_count = 0
    for review in reviews:
        if not isinstance(review, dict):
            errors.append("security review rows must be mappings")
            continue
        review_id = str(review.get("id") or "").strip()
        if not review_id:
            errors.append("security review IDs must be non-blank")
        reference = str(review.get("scan_ref") or "").strip()
        resolved_ref = _resolve_scan_ref(reference, scan_path)
        if resolved_ref is None:
            errors.append(f"{review_id or '<unnamed>'}: scan_ref must point to the configured scan evidence")
            scan_id = ""
        else:
            _resolved_path, scan_id = resolved_ref
            if scan_id not in scan_index:
                errors.append(f"{review_id or '<unnamed>'}: referenced scan {scan_id!r} is missing")
            elif scan_id in review_scan_ids:
                errors.append(f"{scan_id}: more than one review row is registered")
            review_scan_ids.add(scan_id)
        status = str(review.get("status") or "").strip().lower()
        if status not in REVIEW_STATUSES:
            errors.append(f"{review_id or '<unnamed>'}: unsupported review status {status!r}")
        if not str(review.get("owner") or "").strip():
            errors.append(f"{review_id or '<unnamed>'}: owner is required")
        if not str(review.get("next_action") or "").strip():
            errors.append(f"{review_id or '<unnamed>'}: next_action is required")
        if not str(review.get("engine_warning_action") or "").strip():
            errors.append(f"{review_id or '<unnamed>'}: engine_warning_action is required")
        scan = scan_index.get(scan_id)
        if scan is not None:
            scan_status = str(scan.get("status") or "").strip().lower()
            gate_status = str(review.get("gate_status") or "").strip().lower()
            expected_gate = "passed" if scan_status == "passed" else "blocked"
            if gate_status != expected_gate:
                errors.append(f"{review_id}: gate_status must be {expected_gate!r} for scan status {scan_status!r}")
            if scan_status != "passed":
                review_required_count += 1
            if scan_status != "passed" and status == "resolved":
                errors.append(f"{review_id}: unresolved scan findings cannot have resolved review status")

            rule_counts = scan.get("rule_counts") if isinstance(scan.get("rule_counts"), dict) else {}
            groups = review.get("rule_groups")
            if not isinstance(groups, list) or not groups:
                errors.append(f"{review_id}: rule_groups must be a non-empty list")
                groups = []
            mapped_rules: dict[str, str] = {}
            group_count_total = 0
            group_ids: set[str] = set()
            for group in groups:
                if not isinstance(group, dict):
                    errors.append(f"{review_id}: rule_groups must contain mappings")
                    continue
                group_id = str(group.get("id") or "").strip()
                if not group_id or group_id in group_ids:
                    errors.append(f"{review_id}: rule-group IDs must be unique and non-blank")
                group_ids.add(group_id)
                if not str(group.get("next_action") or "").strip():
                    errors.append(f"{review_id}/{group_id}: next_action is required")
                rules = group.get("rules")
                if not isinstance(rules, list) or not rules:
                    errors.append(f"{review_id}/{group_id}: rules must be a non-empty list")
                    continue
                computed = 0
                for rule in rules:
                    rule_name = str(rule or "").strip()
                    if rule_name not in rule_counts:
                        errors.append(f"{review_id}/{group_id}: rule {rule_name!r} is not in scan rule_counts")
                        continue
                    if rule_name in mapped_rules:
                        errors.append(f"{review_id}: rule {rule_name!r} is mapped more than once")
                    mapped_rules[rule_name] = group_id
                    computed += int(rule_counts[rule_name])
                declared = _non_negative_int(group.get("finding_count"))
                if declared is None or declared != computed:
                    errors.append(
                        f"{review_id}/{group_id}: finding_count {group.get('finding_count')!r} disagrees with mapped rules {computed}"
                    )
                group_count_total += computed
            missing_rules = sorted(set(rule_counts) - set(mapped_rules))
            if missing_rules:
                errors.append(f"{review_id}: rules are not assigned a review group: {missing_rules}")
            if group_count_total != _non_negative_int(scan.get("finding_count")):
                errors.append(f"{review_id}: grouped finding counts do not cover the scan")
        review_rows.append(
            {
                "id": review_id,
                "scan_id": scan_id,
                "owner": str(review.get("owner") or ""),
                "status": status,
                "gate_status": str(review.get("gate_status") or ""),
            }
        )

    missing_reviews = sorted(set(scan_index) - review_scan_ids)
    if missing_reviews:
        errors.append(f"scans without a review row: {missing_reviews}")

    reproduction_errors, reproduction_summary = _load_reproduction(review_document, scan_index)
    errors.extend(reproduction_errors)

    return {
        "schema_version": SCHEMA,
        "mode": "static",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "scan_count": len(scan_index),
        "finding_count": total_findings,
        "engine_warning_count": total_warnings,
        "review_required_count": review_required_count,
        "technical_gate_status": technical_gate_status,
        "reviews": review_rows,
        "latest_reproduction": reproduction_summary,
        "licence_metadata_is_gate": False,
        "scope": "secret-safe security-finding review register; no scan, waiver, activation, or deployment mutation",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, default=DEFAULT_SCAN)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inspect(args.scan, args.review)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"security-scan-review: {report['status']} scans={report['scan_count']} "
            f"findings={report['finding_count']} review_required={report['review_required_count']} "
            f"technical_gate={report['technical_gate_status']}"
        )
        for error in report["errors"]:
            print(f"security-scan-review: {error}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
