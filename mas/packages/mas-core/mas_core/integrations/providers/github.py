"""GitHub App adapter for issues, pull requests, checks, and webhooks."""

from __future__ import annotations

import hashlib
import hmac
import re
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
from .base import CredentialResolver, ProviderHTTP, resolve_secret, response_json


class GitHubProvider:
    kind = "github"
    adapter_version = "1"

    def __init__(
        self,
        resolver: CredentialResolver | None = None,
        *,
        timeout: float = 20.0,
        run_credential_broker: Any | None = None,
    ) -> None:
        self.resolver = resolver
        self.run_credential_broker = run_credential_broker
        self.http = ProviderHTTP(resolver, timeout=timeout, token_resolver=self._api_token)

    async def _api_token(self, connection: ProviderConnection) -> str:
        """Resolve an installation token without exposing App private keys."""
        config = connection.config or {}
        if config.get("github_app_id") and config.get("github_installation_id"):
            if not callable(self.run_credential_broker):
                raise RuntimeError("GitHub installation token broker is not configured")
            repository = self._repository(connection)
            profile = connection.capability_profile.lower()
            permissions: dict[str, str] = {"metadata": "read", "issues": "write"}
            if profile in {"delivery", "checks"}:
                permissions.update({"contents": "write", "pull_requests": "write"})
            if profile == "checks":
                permissions["checks"] = "write"
            result = self.run_credential_broker(connection, repository, permissions)
            if hasattr(result, "__await__"):
                result = await result
            if not isinstance(result, dict) or not result.get("token"):
                raise RuntimeError("GitHub installation token broker returned no token")
            return str(result["token"])
        return await resolve_secret(connection, self.resolver)

    @staticmethod
    def _repository(connection: ProviderConnection) -> str:
        """Return the one repository explicitly scoped on this connection."""
        repository = str(connection.config.get("repository") or "")
        owner, separator, name = repository.partition("/")
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        if (
            not separator
            or not owner
            or not name
            or "/" in name
            or any(char not in allowed for char in owner + name)
            or owner.startswith((".", "-"))
            or name.startswith((".", "-"))
        ):
            raise ValueError("GitHub connection.repository must be owner/name")
        return repository

    @staticmethod
    def _safe_git_ref(value: str, *, field: str) -> str:
        """Validate a bounded branch/ref before it reaches the GitHub API."""
        ref = str(value or "")
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-/"
        if (
            not ref
            or len(ref) > 240
            or any(char not in allowed for char in ref)
            or ref.startswith(("/", "-"))
            or ref.endswith(("/", "."))
            or "//" in ref
            or ".." in ref
            or "@{" in ref
            or any(segment in {"", ".", ".."} for segment in ref.split("/"))
        ):
            raise ValueError(f"{field} must be a safe Git ref")
        return ref

    @staticmethod
    def _safe_identifier(value: str, *, field: str) -> str:
        """Validate an issue/PR/check identifier used in a URL path."""
        identifier = str(value or "")
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        if (
            not identifier
            or len(identifier) > 240
            or any(char not in allowed for char in identifier)
            or identifier.startswith((".", "-"))
            or ".." in identifier
        ):
            raise ValueError(f"{field} must be a safe provider identifier")
        return identifier

    async def capabilities(self, connection: ProviderConnection) -> AdapterCapabilities:
        profile = connection.capability_profile.lower()
        return AdapterCapabilities(
            provider_kind=self.kind,
            adapter_version=self.adapter_version,
            work_management=profile in {"pm", "delivery", "checks"},
            source_control=True,
            work_items=profile in {"pm", "delivery", "checks"},
            comments=profile in {"pm", "delivery", "checks"},
            projects=False,
            iterations=False,
            repositories=True,
            pull_requests=profile in {"delivery", "checks"},
            checks=profile == "checks",
            webhooks=True,
            incremental_sync=True,
            supported_fields=frozenset({"title", "description", "status", "priority", "labels"}),
        )

    async def health(self, connection: ProviderConnection) -> dict[str, object]:
        path = "/installation/repositories" if connection.config.get("github_app_id") else "/app"
        response = await self.http.request(connection, "GET", path)
        return {"ok": True, "provider": self.kind, "app": response_json(response)}

    async def discover(self, connection: ProviderConnection) -> dict[str, object]:
        response = await self.http.request(connection, "GET", "/installation/repositories", params={"per_page": 100})
        return {"repositories": response_json(response)}

    async def discover_installation(self, connection: ProviderConnection) -> dict[str, object]:
        return await self.discover(connection)

    async def verify_configuration(self, connection: ProviderConnection) -> dict[str, object]:
        repository = self._repository(connection)
        if connection.capability_profile.lower() not in {"pm", "delivery", "checks"}:
            raise ValueError("GitHub capability_profile must be pm, delivery, or checks")
        discovered = await self.discover_installation(connection)
        repositories = discovered.get("repositories") if isinstance(discovered, dict) else None
        return {"ok": True, "repository": repository, "installation_repositories": repositories}

    async def plan_bootstrap(self, connection: ProviderConnection, desired: dict[str, object]) -> BootstrapPlan:
        profile = connection.capability_profile.lower()
        actions = [
            BootstrapAction(action="adopt_installation", resource="github-app-installation", desired={"repositories": desired.get("repositories", [])}, manual=True),
            BootstrapAction(action="configure_webhook", resource="github-app-webhook", desired={"events": ["issues", "pull_request", "check_run"]}, manual=True),
        ]
        checks = ["installation scope", "JWT/private-key custody", "raw-body HMAC", f"permission profile={profile}"]
        blockers = [] if desired.get("repositories") else ["desired.repositories is required; repository selection is never inferred"]
        return BootstrapPlan(connection_id=connection.id, provider_kind=self.kind, actions=actions, blockers=blockers, checks=checks, rollback_actions=["uninstall app from selected repositories", "disable binding"])

    async def apply_bootstrap(self, connection: ProviderConnection, plan: BootstrapPlan) -> BootstrapApplyResult:
        if not plan.ready_to_apply:
            raise ValueError("bootstrap plan has blockers or destructive actions")
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
        return ProjectProvisioningPlan(
            connection_id=connection.id,
            project_id=project.id,
            provider_kind=self.kind,
            mapping_profile=profile,
            external_project_id=external_project_id,
            blockers=[
                "GitHub Issues has no portable project object; use the explicit umbrella_issues profile or a work-management provider"
            ],
            checks=["repository scope", "issue/comment webhook coverage"],
            rollback_actions=["disable binding", "retain GitHub issues"],
        )

    async def apply_project_provisioning(
        self,
        connection: ProviderConnection,
        plan: ProjectProvisioningPlan,
    ) -> ProjectProvisioningApplyResult:
        raise ValueError("GitHub Issues cannot provision a dedicated project object")

    async def project_project(
        self,
        connection: ProviderConnection,
        project: CanonicalProject,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult:
        raise NotImplementedError("GitHub Issues does not provide a portable project-management project object")

    async def project_iteration(
        self,
        connection: ProviderConnection,
        iteration: CanonicalIteration,
        *,
        external_id: str | None = None,
        idempotency_key: str,
    ) -> ProjectionResult:
        raise NotImplementedError("GitHub Issues does not provide a portable sprint/iteration object")

    async def project_work_item(self, connection: ProviderConnection, item: CanonicalWorkItem, *, external_id: str | None = None, idempotency_key: str) -> ProjectionResult:
        repository = self._repository(connection)
        body = {"title": item.title, "body": self._body(item), "labels": [f"aiat:{item.item_type.lower()}"]}
        if external_id:
            external_id = self._safe_identifier(external_id, field="external_id")
            path = f"/repos/{repository}/issues/{external_id}"
            response = await self.http.request(connection, "PATCH", path, json_body=body, headers={"Idempotency-Key": idempotency_key})
        else:
            response = await self.http.request(connection, "POST", f"/repos/{repository}/issues", json_body=body, headers={"Idempotency-Key": idempotency_key})
        value = response_json(response)
        if not isinstance(value, dict):
            raise RuntimeError("GitHub issue response was not an object")
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.WORK_ITEM, external_id=str(value.get("number")), external_url=value.get("html_url"), provider_version=str(value.get("updated_at") or ""))

    async def read_work_item(self, connection: ProviderConnection, external_id: str) -> ExternalObject:
        repository = self._repository(connection)
        external_id = self._safe_identifier(external_id, field="external_id")
        response = await self.http.request(connection, "GET", f"/repos/{repository}/issues/{external_id}")
        value = response_json(response)
        if not isinstance(value, dict) or value.get("number") is None:
            raise RuntimeError("GitHub issue response was invalid")
        return ExternalObject(
            object_type=ObjectType.WORK_ITEM,
            external_id=str(value["number"]),
            external_key=f"#{value['number']}",
            title=value.get("title"),
            description=value.get("body"),
            url=value.get("html_url"),
            status="closed" if value.get("state") == "closed" else "open",
            metadata={"repository": repository},
            provider_version=value.get("updated_at"),
        )

    async def archive_work_item(self, connection: ProviderConnection, external_id: str, *, idempotency_key: str) -> ProjectionResult:
        repository = self._repository(connection)
        external_id = self._safe_identifier(external_id, field="external_id")
        response = await self.http.request(
            connection,
            "PATCH",
            f"/repos/{repository}/issues/{external_id}",
            json_body={"state": "closed", "state_reason": "not_planned"},
            headers={"Idempotency-Key": idempotency_key},
        )
        value = response_json(response)
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.WORK_ITEM, external_id=str(value.get("number") or external_id) if isinstance(value, dict) else str(external_id), provider_version=str(value.get("updated_at") or "") if isinstance(value, dict) else None)

    async def list_projects(self, connection: ProviderConnection, *, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        return [], cursor

    async def list_iterations(self, connection: ProviderConnection, *, project_external_id: str | None = None, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        return [], cursor

    def _body(self, item: CanonicalWorkItem) -> str:
        marker = f"<!-- aiat:object={item.id};revision={item.revision} -->"
        return f"{marker}\n\n{item.description or ''}\n\nAIAT status: `{item.status}`\nAIAT priority: `{item.priority}`"

    async def project_comment(self, connection: ProviderConnection, *, external_id: str, body: str, idempotency_key: str) -> ProjectionResult:
        repository = self._repository(connection)
        external_id = self._safe_identifier(external_id, field="external_id")
        response = await self.http.request(connection, "POST", f"/repos/{repository}/issues/{external_id}/comments", json_body={"body": body}, headers={"Idempotency-Key": idempotency_key})
        value = response_json(response)
        comment_id = str(value.get("id")) if isinstance(value, dict) else None
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.COMMENT, external_id=comment_id)

    async def project_link(self, connection: ProviderConnection, *, external_id: str, link: dict[str, object], idempotency_key: str) -> ProjectionResult:
        raise NotImplementedError("GitHub Issues has no provider-neutral typed issue-link API")

    async def list_changes(self, connection: ProviderConnection, *, cursor: str | None = None) -> tuple[list[ExternalObject], str | None]:
        repository = self._repository(connection)
        objects: list[ExternalObject] = []
        page = 1
        page_size = 100
        while True:
            params: dict[str, Any] = {"state": "all", "per_page": page_size, "page": page}
            if cursor:
                params["since"] = cursor
            response = await self.http.request(
                connection,
                "GET",
                f"/repos/{repository}/issues",
                params=params,
            )
            value = response_json(response)
            if not isinstance(value, list):
                return objects, cursor
            for item in value:
                if not isinstance(item, dict) or item.get("number") is None or item.get("pull_request"):
                    continue
                external_id = str(item["number"])
                fields = {
                    "title": item.get("title"),
                    "description": item.get("body"),
                    "status": "closed" if item.get("state") == "closed" else "open",
                }
                objects.append(
                    ExternalObject(
                        object_type=ObjectType.WORK_ITEM,
                        external_id=external_id,
                        external_key=f"#{external_id}",
                        title=item.get("title"),
                        description=item.get("body"),
                        url=item.get("html_url"),
                        status=fields["status"],
                        provider_version=item.get("updated_at"),
                        content_hash=normalized_content_hash(
                            ObjectType.WORK_ITEM,
                            external_id,
                            fields,
                            external_repository=repository,
                        ),
                        metadata={"repository": repository},
                    )
                )
            if len(value) < page_size:
                break
            page += 1
        newest = max((str(obj.provider_version) for obj in objects if obj.provider_version), default=cursor)
        return objects, newest

    async def publish_check(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult:
        repository = self._repository(connection)
        response = await self.http.request(connection, "POST", f"/repos/{repository}/check-runs", json_body=payload)
        value = response_json(response)
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.CHECK, external_id=str(value.get("id")) if isinstance(value, dict) else None)

    async def project_pull_request(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult:
        repository = self._repository(connection)
        if payload.get("title") and payload.get("head") and payload.get("base"):
            head = self._safe_git_ref(str(payload["head"]), field="head")
            base = self._safe_git_ref(str(payload["base"]), field="base")
            response = await self.http.request(
                connection,
                "POST",
                f"/repos/{repository}/pulls",
                json_body={
                    "title": str(payload["title"]),
                    "head": head,
                    "base": base,
                    "body": str(payload.get("body") or ""),
                    "draft": bool(payload.get("draft", False)),
                },
                headers={"Idempotency-Key": str(payload.get("idempotency_key") or "")},
            )
            value = response_json(response)
            if not isinstance(value, dict):
                raise RuntimeError("GitHub pull request response was not an object")
            return ProjectionResult(
                status=ProjectionStatus.SYNCED,
                connection_id=connection.id,
                object_type=ObjectType.PULL_REQUEST,
                external_id=str(value.get("number")),
                external_url=value.get("html_url"),
                provider_version=str(value.get("updated_at") or ""),
            )
        number = payload.get("number")
        if number is None:
            raise ValueError("pull request number is required")
        number = self._safe_identifier(str(number), field="number")
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.PULL_REQUEST, external_id=str(number), external_url=f"https://github.com/{repository}/pull/{number}")

    async def create_branch(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult:
        repository = self._repository(connection)
        branch = self._safe_git_ref(str(payload.get("branch") or ""), field="branch")
        from_ref = self._safe_git_ref(str(payload.get("from_ref") or "main"), field="from_ref")
        source = await self.http.request(connection, "GET", f"/repos/{repository}/git/ref/heads/{from_ref}")
        source_value = response_json(source)
        if not isinstance(source_value, dict) or not isinstance(source_value.get("object"), dict):
            raise RuntimeError("GitHub source ref response was invalid")
        sha = str(source_value["object"].get("sha") or "")
        if not sha:
            raise RuntimeError("GitHub source ref has no commit SHA")
        response = await self.http.request(
            connection,
            "POST",
            f"/repos/{repository}/git/refs",
            json_body={"ref": f"refs/heads/{branch}", "sha": sha},
            headers={"Idempotency-Key": str(payload.get("idempotency_key") or "")},
        )
        value = response_json(response)
        return ProjectionResult(
            status=ProjectionStatus.SYNCED,
            connection_id=connection.id,
            object_type=ObjectType.REPOSITORY,
            external_id=branch,
            provider_version=str((value.get("object") or {}).get("sha") or sha) if isinstance(value, dict) else sha,
        )

    async def publish_review_comment(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult:
        repository = self._repository(connection)
        number = payload.get("pull_request_number")
        body = str(payload.get("body") or "")
        commit_id = str(payload.get("commit_id") or "")
        path = str(payload.get("path") or "")
        if number is None or not body:
            raise ValueError("pull_request_number and body are required")
        number = self._safe_identifier(str(number), field="pull_request_number")
        request_body: dict[str, object] = {"body": body}
        if commit_id and path:
            request_body.update({"commit_id": commit_id, "path": path, "line": int(payload.get("line") or 1), "side": str(payload.get("side") or "RIGHT")})
        response = await self.http.request(
            connection,
            "POST",
            f"/repos/{repository}/pulls/{number}/comments",
            json_body=request_body,
            headers={"Idempotency-Key": str(payload.get("idempotency_key") or "")},
        )
        value = response_json(response)
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.PULL_REQUEST, external_id=str(value.get("id")) if isinstance(value, dict) else None)

    async def capture_commit_evidence(self, connection: ProviderConnection, payload: dict[str, object]) -> ProjectionResult:
        repository = self._repository(connection)
        sha = str(payload.get("sha") or "")
        if not sha:
            raise ValueError("sha is required")
        sha = self._safe_identifier(sha, field="sha")
        response = await self.http.request(connection, "GET", f"/repos/{repository}/commits/{sha}")
        value = response_json(response)
        if not isinstance(value, dict):
            raise RuntimeError("GitHub commit response was not an object")
        return ProjectionResult(status=ProjectionStatus.SYNCED, connection_id=connection.id, object_type=ObjectType.CHECK, external_id=sha, provider_version=str(value.get("sha") or sha))

    async def mint_run_credential(self, connection: ProviderConnection, repository: str, permissions: dict[str, str]) -> dict[str, object]:
        # JWT signing and installation-token exchange belong in the credential
        # manager. This method accepts an injected broker callback in production;
        # refusing to fabricate a token is safer than returning a fake success.
        configured_repository = str(connection.config.get("repository") or "")
        if not configured_repository:
            raise ValueError("GitHub run credentials require a configured repository")
        if str(repository) != configured_repository:
            raise ValueError("requested repository is outside the connection scope")
        broker = self.run_credential_broker or connection.config.get("run_token_broker")
        if not callable(broker):
            raise RuntimeError("GitHub installation token broker is not configured")
        if self.run_credential_broker is not None:
            result = broker(connection, repository, permissions)
        else:
            # Test-only legacy injection; database-backed connections cannot
            # serialize callables and production uses the server-side broker.
            result = broker(repository, permissions)
        if hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, dict) or not result.get("token"):
            raise RuntimeError("GitHub token broker returned no token")
        return dict(result)

    def _webhook_secret_test_only(self, connection: ProviderConnection) -> str:
        value = connection.config.get("webhook_secret_test_only")
        if not value:
            raise RuntimeError("GitHub webhook secret must be resolved through the credential boundary")
        return str(value)

    def verify_webhook(self, connection: ProviderConnection, body: bytes, headers: dict[str, str]) -> bool:
        signature = next((v for k, v in headers.items() if k.lower() == "x-hub-signature-256"), "")
        if not signature.startswith("sha256="):
            return False
        expected = hmac.new(self._webhook_secret_test_only(connection).encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature[7:], expected)

    async def verify_webhook_async(self, connection: ProviderConnection, body: bytes, headers: dict[str, str], *, secret_ref: str) -> bool:
        signature = next((v for k, v in headers.items() if k.lower() == "x-hub-signature-256"), "")
        if not signature.startswith("sha256="):
            return False
        refs = connection.config.get("webhook_secret_refs") or [secret_ref]
        if not isinstance(refs, list):
            refs = [secret_ref]
        for ref in refs:
            secret = await resolve_secret(connection, self.resolver, str(ref))
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(signature[7:], expected):
                return True
        return False

    def normalize_webhook(self, event: ExternalEvent) -> NormalizedCommand | None:
        payload = event.payload
        event_name = event.event_type.lower()
        if event_name in {"issues", "issue", "issue_comment", "comment"}:
            obj = payload.get("issue")
            object_type = ObjectType.WORK_ITEM
        elif event_name in {"pull_request", "pull_request_review"}:
            obj = payload.get("pull_request")
            object_type = ObjectType.PULL_REQUEST
        elif event_name in {"check_run", "check_suite"}:
            obj = payload.get("check_run") or payload.get("check_suite")
            object_type = ObjectType.CHECK
        else:
            return None
        if not isinstance(obj, dict) or obj.get("id") is None:
            return None
        fields: dict[str, Any] = {}
        actor = None
        repository_data = obj.get("repository") or payload.get("repository") or {}
        external_repository = (
            str(repository_data.get("full_name"))
            if isinstance(repository_data, dict) and repository_data.get("full_name")
            else None
        )
        if object_type == ObjectType.WORK_ITEM:
            fields = {"title": obj.get("title"), "description": obj.get("body"), "status": "closed" if obj.get("state") == "closed" else "open"}
            body = fields.get("description")
            if isinstance(body, str):
                marker = re.match(r"^\s*<!--\s*aiat:object=([^;\s]+);revision=([0-9]+)\s*-->\s*", body)
                if marker:
                    fields["_aiat_marker_object_id"] = marker.group(1)
                    fields["_aiat_marker_revision"] = int(marker.group(2))
            if event_name in {"issue_comment", "comment"} and isinstance(payload.get("comment"), dict):
                fields["comment"] = payload["comment"].get("body")
            actor_data = obj.get("user") or payload.get("sender")
            if isinstance(actor_data, dict) and (actor_data.get("login") or actor_data.get("id")):
                actor = IntegrationActor(actor_id=str(actor_data.get("login") or actor_data.get("id")))
        elif object_type == ObjectType.PULL_REQUEST:
            fields = {
                "title": obj.get("title"),
                "description": obj.get("body"),
                "status": obj.get("state"),
                "merged": obj.get("merged") if "merged" in obj else None,
                "head_sha": ((obj.get("head") or {}).get("sha") if isinstance(obj.get("head"), dict) else None),
                "base_sha": ((obj.get("base") or {}).get("sha") if isinstance(obj.get("base"), dict) else None),
                "url": obj.get("html_url"),
                "action": payload.get("action"),
            }
            actor_data = obj.get("user") or payload.get("sender")
            if isinstance(actor_data, dict) and (actor_data.get("login") or actor_data.get("id")):
                actor = IntegrationActor(actor_id=str(actor_data.get("login") or actor_data.get("id")))
        elif object_type == ObjectType.CHECK:
            app_data = obj.get("app")
            check_name = obj.get("name")
            if not check_name and isinstance(app_data, dict):
                check_name = app_data.get("name")
            fields = {
                "name": check_name,
                "status": obj.get("status"),
                "conclusion": obj.get("conclusion"),
                "details_url": obj.get("details_url"),
                "head_sha": obj.get("head_sha"),
                "action": payload.get("action"),
            }
            actor_data = obj.get("app") or payload.get("sender")
            if isinstance(actor_data, dict) and (actor_data.get("slug") or actor_data.get("name") or actor_data.get("login")):
                actor = IntegrationActor(actor_id=str(actor_data.get("slug") or actor_data.get("name") or actor_data.get("login")))
        operation = "comment" if event_name in {"issue_comment", "comment"} else event.payload.get("action", "update")
        return NormalizedCommand(
            connection_id=event.connection_id,
            object_type=object_type,
            external_id=str(obj.get("number") or obj["id"]),
            operation=operation,
            fields=fields,
            external_repository=external_repository,
            content_hash=normalized_content_hash(
                object_type,
                str(obj.get("number") or obj["id"]),
                fields,
                external_repository=external_repository,
            ),
            expected_provider_version=(
                str(obj.get("updated_at")) if obj.get("updated_at") is not None else None
            ),
            actor=actor,
            idempotency_key=f"{event.connection_id}:{event.provider_delivery_id}",
            correlation_id=event.correlation_id or event.provider_delivery_id,
            causation_id=event.causation_id,
        )
