"""Validate the checked-in third-party inventory and metadata policy.

The programme is personal/internal-only.  This check verifies that source and
version metadata are present where the inventory claims a resource is
available, but it deliberately does not classify any licence as allowed or
forbidden.  Licence values and restriction notes are catalogue metadata only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def main() -> int:
    inventory_path = Path(__file__).resolve().parents[1] / "docs" / "provenance" / "third_party_components.yaml"
    inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
    policy = inventory.get("policy") or {}
    errors: list[str] = []
    if policy.get("programme_scope") != "personal-internal-only":
        errors.append("programme_scope must be personal-internal-only")
    if policy.get("license_handling") != "metadata-only":
        errors.append("license_handling must be metadata-only")
    if policy.get("enforce_license_gate") is not False:
        errors.append("enforce_license_gate must be false")
    for item in inventory.get("components", []):
        component_id = str(item.get("id") or "").lower()
        status = str(item.get("status") or "")
        if not component_id or not item.get("source"):
            errors.append("every component requires id and source")
        if (
            policy.get("require_exact_version")
            and status not in {"not-installed", "planned"}
            and not status.startswith("unavailable")
            and not item.get("version")
        ):
            errors.append(f"{component_id}: available component requires a recorded version, tag, or digest")
        if status.startswith("unavailable") and not str(item.get("unavailable_reason") or "").strip():
            errors.append(f"{component_id}: unavailable component requires unavailable_reason metadata")
        # ``license``, ``license_evidence``, and any operator notice are
        # intentionally read-and-record fields; no value is rejected here.
    if errors:
        for error in errors:
            print(f"provenance: {error}", file=sys.stderr)
        return 1
    print(f"provenance: validated {len(inventory.get('components', []))} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
