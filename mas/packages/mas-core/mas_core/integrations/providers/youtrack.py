"""YouTrack REST/Webhook Triggers adapter.

The adapter intentionally uses only the public REST surface and a restricted
per-connection token. It never creates users, changes global settings, or
changes provider authority policy.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Any

from ..contracts import (
    AIAT_STABLE_PROJECT_FIELDS,
    DEDICATED_PROJECT_MAPPING_PROFILE,
    UMBRELLA_ISSUES_MAPPING_PROFILE,
    AdapterCapabilities,
    BootstrapAction,
    BootstrapApplyResult,
    BootstrapPlan,
    CanonicalIteration,
    CanonicalProject,
    CanonicalWorkItem,
    ExternalEvent,
    ExternalObject,
    IntegrationActor,
    NormalizedCommand,
    ObjectType,
    ProjectionResult,
    ProjectionStatus,
    ProjectProvisioningApplyResult,
    ProjectProvisioningPlan,
    ProviderConnection,
    normalize_project_mapping_profile,
    normalized_content_hash,
)
from .base import (
    CredentialResolver,
    ProviderHTTP,
    ProviderRequestError,
    resolve_secret,
    response_json,
)

# YouTrack's default roles are deliberately named here instead of being
# treated as a generic "admin" capability.  The integration account is a
# project owner/admin for AIAT projects, but it is not a system administrator.
YOUTRACK_EXPECTED_GLOBAL_ROLES = ("Observer", "Project Creator")
YOUTRACK_EXPECTED_GLOBAL_PERMISSIONS = ("Create Project",)
YOUTRACK_EXPECTED_PROJECT_ROLES = ("Project Admin",)
YOUTRACK_FORBIDDEN_GLOBAL_ADMINISTRATION = (
    "System Admin",
    "System Administrator",
    "User Manager",
    "Low-level Admin Read",
    "Low-level Admin Write",
    "Update Organization",
    "Read Organization",
    "Create Organization",
    "Delete Organization",
    "Admin Read App",
    "Admin Update App",
    "Global App Administration",
    "Authentication Administration",
    "Authentication Admin",
    "Update Authentication",
    "Manage Authentication",
    "Organization Administration",
    "Delete Project",
)


_YOUTRACK_PERMISSION_ALIASES = {
    "global_observer": "observer",
    "observer": "observer",
    "global_project_creator": "project_creator",
    "project_creator": "project_creator",
    "create_project": "create_project",
    "project_admin": "project_admin",
    "system_admin": "system_admin",
    "system_administrator": "system_admin",
    "user_manager": "user_manager",
    "low_level_admin_read": "low_level_admin_read",
    "low_level_admin_write": "low_level_admin_write",
    "admin_read_app": "admin_read_app",
    "admin_update_app": "admin_update_app",
    "global_app_administration": "global_app_administration",
    "app_administration": "global_app_administration",
    "update_organization": "update_organization",
    "read_organization": "read_organization",
    "create_organization": "create_organization",
    "delete_organization": "delete_organization",
    "organization_administration": "organization_administration",
    "authentication_administration": "authentication_administration",
    "authentication_admin": "authentication_administration",
    "update_authentication": "update_authentication",
    "manage_authentication": "manage_authentication",
    "delete_project": "delete_project",
}


_YOUTRACK_FORBIDDEN_KEYS = {
    _YOUTRACK_PERMISSION_ALIASES.get(
        re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    )
    for value in YOUTRACK_FORBIDDEN_GLOBAL_ADMINISTRATION
}


def _normalize_youtrack_permission(value: Any) -> str:
    """Normalize role/permission names from REST or operator evidence."""
    key = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    if key.startswith("global_") and key not in _YOUTRACK_PERMISSION_ALIASES:
        key = key.removeprefix("global_")
    return _YOUTRACK_PERMISSION_ALIASES.get(key, key)


def _youtrack_scope(value: Any, fallback: str | None = None) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("$type") or value.get("type") or value.get("id")
    key = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    if "global" in key:
        return "global"
    if "project" in key:
        return "project"
    return fallback


def _youtrack_permission_report(
    evidence: Any,
    *,
    managed_project_ids: list[str],
) -> dict[str, Any]:
    """Evaluate operator-supplied permission evidence without widening access.

    YouTrack does not expose a safe, portable "effective permissions" probe on
    every supported Cloud/Server version.  The doctor therefore evaluates the
    redacted permission snapshot captured during live certification.  The
    accepted shape intentionally supports both REST assigned-role responses and
    a compact hand-authored fixture (``global_roles``, ``project_roles``, etc.).
    No credential-shaped value is retained in the returned report.
    """

    records: list[dict[str, str | None]] = []

    def add(value: Any, *, scope: str | None = None, project_id: str | None = None, kind: str | None = None) -> None:
        if isinstance(value, str):
            normalized = _normalize_youtrack_permission(value)
            if normalized:
                records.append({"name": normalized, "scope": scope, "project_id": project_id, "kind": kind})
            return
        if isinstance(value, list):
            for item in value:
                add(item, scope=scope, project_id=project_id, kind=kind)
            return
        if not isinstance(value, dict):
            return

        scope_value = value.get("scope")
        local_scope = _youtrack_scope(scope_value, scope)
        scope_project = (
            scope_value.get("id")
            if isinstance(scope_value, dict) and local_scope == "project"
            else None
        )
        local_project = value.get("project_id") or value.get("projectId") or project_id or scope_project
        # Assigned-role REST objects have role/name and scope nested objects.
        if value.get("role") is not None:
            add(value["role"], scope=local_scope, project_id=str(local_project) if local_project else None, kind="role")
        if value.get("permission") is not None:
            add(value["permission"], scope=local_scope, project_id=str(local_project) if local_project else None, kind="permission")
        name = value.get("name") or value.get("key")
        if isinstance(name, str):
            records.append(
                {
                    "name": _normalize_youtrack_permission(name),
                    "scope": local_scope,
                    "project_id": str(local_project) if local_project else None,
                    "kind": kind,
                }
            )
        for field, child_kind in (("permissions", "permission"), ("roles", "role"), ("assignments", None)):
            if field in value:
                add(value[field], scope=local_scope, project_id=str(local_project) if local_project else None, kind=child_kind)
        # Compact certification fixtures often encode a set as
        # ``{"Observer": true, "Project Creator": true}`` rather than as a
        # list.  Treat only truthy named entries as permission facts; all
        # other metadata remains ignored.
        if not any(field in value for field in ("role", "permission", "name", "key", "permissions", "roles", "assignments")):
            for key, enabled in value.items():
                if enabled is True:
                    add(key, scope=local_scope, project_id=str(local_project) if local_project else None, kind=kind)

    if evidence is not None:
        if isinstance(evidence, dict):
            category_scope: dict[str, str | None] = {
                "global_roles": "global",
                "global_permissions": "global",
                "global": "global",
                "project_roles": "project",
                "project_permissions": "project",
                "project": "project",
            }
            category_kind: dict[str, str | None] = {
                "global_roles": "role",
                "project_roles": "role",
                "global_permissions": "permission",
                "project_permissions": "permission",
            }
            for key, value in evidence.items():
                if key in {"project_admin_projects", "managed_project_ids"}:
                    continue
                if key in {
                    "created_project",
                    "created_project_owner",
                    "created_project_ownership",
                    "automatic_project_admin_ownership",
                    "created_project_project_admin",
                    "project_owner",
                }:
                    continue
                if key in category_scope:
                    scope = category_scope[key]
                    kind = category_kind.get(key)
                    if key in {"project_roles", "project_permissions", "project"} and isinstance(value, dict):
                        for project_id, project_values in value.items():
                            add(project_values, scope=scope, project_id=str(project_id), kind=kind)
                    else:
                        add(value, scope=scope, kind=kind)
                else:
                    add(value)
        else:
            add(evidence)

    global_records = [record for record in records if record["scope"] in {None, "global"}]
    project_records = [record for record in records if record["scope"] == "project"]
    global_names = {str(record["name"]) for record in global_records if record.get("name")}
    project_names = {str(record["name"]) for record in project_records if record.get("name")}

    # Project Creator is the role that grants Create Project.  Keep the
    # permission visible in the report even when the snapshot contains only
    # assigned roles, while still requiring the role itself.
    if "project_creator" in global_names:
        global_names.add("create_project")

    project_admin_projects = {
        str(value)
        for value in (evidence.get("project_admin_projects", []) if isinstance(evidence, dict) else [])
        if value
    }
    wildcard_project_admin = False
    for record in project_records:
        if record.get("name") == "project_admin":
            if record.get("project_id"):
                project_admin_projects.add(str(record["project_id"]))
            else:
                wildcard_project_admin = True

    missing: list[str] = []
    if "observer" not in global_names:
        missing.append("Global Observer")
    if "project_creator" not in global_names:
        missing.append("Global Project Creator")
    if "create_project" not in global_names:
        missing.append("Create Project")
    missing_projects = [project_id for project_id in managed_project_ids if project_id not in project_admin_projects and not wildcard_project_admin]
    if missing_projects:
        missing.append(f"Project Admin on AIAT-managed projects: {', '.join(sorted(missing_projects))}")

    ownership = False
    if isinstance(evidence, dict):
        for key in (
            "created_project_owner",
            "created_project_ownership",
            "automatic_project_admin_ownership",
            "created_project_project_admin",
            "project_owner",
        ):
            if evidence.get(key) is True:
                ownership = True
        created_project = evidence.get("created_project")
        if isinstance(created_project, dict) and any(
            created_project.get(key) is True
            for key in (
                "owned_by_integration_user",
                "owner_is_integration_user",
                "created_by_integration_user",
                "project_admin",
                "projectAdmin",
                "project_admin_granted",
                "automatic_project_admin",
            )
        ):
            ownership = True
    if not ownership:
        missing.append("automatic Project Admin ownership on integration-created projects")

    forbidden = sorted(name for name in global_names | project_names if name in _YOUTRACK_FORBIDDEN_KEYS)
    if isinstance(evidence, dict) and any(
        evidence.get(key) is True for key in ("delete_project", "allow_project_delete", "project_deletion_allowed")
    ):
        forbidden.append("delete_project")
    forbidden = sorted(set(forbidden))

    return {
        "ok": not missing and not forbidden,
        "expected": {
            "global_roles": list(YOUTRACK_EXPECTED_GLOBAL_ROLES),
            "global_permissions": list(YOUTRACK_EXPECTED_GLOBAL_PERMISSIONS),
            "project_roles": list(YOUTRACK_EXPECTED_PROJECT_ROLES),
            "project_admin_projects": list(managed_project_ids),
            "automatic_created_project_ownership": True,
            "project_deletion": "prohibited; archive/deactivation only",
        },
        "observed": {
            "global": sorted(global_names),
            "project": sorted(project_names),
            "project_admin_projects": sorted(project_admin_projects),
            "automatic_created_project_ownership": ownership,
        },
        "missing": missing,
        "forbidden": forbidden,
        "deletion_policy": {
            "normal_automation": "archive_or_deactivate",
            "permanent_delete": "explicit_operator_approval_required",
        },
    }


class YouTrackProvider:
    kind = "youtrack"
    adapter_version = "1"

    def __init__(self, resolver: CredentialResolver | None = None, *, timeout: float = 20.0) -> None:
        self.http = ProviderHTTP(resolver, timeout=timeout)
        self.resolver = resolver

    def _api_path(self, path: str) -> str:
        return "/api" + (path if path.startswith("/") else "/" + path)

    @staticmethod
    def _safe_segment(value: str, *, field: str) -> str:
        """Validate an opaque YouTrack identifier before path interpolation."""
        segment = str(value or "")
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        if (
            not segment
            or len(segment) > 240
            or any(char not in allowed for char in segment)
            or segment.startswith((".", "-"))
            or ".." in segment
        ):
            raise ValueError(f"{field} must be a safe provider identifier")
        return segment

    async def health(self, connection: ProviderConnection) -> dict[str, object]:
        response = await self.http.request(connection, "GET", self._api_path("users/me?fields=id,login"))
        value = response_json(response)
        return {"ok": True, "provider": self.kind, "identity": value}

    async def resolve_external_actor(
        self,
        connection: ProviderConnection,
        *,
        login: str | None = None,
        email: str | None = None,
    ) -> dict[str, str]:
        """Resolve a webhook actor to YouTrack's immutable user ID.

        Login/email are lookup hints corroborated against the authenticated
        provider response; callers must persist only the returned ``id`` as an
        authorization key.  Ambiguous or incomplete results fail closed.
        """
        if not login and not email:
            raise ValueError("a provider actor login or email observation is required")
        query = str(login or email or "").strip()
        response = await self.http.request(
            connection,
            "GET",
            self._api_path("users"),
            params={"query": query, "fields": "id,login,email,fullName", "$top": 20},
        )
        value = response_json(response)
        if not isinstance(value, list):
            raise RuntimeError("YouTrack user lookup returned an unsupported response")
        matches = [
            item for item in value
            if isinstance(item, dict)
            and item.get("id")
            and (not login or str(item.get("login") or "") == str(login))
            and (not email or str(item.get("email") or "").lower() == str(email).lower())
        ]
        if len(matches) != 1:
            raise ValueError("YouTrack actor lookup was ambiguous or did not corroborate webhook evidence")
        actor = matches[0]
        return {
            "id": str(actor["id"]),
            "login": str(actor.get("login") or ""),
            "email": str(actor.get("email") or ""),
            "full_name": str(actor.get("fullName") or ""),
        }

    async def capabilities(self, connection: ProviderConnection) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider_kind=self.kind,
            adapter_version=self.adapter_version,
            work_management=True,
            projects=True,
            iterations=True,
            work_items=True,
            comments=True,
            links=True,
            webhooks=True,
            incremental_sync=True,
            supported_fields=frozenset({"title", "description", "status", "priority", "sprint_id", "assigned_user"}),
        )

    async def discover(self, connection: ProviderConnection) -> dict[str, object]:
        projects_response = await self.http.request(
            connection,
            "GET",
            self._api_path("admin/projects"),
            params={"fields": "id,shortName,name,archived,createdBy(id,login),leader(id,login)", "$top": 100},
        )
        projects = response_json(projects_response)
        return {"provider": self.kind, "projects": projects if isinstance(projects, list) else []}

    @staticmethod
    def _permission_evidence(connection: ProviderConnection) -> Any:
        """Return redacted operator/live-certification evidence from config."""
        config = connection.config or {}
        return (
            config.get("permission_evidence")
            if config.get("permission_evidence") is not None
            else config.get("least_privilege_evidence")
            if config.get("least_privilege_evidence") is not None
            else config.get("permission_snapshot")
            if config.get("permission_snapshot") is not None
            else config.get("permissions")
        )

    async def verify_least_privilege(self, connection: ProviderConnection) -> dict[str, object]:
        """Certify the intentional YouTrack role boundary for this connection.

        Permission evidence is metadata, not a credential.  Requiring it in
        the doctor makes a live staging denial test an activation gate without
        asking the integration account for any global administration endpoint.
        """
        evidence = self._permission_evidence(connection)
        if evidence is None:
            return {
                "ok": False,
                "missing": ["permission_evidence (live YouTrack least-privilege certification)"],
                "forbidden": [],
                "expected": {
                    "global_roles": list(YOUTRACK_EXPECTED_GLOBAL_ROLES),
                    "global_permissions": list(YOUTRACK_EXPECTED_GLOBAL_PERMISSIONS),
                    "project_roles": list(YOUTRACK_EXPECTED_PROJECT_ROLES),
                    "project_deletion": "prohibited; archive/deactivation only",
                },
                "observed": {},
            }
        configured_ids: list[str] = []
        configured_project = connection.config.get("project_id")
        if configured_project:
            configured_ids.append(str(configured_project))
        configured_ids.extend(
            str(value)
            for value in (connection.config.get("managed_project_ids") or [])
            if value and str(value) not in configured_ids
        )
        report = _youtrack_permission_report(evidence, managed_project_ids=configured_ids)
        report["evidence_source"] = "connection.config (operator-attached redacted certification evidence)"
        return report

    async def verify_configuration(self, connection: ProviderConnection) -> dict[str, object]:
        configured_project = str(connection.config.get("project_id") or "")
        project_id = self._safe_segment(configured_project, field="project_id") if configured_project else None
        if not (connection.config.get("webhook_secret_ref") or connection.config.get("webhook_secret_refs") or connection.config.get("webhook_token_test_only")):
            raise ValueError("YouTrack webhook_secret_ref is required")
        discovered = await self.discover(connection)
        projects = discovered.get("projects") if isinstance(discovered, dict) else None
        if project_id and isinstance(projects, list) and projects and not any(str(item.get("id")) == project_id for item in projects if isinstance(item, dict)):
            raise ValueError(f"YouTrack project {project_id!r} was not found in the integration scope")
        return {
            "ok": True,
            "project_id": project_id,
            "discovered_projects": len(projects or []),
        }

    async def plan_bootstrap(self, connection: ProviderConnection, desired: dict[str, object]) -> BootstrapPlan:
        discovered = await self.discover(connection)
        actions: list[BootstrapAction] = []
        blockers: list[str] = []
        requested_project = str(desired.get("project_id") or "") or None
        if requested_project:
            requested_project = self._safe_segment(requested_project, field="desired.project_id")
            actions.append(
                BootstrapAction(
                    action="adopt_project",
                    resource=f"youtrack:project:{requested_project}",
                    desired={"project_id": str(requested_project), "project_admin": True},
                    reason="existing AIAT-managed projects require Project Admin access",
                )
            )
        else:
            project_name = str(desired.get("project_name") or "")
            project_short_name = str(desired.get("project_short_name") or "")
            if not project_name or not project_short_name:
                blockers.append(
                    "desired.project_name and desired.project_short_name are required when creating an AIAT project"
                )
            else:
                project_short_name = self._safe_segment(project_short_name, field="desired.project_short_name")
                actions.append(
                    BootstrapAction(
                        action="create_project",
                        resource=f"youtrack:project:{project_short_name}",
                        desired={
                            "name": project_name,
                            "short_name": project_short_name,
                            "owner": "integration_user",
                            "project_admin": True,
                        },
                        reason="Create Project grants the integration user automatic Project Admin ownership",
                    )
                )
        actions.extend(
            BootstrapAction(
                action="adopt_or_create_field",
                resource=f"youtrack:field:{name}",
                desired={"name": name, "type": "string" if name != "AIAT Revision" else "integer"},
                reason="stable AIAT mapping marker; compatible fields are adopted by name",
            )
            for name in ("AIAT Object ID", "AIAT Object Type", "AIAT Revision", "AIAT Managed")
        )
        actions.append(
            BootstrapAction(
                action="configure_webhook",
                resource=f"youtrack:webhook:{requested_project or 'unselected'}",
                desired={"events": ["issue", "comment"], "header": desired.get("webhook_header", "X-YouTrack-Token")},
                manual=True,
                reason="Operator configures Webhook Triggers; the runtime identity only validates its token",
            )
        )
        if not isinstance(discovered.get("projects"), list):
            blockers.append("YouTrack project discovery returned an unsupported response")
        evidence = self._permission_evidence(connection)
        if evidence is not None:
            permission_report = await self.verify_least_privilege(connection)
            if not permission_report.get("ok"):
                blockers.extend(str(item) for item in permission_report.get("missing", []))
                blockers.extend(f"forbidden permission: {item}" for item in permission_report.get("forbidden", []))
        return BootstrapPlan(
            connection_id=connection.id,
            provider_kind=self.kind,
            actions=actions,
            blockers=blockers,
            checks=[
                "Global Observer",
                "Global Project Creator",
                "Create Project",
                "Project Admin on existing AIAT-managed projects",
                "automatic Project Admin ownership on integration-created projects",
                "forbidden global administration denied",
                "project deletion denied; archive/deactivation only",
                "restricted integration identity",
                "REST read/write",
                "webhook token",
                "mapping uniqueness",
            ],
            rollback_actions=["disable binding", "disable webhook", "archive/deactivate provider projects", "retain external resources"],
        )

    @classmethod
    def project_short_name_for_canonical(cls, project: CanonicalProject) -> str:
        """Build a deterministic, collision-resistant YouTrack short name.

        The canonical UUID suffix is intentional: names are user-controlled
        and are not a safe uniqueness key.  The result uses only the
        characters accepted by YouTrack project identifiers and is kept well
        below the provider's length limit.
        """
        slug = re.sub(r"[^A-Za-z0-9]+", "-", project.name.upper()).strip("-")
        slug = slug[:32] or "PROJECT"
        suffix = project.id.hex[:10].upper()
        candidate = f"AIAT-{slug}-{suffix}"
        return cls._safe_segment(candidate[:64], field="generated_project_short_name")

    async def plan_project_provisioning(
        self,
        connection: ProviderConnection,
        project: CanonicalProject,
        *,
        mapping_profile: str = DEDICATED_PROJECT_MAPPING_PROFILE,
        external_project_id: str | None = None,
    ) -> ProjectProvisioningPlan:
        """Plan one canonical project's provider project and stable fields.

        Planning is read-only.  Webhook Triggers attachment is represented as
        a manual action because the approved integration role intentionally
        does not receive app-administration permission.  That action blocks
        activation but does not prevent safe project/field creation.
        """
        profile = normalize_project_mapping_profile(mapping_profile)
        discovered = await self.discover(connection)
        visible = [item for item in (discovered.get("projects") or []) if isinstance(item, dict)]
        blockers: list[str] = []
        manual_actions: list[str] = []
        actions: list[BootstrapAction] = []
        project_id: str | None = None
        project_key: str | None = None

        if profile == UMBRELLA_ISSUES_MAPPING_PROFILE:
            # This path is deliberately explicit.  It is useful for an
            # operator who consciously wants issue-only mapping, but it is
            # never selected by the default canonical-project flow.
            selected = str(external_project_id or connection.config.get("project_id") or "")
            if not selected:
                blockers.append("umbrella_issues requires an explicit external project selector")
            else:
                selected = self._safe_segment(selected, field="external_project_id")
                match = next((item for item in visible if str(item.get("id") or "") == selected), None)
                if match is None:
                    blockers.append(f"external project {selected!r} is outside the connection discovery scope")
                else:
                    project_id = selected
                    project_key = str(match.get("shortName") or "") or None
                    actions.append(
                        BootstrapAction(
                            action="adopt_project",
                            resource=f"youtrack:project:{selected}",
                            desired={"project_id": selected, "project_admin": True},
                            current={"project_ids": sorted(str(item.get("id")) for item in visible if item.get("id"))},
                            reason="explicit umbrella_issues mapping profile",
                        )
                    )
        else:
            generated_key = self.project_short_name_for_canonical(project)
            requested_id = self._safe_segment(str(external_project_id), field="external_project_id") if external_project_id else None
            if requested_id:
                match = next((item for item in visible if str(item.get("id") or "") == requested_id), None)
                if match is None:
                    blockers.append(f"external project {requested_id!r} is outside the connection discovery scope")
                else:
                    project_id = requested_id
                    project_key = str(match.get("shortName") or generated_key)
                    actions.append(
                        BootstrapAction(
                            action="adopt_project",
                            resource=f"youtrack:project:{requested_id}",
                            desired={"project_id": requested_id, "project_admin": True},
                            current={"project_ids": sorted(str(item.get("id")) for item in visible if item.get("id"))},
                            reason="operator-selected provider project for this canonical project",
                        )
                    )
            else:
                candidates = [
                    item for item in visible
                    if self._project_name_matches(item, name=project.name, short_name=generated_key)
                ]
                if any(str(item.get("shortName") or "").upper() == generated_key.upper() for item in visible) and not candidates:
                    blockers.append(f"generated YouTrack short name {generated_key!r} is already used by another project")
                if len(candidates) > 1:
                    blockers.append("duplicate provider projects match the canonical project and generated short name")
                elif candidates:
                    project_id = str(candidates[0].get("id") or "") or None
                    project_key = str(candidates[0].get("shortName") or generated_key)
                    actions.append(
                        BootstrapAction(
                            action="adopt_project",
                            resource=f"youtrack:project:{project_key}",
                            desired={"project_id": project_id, "project_admin": True},
                            current={"project_ids": sorted(str(item.get("id")) for item in visible if item.get("id"))},
                            reason="matching provider project already exists",
                        )
                    )
                else:
                    project_key = generated_key
                    actions.append(
                        BootstrapAction(
                            action="create_project",
                            resource=f"youtrack:project:{generated_key}",
                            desired={
                                "name": project.name,
                                "short_name": generated_key,
                                "owner": "integration_user",
                                "project_admin": True,
                            },
                            # Existing unrelated projects are expected for
                            # future provisioning; the apply step rejects
                            # only changes to this discovery snapshot.
                            current={"project_ids": sorted(str(item.get("id")) for item in visible if item.get("id"))},
                            reason="one provider project per canonical project",
                        )
                    )

        # Stable fields are attached to every dedicated provider project.  An
        # umbrella profile still attaches/adopts them on the explicitly
        # selected project so mappings remain portable.
        actions.extend(
            BootstrapAction(
                action="adopt_or_create_field",
                resource=f"youtrack:field:{name}",
                desired={"name": name, "type": "string" if name != "AIAT Revision" else "integer"},
                reason="stable AIAT mapping marker",
            )
            for name in AIAT_STABLE_PROJECT_FIELDS
        )
        webhook_project = project_id or project_key or "pending"
        header = str(connection.config.get("webhook_header") or "X-YouTrack-Token")
        configured_endpoint = str(connection.config.get("webhook_endpoint") or "").rstrip("/")
        if configured_endpoint:
            webhook_endpoint = configured_endpoint
        else:
            gateway_base = str(
                connection.config.get("pm_gateway_public_url")
                or os.getenv("PM_GATEWAY_PUBLIC_URL", "")
            ).rstrip("/")
            webhook_endpoint = (
                f"{gateway_base}/webhooks/{connection.id}" if gateway_base else f"/webhooks/{connection.id}"
            )
        manual_actions.append(
            f"Attach Webhook Triggers to YouTrack project {webhook_project} for issue and comment events; POST to {webhook_endpoint}, configure {header}, and use the managed webhook secret reference"
        )
        actions.append(
            BootstrapAction(
                action="configure_webhook",
                resource=f"youtrack:webhook:{webhook_project}",
                desired={"project_id": project_id, "project_short_name": project_key, "events": ["issue", "comment"], "header": header, "endpoint": webhook_endpoint},
                manual=True,
                reason="app attachment is outside the restricted integration account permissions",
            )
        )
        evidence = self._permission_evidence(connection)
        if evidence is not None:
            permission_report = await self.verify_least_privilege(connection)
            if not permission_report.get("ok"):
                blockers.extend(str(item) for item in permission_report.get("missing", []))
                blockers.extend(f"forbidden permission: {item}" for item in permission_report.get("forbidden", []))
        return ProjectProvisioningPlan(
            connection_id=connection.id,
            project_id=project.id,
            provider_kind=self.kind,
            mapping_profile=profile,
            external_project_id=project_id,
            external_project_key=project_key,
            actions=actions,
            blockers=blockers,
            manual_actions=manual_actions,
            checks=[
                "one dedicated YouTrack project per canonical project by default",
                "unique valid YouTrack short name",
                "AIAT Object ID",
                "AIAT Object Type",
                "AIAT Revision",
                "AIAT Managed",
                "issue webhook coverage",
                "comment webhook coverage",
                "Global Observer",
                "Global Project Creator",
                "Create Project",
                "Project Admin on existing AIAT-managed projects",
                "automatic Project Admin ownership on integration-created projects",
                "forbidden global administration denied",
                "project deletion denied; archive/deactivation only",
            ],
            rollback_actions=[
                "disable or drain the binding",
                "disable the webhook app attachment",
                "archive/deactivate the provider project",
                "retain the provider project and mappings for re-adoption",
            ],
        )

    async def apply_project_provisioning(
        self,
        connection: ProviderConnection,
        plan: ProjectProvisioningPlan,
    ) -> ProjectProvisioningApplyResult:
        """Apply only the safe project/field portion of a project plan."""
        if plan.connection_id != connection.id or plan.provider_kind != self.kind:
            raise ValueError("project provisioning plan does not belong to this YouTrack connection")
        if not plan.ready_to_apply:
            raise ValueError("project provisioning plan has blockers or destructive actions")
        bootstrap = BootstrapPlan(
            plan_id=plan.plan_id,
            connection_id=plan.connection_id,
            provider_kind=plan.provider_kind,
            actions=plan.actions,
            blockers=plan.blockers,
            checks=plan.checks,
            rollback_actions=plan.rollback_actions,
        )
        applied = await self.apply_bootstrap(connection, bootstrap)
        return ProjectProvisioningApplyResult(plan=plan, created=applied.created, adopted=applied.adopted)

    async def _list_global_custom_fields(self, connection: ProviderConnection) -> list[dict[str, Any]]:
        """Read global field prototypes without changing provider state."""
        fields: list[dict[str, Any]] = []
        skip = 0
        # YouTrack Cloud caps collection pages at 42 entities.  Continue until
        # a short page so a pre-existing AIAT field is never duplicated merely
        # because it falls beyond the first page.
        while True:
            response = await self.http.request(
                connection,
                "GET",
                self._api_path("admin/customFieldSettings/customFields"),
                params={
                    "fields": "id,name,fieldType(id,valueType,isMultiValue)",
                    "$top": 42,
                    "$skip": skip,
                },
            )
            value = response_json(response)
            page = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
            fields.extend(page)
            if len(page) < 42:
                break
            skip += len(page)
        return fields

    async def _list_project_custom_fields(
        self,
        connection: ProviderConnection,
        project_id: str,
    ) -> list[dict[str, Any]]:
        target = self._safe_segment(project_id, field="project_id")
        response = await self.http.request(
            connection,
            "GET",
            self._api_path(f"admin/projects/{target}/customFields"),
            params={
                "fields": "id,field(id,name,fieldType(id,valueType,isMultiValue)),project(id,shortName),canBeEmpty,isPublic",
                "$top": 42,
            },
        )
        value = response_json(response)
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _field_value_type(field: dict[str, Any]) -> str | None:
        field_type = field.get("fieldType")
        if not isinstance(field_type, dict):
            return None
        value_type = field_type.get("valueType")
        if value_type:
            return str(value_type)
        identifier = str(field_type.get("id") or "")
        return identifier.split("[", 1)[0] or None

    @classmethod
    def _field_type_matches(cls, field: dict[str, Any], expected: str) -> bool:
        return cls._field_value_type(field) == expected

    @staticmethod
    def _project_name_matches(project: dict[str, Any], *, name: str, short_name: str) -> bool:
        return (
            str(project.get("name") or "") == name
            and str(project.get("shortName") or "").upper() == short_name.upper()
        )

    async def _create_project_for_bootstrap(
        self,
        connection: ProviderConnection,
        *,
        name: str,
        short_name: str,
    ) -> dict[str, Any]:
        identity = await self.health(connection)
        identity_value = identity.get("identity")
        owner_id = str(identity_value.get("id") or "") if isinstance(identity_value, dict) else ""
        owner_id = self._safe_segment(owner_id, field="integration_user_id")
        try:
            response = await self.http.request(
                connection,
                "POST",
                self._api_path("admin/projects"),
                params={"fields": "id,name,shortName,archived,createdBy(id,login),leader(id,login)"},
                json_body={
                    "name": name,
                    "shortName": short_name,
                    "leader": {"id": owner_id},
                },
                headers={"Idempotency-Key": f"aiat-bootstrap-project-{short_name}"},
            )
            value = response_json(response)
            if not isinstance(value, dict):
                raise RuntimeError("YouTrack project creation returned an unsupported response")
            return value
        except ProviderRequestError as exc:
            # A retry after a network failure or concurrent operator run may
            # receive a conflict even though the provider committed the
            # project.  Re-read and adopt the exact target only; all other
            # conflicts remain hard failures.
            if exc.status_code not in {400, 409}:
                raise RuntimeError(f"YouTrack project creation failed with status {exc.status_code}") from exc
            discovered = await self.discover(connection)
            candidates = [
                item
                for item in (discovered.get("projects") if isinstance(discovered, dict) else [])
                if isinstance(item, dict) and self._project_name_matches(item, name=name, short_name=short_name)
            ]
            if len(candidates) == 1:
                return candidates[0]
            raise RuntimeError(f"YouTrack project creation failed with status {exc.status_code}") from exc

    async def apply_bootstrap(self, connection: ProviderConnection, plan: BootstrapPlan) -> BootstrapApplyResult:
        """Apply only project/field actions from an approved, non-destructive plan.

        Webhooks remain manual by design.  The method re-reads provider scope
        before mutating anything, rejects unrelated drift, and treats every
        create/attach operation as an adopt-or-create action so retries are
        safe after partial network failures.
        """
        if not plan.ready_to_apply:
            raise ValueError("bootstrap plan has blockers or destructive actions")
        if plan.connection_id != connection.id or plan.provider_kind != self.kind:
            raise ValueError("bootstrap plan does not belong to this YouTrack connection")

        discovered = await self.discover(connection)
        visible_projects = discovered.get("projects") if isinstance(discovered, dict) else None
        if not isinstance(visible_projects, list):
            raise ValueError("YouTrack project discovery returned an unsupported response")
        projects = [item for item in visible_projects if isinstance(item, dict)]
        created: list[dict[str, Any]] = []
        adopted: list[dict[str, Any]] = []

        project_action = next(
            (action for action in plan.actions if action.resource.startswith("youtrack:project:")),
            None,
        )
        project: dict[str, Any] | None = None
        if project_action is not None:
            desired = project_action.desired
            if project_action.action == "adopt_project":
                project_id = self._safe_segment(str(desired.get("project_id") or ""), field="desired.project_id")
                project = next((item for item in projects if str(item.get("id") or "") == project_id), None)
                if project is None:
                    raise ValueError(f"YouTrack project {project_id!r} is no longer in the integration scope")
                adopted.append({
                    "resource": project_action.resource,
                    "external_id": project_id,
                    "name": project.get("name"),
                    "short_name": project.get("shortName"),
                    "project_admin": bool(desired.get("project_admin")),
                })
            elif project_action.action == "create_project":
                name = str(desired.get("name") or "")
                short_name = self._safe_segment(str(desired.get("short_name") or ""), field="desired.short_name")
                planned_project_ids = project_action.current.get("project_ids") if isinstance(project_action.current, dict) else None
                if planned_project_ids is not None:
                    planned_ids = {str(value) for value in planned_project_ids if value}
                    visible_ids = {str(item.get("id")) for item in projects if item.get("id")}
                    if visible_ids != planned_ids:
                        raise ValueError("YouTrack provider drift: project discovery changed since planning")
                candidates = [
                    item for item in projects if self._project_name_matches(item, name=name, short_name=short_name)
                ]
                if len(candidates) > 1:
                    raise ValueError("YouTrack provider drift: duplicate AIAT project candidates")
                if candidates:
                    if planned_project_ids is None and any(
                        not self._project_name_matches(item, name=name, short_name=short_name)
                        for item in projects
                    ):
                        raise ValueError("YouTrack provider drift: visible projects changed since planning")
                    project = candidates[0]
                    adopted.append({
                        "resource": project_action.resource,
                        "external_id": str(project.get("id") or ""),
                        "name": project.get("name"),
                        "short_name": project.get("shortName"),
                        "project_admin": bool(desired.get("project_admin")),
                    })
                else:
                    # The approved plan was generated with an empty visible
                    # scope.  Do not silently absorb an unrelated project.
                    if projects and planned_project_ids is None:
                        raise ValueError("YouTrack provider drift: visible projects changed since planning")
                    project = await self._create_project_for_bootstrap(
                        connection,
                        name=name,
                        short_name=short_name,
                    )
                    created.append({
                        "resource": project_action.resource,
                        "external_id": str(project.get("id") or ""),
                        "name": project.get("name") or name,
                        "short_name": project.get("shortName") or short_name,
                        "project_admin": bool(desired.get("project_admin")),
                    })
            else:
                raise ValueError(f"unsupported YouTrack bootstrap project action {project_action.action!r}")

        if project is None or not project.get("id"):
            raise ValueError("YouTrack bootstrap did not produce a project id")
        project_id = self._safe_segment(str(project["id"]), field="project_id")

        # Read global prototypes only after the project exists.  YouTrack
        # permits Project Admins with Update Project to create/reuse these
        # prototypes, while the same endpoint is intentionally forbidden to
        # the integration user before it owns an AIAT project.
        global_fields = await self._list_global_custom_fields(connection)
        project_fields = await self._list_project_custom_fields(connection, project_id)
        for field_action in (
            action for action in plan.actions if action.resource.startswith("youtrack:field:")
        ):
            desired = field_action.desired
            name = str(desired.get("name") or "")
            expected_type = str(desired.get("type") or "")
            attached = [
                item for item in project_fields
                if isinstance(item.get("field"), dict)
                and str(item["field"].get("name") or "") == name
            ]
            if len(attached) > 1:
                raise ValueError(f"YouTrack provider drift: duplicate project custom field {name!r}")
            if attached:
                project_field = attached[0]
                field = project_field.get("field") or {}
                if not self._field_type_matches(field, expected_type):
                    raise ValueError(f"YouTrack custom field {name!r} has an incompatible type")
                adopted.append({
                    "resource": field_action.resource,
                    "external_id": str(project_field.get("id") or ""),
                    "global_field_id": str(field.get("id") or ""),
                    "name": name,
                    "type": expected_type,
                })
                continue

            prototypes = [item for item in global_fields if str(item.get("name") or "") == name]
            if len(prototypes) > 1:
                raise ValueError(f"YouTrack provider drift: duplicate global custom field {name!r}")
            prototype = prototypes[0] if prototypes else None
            if prototype is not None and not self._field_type_matches(prototype, expected_type):
                raise ValueError(f"YouTrack custom field {name!r} has an incompatible type")
            if prototype is None:
                try:
                    response = await self.http.request(
                        connection,
                        "POST",
                        self._api_path("admin/customFieldSettings/customFields"),
                        params={"fields": "id,name,fieldType(id,valueType,isMultiValue)"},
                        json_body={
                            "name": name,
                            "fieldType": {"id": expected_type},
                            "isAutoAttached": False,
                            "isDisplayedInIssueList": False,
                            "isPublic": True,
                        },
                        headers={"Idempotency-Key": f"aiat-bootstrap-field-{expected_type}-{name}"},
                    )
                    value = response_json(response)
                    prototype = value if isinstance(value, dict) else None
                except ProviderRequestError as exc:
                    if exc.status_code not in {400, 409}:
                        raise RuntimeError(f"YouTrack custom field creation failed with status {exc.status_code}") from exc
                    refreshed = await self._list_global_custom_fields(connection)
                    matches = [item for item in refreshed if str(item.get("name") or "") == name]
                    if len(matches) == 1:
                        prototype = matches[0]
                    else:
                        raise RuntimeError(f"YouTrack custom field creation failed with status {exc.status_code}") from exc
                if prototype is None or not prototype.get("id"):
                    raise RuntimeError(f"YouTrack custom field {name!r} creation returned no id")
                global_fields.append(prototype)

            global_id = self._safe_segment(str(prototype.get("id") or ""), field="custom_field_id")
            try:
                response = await self.http.request(
                    connection,
                    "POST",
                    self._api_path(f"admin/projects/{project_id}/customFields"),
                    params={"fields": "id,field(id,name,fieldType(id,valueType,isMultiValue)),project(id,shortName),canBeEmpty,isPublic"},
                    json_body={
                        "field": {"id": global_id},
                        "$type": "SimpleProjectCustomField",
                        "canBeEmpty": True,
                        "isPublic": True,
                    },
                    headers={"Idempotency-Key": f"aiat-bootstrap-attach-{project_id}-{global_id}"},
                )
                attached_value = response_json(response)
                project_field = attached_value if isinstance(attached_value, dict) else None
            except ProviderRequestError as exc:
                if exc.status_code not in {400, 409}:
                    raise RuntimeError(f"YouTrack project custom field attach failed with status {exc.status_code}") from exc
                refreshed = await self._list_project_custom_fields(connection, project_id)
                matches = [
                    item for item in refreshed
                    if isinstance(item.get("field"), dict)
                    and str(item["field"].get("id") or "") == global_id
                ]
                project_field = matches[0] if len(matches) == 1 else None
                if project_field is None:
                    raise RuntimeError(f"YouTrack project custom field attach failed with status {exc.status_code}") from exc
            if project_field is None or not project_field.get("id"):
                raise RuntimeError(f"YouTrack custom field {name!r} attach returned no id")
            created.append({
                "resource": field_action.resource,
                "external_id": str(project_field["id"]),
                "global_field_id": global_id,
                "name": name,
                "type": expected_type,
            })
            project_fields.append(project_field)

        return BootstrapApplyResult(plan=plan, created=created, adopted=adopted)

    async def project_project(
        self,
        connection: ProviderConnection,
        project: CanonicalProject,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult:
        target_value = external_id or str(connection.config.get("project_id") or "")
        if target_value:
            target = self._safe_segment(target_value, field="project_id")
            path = self._api_path(f"admin/projects/{target}")
            body: dict[str, Any] = {"name": project.name, "description": project.description or ""}
        else:
            short_name = str(connection.config.get("project_short_name") or "")
            if not short_name:
                short_name = re.sub(r"[^A-Za-z0-9]+", "", project.name).upper()[:12]
            short_name = self._safe_segment(short_name, field="project_short_name")
            owner_id = str(connection.config.get("integration_user_id") or "")
            if not owner_id:
                identity = await self.health(connection)
                identity_value = identity.get("identity")
                if isinstance(identity_value, dict):
                    owner_id = str(identity_value.get("id") or "")
            owner_id = self._safe_segment(owner_id, field="integration_user_id")
            path = self._api_path("admin/projects")
            body = {
                "name": project.name,
                "shortName": short_name,
                "description": project.description or "",
                # YouTrack grants the creator Project Admin automatically.  We
                # explicitly make the integration account the leader so that
                # ownership remains deterministic for AIAT-created projects.
                "leader": {"id": owner_id},
            }
        response = await self.http.request(
            connection,
            "POST",
            path,
            json_body=body,
            headers={"Idempotency-Key": idempotency_key},
        )
        value = response_json(response)
        provider_id = target_value or (str(value.get("id")) if isinstance(value, dict) and value.get("id") else "")
        if not provider_id:
            raise RuntimeError("YouTrack project response did not include an id")
        return ProjectionResult(
            status=ProjectionStatus.SYNCED,
            connection_id=connection.id,
            object_type=ObjectType.PROJECT,
            aiat_object_id=project.id,
            external_id=provider_id,
            provider_version=str(value.get("updated") or "") if isinstance(value, dict) else None,
        )

    async def archive_project(
        self,
        connection: ProviderConnection,
        external_id: str,
        *,
        idempotency_key: str,
    ) -> ProjectionResult:
        """Deactivate a project; permanent provider deletion is never automated."""
        target = self._safe_segment(external_id, field="project_id")
        response = await self.http.request(
            connection,
            "POST",
            self._api_path(f"admin/projects/{target}"),
            json_body={"archived": True},
            headers={"Idempotency-Key": idempotency_key},
        )
        value = response_json(response)
        return ProjectionResult(
            status=ProjectionStatus.SYNCED,
            connection_id=connection.id,
            object_type=ObjectType.PROJECT,
            external_id=target,
            provider_version=str(value.get("updated") or "") if isinstance(value, dict) else None,
            message="project archived/deactivated; permanent deletion requires explicit operator approval",
        )

    async def project_iteration(
        self,
        connection: ProviderConnection,
        iteration: CanonicalIteration,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult:
        board = self._safe_segment(
            str(connection.config.get("agile_board_id") or ""),
            field="agile_board_id",
        )
        body = {"name": iteration.name or f"Sprint {iteration.number}", "goal": iteration.goal or ""}
        if external_id:
            external_id = self._safe_segment(external_id, field="external_id")
            path = self._api_path(f"agiles/{board}/sprints/{external_id}")
            method = "POST"
        else:
            path = self._api_path(f"agiles/{board}/sprints")
            method = "POST"
        response = await self.http.request(connection, method, path, json_body=body, headers={"Idempotency-Key": idempotency_key})
        value = response_json(response)
        provider_id = external_id or (str(value.get("id")) if isinstance(value, dict) and value.get("id") else None)
        if not provider_id:
            raise RuntimeError("YouTrack iteration response did not include an id")
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.SPRINT, aiat_object_id=iteration.id, external_id=provider_id, provider_version=str(value.get("version") or "") if isinstance(value, dict) else None)

    def _fields(self, item: CanonicalWorkItem) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = [
            {"name": "AIAT Object ID", "$type": "SimpleIssueCustomField", "value": str(item.id)},
            {"name": "AIAT Object Type", "$type": "SimpleIssueCustomField", "value": "work_item"},
            {"name": "AIAT Revision", "$type": "SimpleIssueCustomField", "value": item.revision},
            {"name": "AIAT Managed", "$type": "SimpleIssueCustomField", "value": "true"},
        ]
        return values

    async def project_work_item(self, connection: ProviderConnection, item: CanonicalWorkItem, *, external_id: str | None = None, idempotency_key: str) -> ProjectionResult:
        body = {"summary": item.title, "description": item.description or "", "customFields": self._fields(item)}
        if external_id:
            external_id = self._safe_segment(external_id, field="external_id")
            response = await self.http.request(connection, "POST", self._api_path(f"issues/{external_id}"), json_body=body, headers={"Idempotency-Key": idempotency_key}, params={"fields": "id,idReadable,updated"})
            external = external_id
        else:
            project_id = self._safe_segment(str(connection.config.get("project_id") or ""), field="project_id")
            response = await self.http.request(connection, "POST", self._api_path("issues"), json_body={**body, "project": {"id": project_id}}, headers={"Idempotency-Key": idempotency_key}, params={"fields": "id,idReadable,updated"})
            value = response_json(response)
            external = str(value.get("id") if isinstance(value, dict) else "")
        value = response_json(response)
        external_key = str(value.get("idReadable")) if isinstance(value, dict) and value.get("idReadable") else None
        version = str(value.get("updated")) if isinstance(value, dict) and value.get("updated") else None
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.WORK_ITEM, external_id=external, external_key=external_key, provider_version=version)

    async def read_work_item(self, connection: ProviderConnection, external_id: str) -> ExternalObject:
        external_id = self._safe_segment(external_id, field="external_id")
        response = await self.http.request(
            connection,
            "GET",
            self._api_path(f"issues/{external_id}"),
            params={"fields": "id,idReadable,summary,description,customFields(name,value(name)),updated,project(id)"},
        )
        value = response_json(response)
        if not isinstance(value, dict) or not value.get("id"):
            raise RuntimeError("YouTrack issue response was invalid")
        return ExternalObject(
            object_type=ObjectType.WORK_ITEM,
            external_id=str(value["id"]),
            external_key=value.get("idReadable"),
            title=value.get("summary"),
            description=value.get("description"),
            priority=self._priority_from_custom_fields(value.get("customFields")),
            project_external_id=str((value.get("project") or {}).get("id")) if isinstance(value.get("project"), dict) else None,
            provider_version=str(value.get("updated") or "") or None,
        )

    @staticmethod
    def _priority_from_custom_fields(fields: Any) -> str | None:
        """Extract YouTrack's Priority custom field without assuming a schema."""
        if not isinstance(fields, list):
            return None
        for field in fields:
            if not isinstance(field, dict) or str(field.get("name") or "").strip().lower() != "priority":
                continue
            value = field.get("value")
            if isinstance(value, dict):
                return str(value.get("name") or value.get("presentation") or "") or None
            if value is not None:
                return str(value)
        return None

    async def archive_work_item(self, connection: ProviderConnection, external_id: str, *, idempotency_key: str) -> ProjectionResult:
        external_id = self._safe_segment(external_id, field="external_id")
        raise NotImplementedError("YouTrack has no issue archive operation; use an approved workflow state")

    async def list_projects(self, connection: ProviderConnection, *, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        discovered = await self.discover(connection)
        values = discovered.get("projects") if isinstance(discovered, dict) else []
        objects = [
            ExternalObject(
                object_type=ObjectType.PROJECT,
                external_id=str(item.get("id")),
                external_key=item.get("shortName"),
                title=item.get("name"),
                status="archived" if item.get("archived") else "active",
            )
            for item in values
            if isinstance(item, dict) and item.get("id")
        ]
        return objects, str(len(objects))

    async def list_iterations(self, connection: ProviderConnection, *, project_external_id: str | None = None, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        board = str(connection.config.get("agile_board_id") or "")
        if not board:
            return [], cursor
        board = self._safe_segment(board, field="agile_board_id")
        response = await self.http.request(connection, "GET", self._api_path(f"agiles/{board}/sprints"), params={"fields": "id,name,goal,archived,version"})
        value = response_json(response)
        if not isinstance(value, list):
            return [], cursor
        objects = [ExternalObject(object_type=ObjectType.SPRINT, external_id=str(item.get("id")), title=item.get("name"), description=item.get("goal"), status="archived" if item.get("archived") else "planned", provider_version=str(item.get("version") or "") or None) for item in value if isinstance(item, dict) and item.get("id")]
        return objects, str(len(objects))

    async def project_comment(self, connection: ProviderConnection, *, external_id: str, body: str, idempotency_key: str) -> ProjectionResult:
        external_id = self._safe_segment(external_id, field="external_id")
        response = await self.http.request(connection, "POST", self._api_path(f"issues/{external_id}/comments"), json_body={"text": body}, headers={"Idempotency-Key": idempotency_key})
        value = response_json(response)
        comment_id = str(value.get("id")) if isinstance(value, dict) else None
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.COMMENT, external_id=comment_id)

    async def project_link(self, connection: ProviderConnection, *, external_id: str, link: dict[str, object], idempotency_key: str) -> ProjectionResult:
        external_id = self._safe_segment(external_id, field="external_id")
        link_type = str(link.get("link_type") or "relates to")
        target = str(link.get("target_id") or "")
        if not target:
            raise ValueError("YouTrack link target_id is required")
        target = self._safe_segment(target, field="target_id")
        response = await self.http.request(
            connection,
            "POST",
            self._api_path(f"issues/{external_id}/links"),
            json_body={"linkType": {"name": link_type}, "issues": [{"id": target}]},
            headers={"Idempotency-Key": idempotency_key},
        )
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.WORK_ITEM, external_id=external_id, provider_version=response.headers.get("ETag"))

    async def list_changes(self, connection: ProviderConnection, *, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        params = {"fields": "id,idReadable,summary,description,updated,project(id)", "$top": 100}
        query_parts: list[str] = []
        # YouTrack's search language expects the project short name inside
        # braces (for example ``project: {AIAT}``), not the REST id ``0-1``.
        # Bootstrap stores both selectors; prefer the short name and retain
        # the id only as a legacy fallback for providers that accept it.
        configured_project = str(
            connection.config.get("project_short_name")
            or connection.config.get("project_id")
            or ""
        )
        if configured_project:
            configured_project = self._safe_segment(configured_project, field="project_id")
            query_parts.append(f"project: {{{configured_project}}}")
        cursor_millis: int | None = None
        if cursor:
            # YouTrack accepts a calendar date for the ``updated`` search
            # field, while its REST ``updated`` value/cursor is epoch
            # milliseconds.  Query from that UTC date (inclusive), then
            # filter the page below by the durable millisecond cursor so a
            # repeated date scan cannot manufacture duplicates or skip data.
            try:
                cursor_millis = int(str(cursor))
                from datetime import datetime, timezone

                cursor_date = datetime.fromtimestamp(cursor_millis / 1000, tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                cursor_date = str(cursor).split("T", 1)[0]
            query_parts.append(f"updated: {{{cursor_date}}} .. *")
        if query_parts:
            params["query"] = " ".join(query_parts)
        objects: list[ExternalObject] = []
        page_size = 100
        offset = 0
        while True:
            page_params = {**params, "$skip": offset}
            response = await self.http.request(connection, "GET", self._api_path("issues"), params=page_params)
            value = response_json(response)
            if not isinstance(value, list):
                return objects, cursor
            for item in value:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if cursor_millis is not None and item.get("updated") is not None:
                    try:
                        if int(str(item.get("updated"))) <= cursor_millis:
                            continue
                    except (TypeError, ValueError):
                        pass
                project_external_id = (
                    str((item.get("project") or {}).get("id"))
                    if isinstance(item.get("project"), dict)
                    else None
                )
                fields: dict[str, Any] = {}
                for source, key in (
                    ("summary", "title"),
                    ("description", "description"),
                    ("status", "status"),
                    ("priority", "priority"),
                ):
                    if item.get(source) is not None:
                        value_for_field = item[source]
                        fields[key] = (
                            value_for_field.get("name")
                            if isinstance(value_for_field, dict)
                            else value_for_field
                        )
                objects.append(
                    ExternalObject(
                        object_type=ObjectType.WORK_ITEM,
                        external_id=str(item.get("id")),
                        external_key=item.get("idReadable"),
                        title=item.get("summary"),
                        description=item.get("description"),
                        project_external_id=project_external_id,
                        provider_version=str(item.get("updated")) if item.get("updated") is not None else None,
                        content_hash=normalized_content_hash(
                            ObjectType.WORK_ITEM,
                            str(item.get("id")),
                            fields,
                            external_project_id=project_external_id,
                        ),
                    )
                )
            if len(value) < page_size:
                break
            offset += len(value)
        newest = max(
            (str(obj.provider_version) for obj in objects if obj.provider_version),
            default=cursor,
        )
        return objects, newest

    async def _webhook_secret(self, connection: ProviderConnection) -> str:
        ref = str(connection.config.get("webhook_secret_ref") or "")
        if not ref:
            raise RuntimeError("YouTrack webhook_secret_ref is required")
        return await resolve_secret(connection, self.resolver, ref)

    async def _webhook_secrets(self, connection: ProviderConnection) -> list[str]:
        refs = connection.config.get("webhook_secret_refs") or []
        if not isinstance(refs, list):
            refs = []
        current = connection.config.get("webhook_secret_ref")
        refs = [str(item) for item in refs if item]
        if current and str(current) not in refs:
            refs.insert(0, str(current))
        if not refs:
            raise RuntimeError("YouTrack webhook_secret_ref is required")
        return [await resolve_secret(connection, self.resolver, ref) for ref in refs]

    def verify_webhook(self, connection: ProviderConnection, body: bytes, headers: dict[str, str]) -> bool:
        # The Webhook Triggers app uses a shared header token, not a signature.
        # The resolver is async in normal operation, so callers should use
        # verify_webhook_async at the gateway boundary when a secret reference
        # must be resolved. This synchronous path supports a test-only token.
        configured = connection.config.get("webhook_token_test_only")
        header = str(connection.config.get("webhook_header") or "X-YouTrack-Token").lower()
        supplied = next((value for key, value in headers.items() if key.lower() == header), "")
        return bool(configured) and hmac.compare_digest(supplied, str(configured))

    async def verify_webhook_async(self, connection: ProviderConnection, body: bytes, headers: dict[str, str]) -> bool:
        header = str(connection.config.get("webhook_header") or "X-YouTrack-Token").lower()
        supplied = next((value for key, value in headers.items() if key.lower() == header), "")
        return any(hmac.compare_digest(supplied, secret) for secret in await self._webhook_secrets(connection))

    def normalize_webhook(self, event: ExternalEvent) -> NormalizedCommand | None:
        payload = event.payload
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else payload
        external_id = issue.get("id") if isinstance(issue, dict) else None
        if not external_id:
            return None
        fields = {}
        actor = None
        actor_data: dict[str, Any] | None = None
        project_data = issue.get("project") if isinstance(issue, dict) else None
        external_project_id = (
            str(project_data.get("id"))
            if isinstance(project_data, dict) and project_data.get("id") is not None
            else None
        )
        if isinstance(issue, dict):
            for source, target in (("summary", "title"), ("description", "description"), ("status", "status"), ("priority", "priority")):
                if source in issue:
                    value = issue[source]
                    fields[target] = value.get("name") if isinstance(value, dict) else value
            # Webhook Triggers payloads expose changed values in
            # ``changedFields`` rather than always including the current field
            # at the issue root.  Normalize the bounded command vocabulary
            # from that provider shape while retaining only the stable
            # provider fields that the control plane can authorize.
            changed_fields = issue.get("changedFields") or payload.get("changedFields")
            if isinstance(changed_fields, list):
                changed_business_fields: set[str] = set()
                for changed in changed_fields:
                    if not isinstance(changed, dict):
                        continue
                    name = str(changed.get("name") or "").strip().lower()
                    value = changed.get("value")
                    if name == "aiat revision":
                        if value is not None:
                            fields["_aiat_marker_revision"] = value
                    elif name == "priority":
                        changed_business_fields.add("priority")
                        fields["priority"] = (
                            value.get("name")
                            if isinstance(value, dict)
                            else value
                        )
                    elif name == "summary":
                        changed_business_fields.add("title")
                        fields["title"] = value
                    elif name == "description":
                        changed_business_fields.add("description")
                        fields["description"] = value
                    elif name == "status":
                        changed_business_fields.add("status")
                        fields["status"] = (
                            value.get("name")
                            if isinstance(value, dict)
                            else value
                        )
                if changed_business_fields:
                    fields = {
                        key: value
                        for key, value in fields.items()
                        if key.startswith("_") or key in changed_business_fields
                    }
            actor_data = issue.get("updatedBy") or issue.get("reporter")
        comment_data = payload.get("comment")
        if not isinstance(comment_data, dict):
            comments = payload.get("comments")
            if isinstance(comments, list) and comments and isinstance(comments[-1], dict):
                comment_data = comments[-1]
        if actor is None and isinstance(actor_data, dict) and (actor_data.get("login") or actor_data.get("id")):
            stable_id = actor_data.get("id")
            actor = IntegrationActor(
                actor_id=str(stable_id or actor_data.get("login")),
                immutable_actor_id=stable_id is not None,
                provider_login=str(actor_data.get("login")) if actor_data.get("login") is not None else None,
                provider_email=str(actor_data.get("email")) if actor_data.get("email") is not None else None,
            )
        if actor is None and isinstance(comment_data, dict):
            comment_actor = comment_data.get("author")
            if isinstance(comment_actor, dict) and (comment_actor.get("login") or comment_actor.get("id")):
                stable_id = comment_actor.get("id")
                actor = IntegrationActor(
                    actor_id=str(stable_id or comment_actor.get("login")),
                    immutable_actor_id=stable_id is not None,
                    provider_login=str(comment_actor.get("login")) if comment_actor.get("login") is not None else None,
                    provider_email=str(comment_actor.get("email")) if comment_actor.get("email") is not None else None,
                )
        if isinstance(comment_data, dict) and comment_data.get("text") is not None:
            fields["comment"] = comment_data.get("text")
        expected_canonical_revision = payload.get("aiat_expected_revision")
        if expected_canonical_revision is None and isinstance(issue, dict):
            expected_canonical_revision = issue.get("aiatExpectedRevision")
        try:
            expected_canonical_revision = (
                int(expected_canonical_revision)
                if expected_canonical_revision is not None
                else None
            )
        except (TypeError, ValueError):
            expected_canonical_revision = None
        return NormalizedCommand(
            connection_id=event.connection_id,
            object_type=ObjectType.WORK_ITEM,
            external_id=str(external_id),
            operation="comment" if "comment" in event.event_type.lower() else "update",
            fields=fields,
            external_project_id=external_project_id,
            content_hash=normalized_content_hash(
                ObjectType.WORK_ITEM,
                str(external_id),
                fields,
                external_project_id=external_project_id,
            ),
            expected_provider_version=(
                str(issue.get("updated")) if isinstance(issue, dict) and issue.get("updated") is not None else None
            ),
            expected_canonical_revision=expected_canonical_revision,
            actor=actor,
            idempotency_key=f"{event.connection_id}:{event.provider_delivery_id}",
            correlation_id=event.correlation_id or event.provider_delivery_id,
            causation_id=event.causation_id,
        )
