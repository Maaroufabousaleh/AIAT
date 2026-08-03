"""Provider ports. Implementations must be side-effect explicit and bounded."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .contracts import (
        AdapterCapabilities,
        BootstrapApplyResult,
        BootstrapPlan,
        CanonicalIteration,
        CanonicalProject,
        CanonicalWorkItem,
        ExternalEvent,
        ExternalObject,
        NormalizedCommand,
        ProjectionResult,
        ProjectProvisioningApplyResult,
        ProjectProvisioningPlan,
        ProviderConnection,
    )


class WorkManagementProvider(Protocol):
    """Common denominator for YouTrack, Linear, Jira, GitHub Issues, etc."""

    kind: str

    async def health(self, connection: ProviderConnection) -> dict[str, object]: ...

    async def capabilities(self, connection: ProviderConnection) -> AdapterCapabilities: ...

    async def discover(self, connection: ProviderConnection) -> dict[str, object]: ...

    async def verify_configuration(self, connection: ProviderConnection) -> dict[str, object]: ...

    async def plan_bootstrap(self, connection: ProviderConnection, desired: dict[str, object]) -> BootstrapPlan: ...

    async def apply_bootstrap(self, connection: ProviderConnection, plan: BootstrapPlan) -> BootstrapApplyResult: ...

    async def plan_project_provisioning(
        self,
        connection: ProviderConnection,
        project: CanonicalProject,
        *,
        mapping_profile: str = "dedicated_project",
        external_project_id: str | None = None,
    ) -> ProjectProvisioningPlan: ...

    async def apply_project_provisioning(
        self,
        connection: ProviderConnection,
        plan: ProjectProvisioningPlan,
    ) -> ProjectProvisioningApplyResult: ...

    async def project_project(
        self,
        connection: ProviderConnection,
        project: CanonicalProject,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult: ...

    async def project_iteration(
        self,
        connection: ProviderConnection,
        iteration: CanonicalIteration,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult: ...

    async def project_work_item(
        self,
        connection: ProviderConnection,
        item: CanonicalWorkItem,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult: ...

    async def project_comment(
        self,
        connection: ProviderConnection,
        *,
        external_id: str,
        body: str,
        idempotency_key: str,
    ) -> ProjectionResult: ...

    async def project_link(
        self,
        connection: ProviderConnection,
        *,
        external_id: str,
        link: dict[str, object],
        idempotency_key: str,
    ) -> ProjectionResult: ...

    async def read_work_item(
        self,
        connection: ProviderConnection,
        external_id: str,
    ) -> ExternalObject: ...

    async def archive_work_item(
        self,
        connection: ProviderConnection,
        external_id: str,
        *,
        idempotency_key: str,
    ) -> ProjectionResult: ...

    async def list_projects(
        self, connection: ProviderConnection, *, cursor: str | None = None
    ) -> tuple[Sequence[ExternalObject], str | None]: ...

    async def list_iterations(
        self,
        connection: ProviderConnection,
        *,
        project_external_id: str | None = None,
        cursor: str | None = None,
    ) -> tuple[Sequence[ExternalObject], str | None]: ...

    async def list_changes(
        self, connection: ProviderConnection, *, cursor: str | None = None
    ) -> tuple[Sequence[ExternalObject], str | None]: ...

    def verify_webhook(self, connection: ProviderConnection, body: bytes, headers: dict[str, str]) -> bool: ...

    def normalize_webhook(self, event: ExternalEvent) -> NormalizedCommand | None: ...


class SourceControlProvider(Protocol):
    """Source-control facts and governed repository execution capabilities."""

    kind: str

    async def capabilities(self, connection: ProviderConnection) -> AdapterCapabilities: ...

    async def discover_installation(self, connection: ProviderConnection) -> dict[str, object]: ...

    async def project_pull_request(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult: ...

    async def create_branch(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult: ...

    async def publish_review_comment(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult: ...

    async def capture_commit_evidence(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult: ...

    async def publish_check(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult: ...

    async def mint_run_credential(self, connection: ProviderConnection, repository: str, permissions: dict[str, str]) -> dict[str, object]: ...

    def verify_webhook(self, connection: ProviderConnection, body: bytes, headers: dict[str, str]) -> bool: ...

    def normalize_webhook(self, event: ExternalEvent) -> NormalizedCommand | None: ...
