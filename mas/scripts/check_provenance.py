"""Fail closed when the checked-in third-party inventory violates policy."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def main() -> int:
    inventory_path = Path(__file__).resolve().parents[1] / "docs" / "provenance" / "third_party_components.yaml"
    inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
    policy = inventory.get("policy") or {}
    allowlist = {str(value).upper() for value in policy.get("default_license_allowlist", [])}
    prohibited = {str(value).upper() for value in policy.get("prohibited_default_licenses", [])}
    prohibited_ids = {str(value).lower() for value in policy.get("prohibited_default_components", [])}
    errors: list[str] = []
    for item in inventory.get("components", []):
        component_id = str(item.get("id") or "").lower()
        license_id = str(item.get("license") or "").upper()
        status = str(item.get("status") or "")
        if not component_id or not item.get("source"):
            errors.append("every component requires id and source")
        if policy.get("require_exact_version") and status.startswith("approved") and not item.get("version"):
            errors.append(f"{component_id}: approved component requires an exact version or image tag")
        if policy.get("require_source_and_license_evidence") and status.startswith("approved") and not item.get("license_evidence"):
            errors.append(f"{component_id}: approved component requires license evidence")
        if status == "approved" and license_id not in allowlist:
            errors.append(f"{component_id}: {license_id} is outside the default allowlist")
        if status == "approved" and license_id in prohibited:
            errors.append(f"{component_id}: prohibited default license {license_id}")
        if status == "approved" and component_id in prohibited_ids:
            errors.append(f"{component_id}: prohibited default component")
    if errors:
        for error in errors:
            print(f"provenance: {error}", file=sys.stderr)
        return 1
    print(f"provenance: validated {len(inventory.get('components', []))} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
