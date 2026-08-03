"""Deterministic in-memory provider used by contract and integration tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import defaultdict
from typing import Any

from ..contracts import (
    DEDICATED_PROJECT_MAPPING_PROFILE,
    AdapterCapabilities,
    BootstrapAction,
    BootstrapApplyResult,
    BootstrapPlan,
    CanonicalIteration,
    CanonicalProject,
    CanonicalWorkItem,
    ExternalEvent,
    ExternalObject,
    NormalizedCommand,
    ProjectionResult,
    ProjectionStatus,
    ProjectProvisioningApplyResult,
    ProjectProvisioningPlan,
    ProviderConnection,
    normalize_project_mapping_profile,
)


class FakeProvider:
    kind = "fake"

    def __init__(self, webhook_secret: str = "fake-secret") -> None:
        self.webhook_secret = webhook_secret.encode("utf-8")
        self.objects: dict[str, ExternalObject] = {}
        self.calls: list[dict[str, Any]] = []
        self._by_key: dict[str, str] = {}
        self._counters: defaultdict[str, int] = defaultdict(int)

    async def health(self, connection: ProviderConnection) -> dict[str, object]:
        return {"ok": True, "provider": self.kind, "objects": len(self.objects)}

    async def capabilities(self, connection: ProviderConnection) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider_kind=self.kind,
            adapter_version="1",
            work_management=True,
            projects=True,
            iterations=True,
            work_items=True,
            comments=True,
            links=True,
            webhooks=True,
            incremental_sync=True,
            supported_fields=frozenset({"title", "description", "status", "priority", "sprint_id"}),
        )

    async def discover(self, connection: ProviderConnection) -> dict[str, object]:
        return {"provider": self.kind, "objects": list(self.objects)}

    async def verify_configuration(self, connection: ProviderConnection) -> dict[str, object]:
        return {"ok": True, "provider": self.kind, "scope": "in-memory"}

    async def plan_bootstrap(self, connection: ProviderConnection, desired: dict[str, object]) -> BootstrapPlan:
        return BootstrapPlan(
            connection_id=connection.id,
            provider_kind=self.kind,
            actions=[BootstrapAction(action="adopt", resource="fake://workspace", desired=dict(desired))],
            checks=["fake health", "mapping uniqueness"],
            rollback_actions=["disable connection"],
        )

    async def apply_bootstrap(self, connection: ProviderConnection, plan: BootstrapPlan) -> BootstrapApplyResult:
        return BootstrapApplyResult(plan=plan)

    async def plan_project_provisioning(
        self,
        connection: ProviderConnection,
        project: CanonicalProject,
        *,
        mapping_profile: str = DEDICATED_PROJECT_MAPPING_PROFILE,
        external_project_id: str | None = None,
    ) -> ProjectProvisioningPlan:
        profile = normalize_project_mapping_profile(mapping_profile)
        external_id = external_project_id or f"fake-project-{project.id}"
        action = BootstrapAction(
            action="adopt_project" if external_project_id else "create_project",
            resource=f"fake:project:{external_id}",
            desired={"project_id": external_id, "name": project.name},
        )
        return ProjectProvisioningPlan(
            connection_id=connection.id,
            project_id=project.id,
            provider_kind=self.kind,
            mapping_profile=profile,
            external_project_id=external_id,
            external_project_key=external_id,
            actions=[action],
            checks=["fake project scope", "idempotent project provisioning"],
            rollback_actions=["disable binding"],
        )

    async def apply_project_provisioning(
        self,
        connection: ProviderConnection,
        plan: ProjectProvisioningPlan,
    ) -> ProjectProvisioningApplyResult:
        if not plan.ready_to_apply:
            raise ValueError("project provisioning plan has blockers")
        resource = {
            "resource": f"fake:project:{plan.external_project_id}",
            "external_id": plan.external_project_id,
            "short_name": plan.external_project_key,
        }
        adopted = any(item.action == "adopt_project" for item in plan.actions)
        return ProjectProvisioningApplyResult(
            plan=plan,
            created=[] if adopted else [resource],
            adopted=[resource] if adopted else [],
        )

    async def project_project(
        self,
        connection: ProviderConnection,
        project: CanonicalProject,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult:
        ext_id = external_id or f"fake-project-{project.id}"
        self.objects[ext_id] = ExternalObject(
            object_type="project",
            external_id=ext_id,
            external_key=ext_id,
            title=project.name,
            description=project.description,
            status=project.state,
            provider_version=str(project.revision),
            metadata={"aiat_object_id": str(project.id), "idempotency_key": idempotency_key},
        )
        self.calls.append({"operation": "project_project", "idempotency_key": idempotency_key, "object": ext_id})
        return ProjectionResult(
            status=ProjectionStatus.SYNCED,
            connection_id=connection.id,
            object_type="project",
            aiat_object_id=project.id,
            external_id=ext_id,
            provider_version=str(project.revision),
        )

    async def project_iteration(
        self,
        connection: ProviderConnection,
        iteration: CanonicalIteration,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult:
        ext_id = external_id or f"fake-iteration-{iteration.id}"
        self.objects[ext_id] = ExternalObject(
            object_type="sprint",
            external_id=ext_id,
            external_key=ext_id,
            title=iteration.name or f"Sprint {iteration.number}",
            description=iteration.goal,
            status=iteration.status,
            project_external_id=f"fake-project-{iteration.project_id}",
            provider_version=str(iteration.revision),
            metadata={"aiat_object_id": str(iteration.id), "idempotency_key": idempotency_key},
        )
        self.calls.append({"operation": "project_iteration", "idempotency_key": idempotency_key, "object": ext_id})
        return ProjectionResult(
            status=ProjectionStatus.SYNCED,
            connection_id=connection.id,
            object_type="sprint",
            aiat_object_id=iteration.id,
            external_id=ext_id,
            provider_version=str(iteration.revision),
        )

    async def project_work_item(self, connection: ProviderConnection, item: CanonicalWorkItem, *, external_id: str | None = None, idempotency_key: str) -> ProjectionResult:
        existing = external_id or self._by_key.get(str(item.id))
        ext_id = existing or f"fake-{item.id}"
        obj = ExternalObject(
            object_type="work_item",
            external_id=ext_id,
            external_key=ext_id,
            title=item.title,
            description=item.description,
            status=item.status,
            priority=item.priority,
            provider_version=str(item.revision),
            metadata={"aiat_object_id": str(item.id), "idempotency_key": idempotency_key},
        )
        self.objects[ext_id] = obj
        self._by_key[str(item.id)] = ext_id
        self.calls.append({"operation": "project_work_item", "idempotency_key": idempotency_key, "object": ext_id})
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type="work_item", aiat_object_id=item.id, external_id=ext_id, provider_version=str(item.revision))

    async def read_work_item(self, connection: ProviderConnection, external_id: str) -> ExternalObject:
        object_ = self.objects.get(str(external_id))
        if object_ is None or str(object_.object_type) != "work_item":
            raise KeyError(f"fake work item {external_id!r} not found")
        return object_

    async def archive_work_item(self, connection: ProviderConnection, external_id: str, *, idempotency_key: str) -> ProjectionResult:
        object_ = await self.read_work_item(connection, external_id)
        self.objects[str(external_id)] = object_.model_copy(update={"status": "archived", "metadata": {**object_.metadata, "idempotency_key": idempotency_key}})
        self.calls.append({"operation": "archive_work_item", "idempotency_key": idempotency_key, "object": str(external_id)})
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type="work_item", external_id=str(external_id))

    async def list_projects(self, connection: ProviderConnection, *, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        values = [item for item in self.objects.values() if str(item.object_type) == "project"]
        return values, str(len(values))

    async def list_iterations(self, connection: ProviderConnection, *, project_external_id: str | None = None, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        values = [item for item in self.objects.values() if str(item.object_type) == "sprint" and (project_external_id is None or item.project_external_id == project_external_id)]
        return values, str(len(values))

    async def project_comment(self, connection: ProviderConnection, *, external_id: str, body: str, idempotency_key: str) -> ProjectionResult:
        self.calls.append({"operation": "project_comment", "idempotency_key": idempotency_key, "object": external_id, "body": body})
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type="comment", external_id=external_id)

    async def project_link(self, connection: ProviderConnection, *, external_id: str, link: dict[str, object], idempotency_key: str) -> ProjectionResult:
        self.calls.append({"operation": "project_link", "idempotency_key": idempotency_key, "object": external_id, "link": link})
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type="work_item", external_id=external_id)

    async def list_changes(self, connection: ProviderConnection, *, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        return list(self.objects.values()), str(len(self.objects))

    def verify_webhook(self, connection: ProviderConnection, body: bytes, headers: dict[str, str]) -> bool:
        supplied = headers.get("x-fake-signature", "")
        secret = str(connection.config.get("webhook_secret_test_only") or self.webhook_secret.decode("utf-8")).encode("utf-8")
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)

    def normalize_webhook(self, event: ExternalEvent) -> NormalizedCommand | None:
        obj = event.payload.get("object")
        if not isinstance(obj, dict) or not obj.get("external_id"):
            return None
        return NormalizedCommand(
            connection_id=event.connection_id,
            object_type=str(obj.get("object_type") or "work_item"),
            external_id=str(obj["external_id"]),
            operation=str(event.payload.get("operation") or "update"),
            fields=dict(obj.get("fields") or {}),
            external_project_id=(
                str(obj.get("project_external_id"))
                if obj.get("project_external_id") is not None
                else None
            ),
            external_repository=(
                str(obj.get("repository")) if obj.get("repository") is not None else None
            ),
            content_hash=hashlib.sha256(
                json.dumps(
                    {"object_type": str(obj.get("object_type") or "work_item"), "external_id": str(obj["external_id"]), "fields": dict(obj.get("fields") or {})},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            expected_provider_version=(
                str(obj.get("provider_version")) if obj.get("provider_version") is not None else None
            ),
            idempotency_key=f"{event.connection_id}:{event.provider_delivery_id}",
            correlation_id=event.correlation_id or event.provider_delivery_id,
            causation_id=event.causation_id,
        )
