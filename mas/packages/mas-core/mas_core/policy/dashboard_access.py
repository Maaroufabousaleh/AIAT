"""Section-level authorization for dashboard/API consumers.

The dashboard is a human operator surface, but the control plane is also
called by the CEO runtime, internal services, and worker containers.  API-key
authentication identifies those callers; this module adds the second,
persistable decision: which dashboard section a principal may request.

The policy is deliberately small and deny-by-default for unknown sections.
The operator principal is the only principal that receives the complete
dashboard surface.  A deployment may persist a narrower mapping in the
``system_config`` row identified by :data:`DASHBOARD_SECTION_ACL_CONFIG_KEY`.
Licence metadata is unrelated to this decision.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any

DASHBOARD_SECTION_ACL_CONFIG_KEY = "dashboard.section_acl.v1"

# Keep this list intentionally finite.  It is also the bound used by the
# dashboard header/middleware, so an arbitrary path segment can never become a
# new ACL key at runtime.
DASHBOARD_SECTIONS: tuple[str, ...] = (
    "analytics",
    "ceo",
    "credentials",
    "flows",
    "governance",
    "identity",
    "integrations",
    "operations",
    "projects",
    "system",
    "workers",
)

DASHBOARD_PRINCIPALS: tuple[str, ...] = (
    "operator",
    "ceo",
    "service",
    "worker",
    "pm_gateway",
    "gateway",
)

_ALL_SECTIONS = frozenset(DASHBOARD_SECTIONS)

# Default policy is safe for the existing internal topology and still keeps
# secrets, identity administration, external integrations, and host
# operations out of automated callers.  Values are frozensets so callers
# cannot mutate the process-wide defaults accidentally.
DEFAULT_DASHBOARD_SECTION_ACL: dict[str, frozenset[str]] = {
    "operator": _ALL_SECTIONS,
    "ceo": frozenset({"analytics", "ceo", "flows", "governance", "projects", "system", "workers"}),
    "service": frozenset({"analytics", "flows", "governance", "projects", "system", "workers"}),
    "worker": frozenset({"analytics", "projects", "system", "workers"}),
    "pm_gateway": frozenset({"integrations", "projects", "system"}),
    "gateway": frozenset({"integrations", "system"}),
}


def _clean_sections(value: Any, *, principal: str) -> frozenset[str]:
    if not isinstance(value, Collection) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"ACL entry for {principal!r} must be a list of section names")
    sections = {str(item).strip().lower() for item in value if str(item).strip()}
    unknown = sections - _ALL_SECTIONS
    if unknown:
        raise ValueError(f"ACL entry for {principal!r} contains unknown sections: {sorted(unknown)}")
    return frozenset(sections)


def normalize_dashboard_acl(value: Mapping[str, Any] | str | None) -> dict[str, frozenset[str]]:
    """Return a validated complete ACL, falling back to defaults when absent.

    Persisted values may omit principals to retain their defaults.  Supplying
    an explicit empty list is meaningful and denies that principal every
    section.  Unknown principals are rejected so a typo cannot silently create
    a credential class that the authentication middleware never authenticates.
    """

    if value is None or value == "":
        return dict(DEFAULT_DASHBOARD_SECTION_ACL)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("dashboard ACL config is not valid JSON") from exc
    else:
        parsed = value
    if not isinstance(parsed, Mapping):
        raise ValueError("dashboard ACL config must be an object")

    result = dict(DEFAULT_DASHBOARD_SECTION_ACL)
    for raw_principal, raw_sections in parsed.items():
        principal = str(raw_principal).strip().lower()
        if principal not in DASHBOARD_PRINCIPALS:
            raise ValueError(f"dashboard ACL contains unknown principal: {principal!r}")
        result[principal] = _clean_sections(raw_sections, principal=principal)
    # The operator must remain able to repair a bad automation policy.  This
    # invariant also prevents an accidental persisted config from locking out
    # the only human recovery path.
    result["operator"] = _ALL_SECTIONS
    return result


def serialize_dashboard_acl(value: Mapping[str, Any] | None = None) -> str:
    """Serialize an ACL in deterministic form for ``system_config``."""

    normalized = normalize_dashboard_acl(value)
    return json.dumps(
        {principal: sorted(normalized[principal]) for principal in DASHBOARD_PRINCIPALS},
        sort_keys=True,
        separators=(",", ":"),
    )


def principal_can_access_section(
    principal: str,
    section: str,
    acl: Mapping[str, Sequence[str] | frozenset[str]] | None = None,
) -> bool:
    """Check one authenticated principal against one known dashboard section."""

    principal_key = str(principal).strip().lower()
    section_key = str(section).strip().lower()
    if section_key not in _ALL_SECTIONS:
        return False
    effective = normalize_dashboard_acl(acl)
    return section_key in effective.get(principal_key, frozenset())


def sections_for_principal(
    principal: str,
    acl: Mapping[str, Sequence[str] | frozenset[str]] | None = None,
) -> tuple[str, ...]:
    """Return sorted sections visible to an authenticated principal."""

    effective = normalize_dashboard_acl(acl)
    return tuple(sorted(effective.get(str(principal).strip().lower(), frozenset())))
