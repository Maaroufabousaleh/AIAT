from __future__ import annotations

import hashlib
import hmac
import json
import ssl
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from mas_core.integrations.contracts import (
    BootstrapAction,
    BootstrapPlan,
    CanonicalIteration,
    CanonicalProject,
    CanonicalWorkItem,
    ExternalEvent,
    PMLifecycleTransitionPlan,
    LifecyclePlanStatus,
    ProviderConnection,
    pm_binding_effective_policy,
    validate_credential_references,
)
from mas_core.integrations.providers.fake import FakeProvider
from mas_core.integrations.providers.github import GitHubProvider
from mas_core.integrations.providers.youtrack import YouTrackProvider
from mas_core.integrations.providers.base import provider_ssl_context
from mas_core.integrations.registry import ProviderRegistry


def connection(kind: str = "fake", **config: object) -> ProviderConnection:
    return ProviderConnection(
        provider_kind=kind,
        display_name=f"{kind} test",
        base_url="https://provider.example",
        credential_ref="test-secret",
        config=dict(config),
    )


def item() -> CanonicalWorkItem:
    return CanonicalWorkItem(id=uuid4(), project_id=uuid4(), title="Integrate PM", revision=3)


@pytest.mark.asyncio
async def test_fake_provider_supports_project_iteration_and_archive_contracts() -> None:
    provider = FakeProvider()
    conn = connection()
    project = CanonicalProject(id=uuid4(), name="AIAT")
    iteration = CanonicalIteration(id=uuid4(), project_id=project.id, number=2, name="Sprint 2")
    await provider.project_project(conn, project, idempotency_key="project-1")
    await provider.project_iteration(conn, iteration, idempotency_key="iteration-1")
    work = await provider.project_work_item(conn, CanonicalWorkItem(id=uuid4(), project_id=project.id, title="Task"), idempotency_key="work-1")
    assert (await provider.list_projects(conn))[0][0].external_id.startswith("fake-project-")
    assert (await provider.list_iterations(conn))[0][0].title == "Sprint 2"
    assert (await provider.read_work_item(conn, str(work.external_id))).title == "Task"
    await provider.archive_work_item(conn, str(work.external_id), idempotency_key="archive-1")
    assert (await provider.read_work_item(conn, str(work.external_id))).status == "archived"


def test_nested_provider_secret_material_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_credential_references({"selectors": [{"nested": {"token": "not-a-ref"}}]})


def test_provider_ssl_context_keeps_certificate_verification_enabled() -> None:
    context = provider_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_youtrack_canonical_short_names_are_valid_and_uuid_unique() -> None:
    first = CanonicalProject(id=uuid4(), name="Roadmap / North America")
    second = CanonicalProject(id=uuid4(), name=first.name)
    first_key = YouTrackProvider.project_short_name_for_canonical(first)
    second_key = YouTrackProvider.project_short_name_for_canonical(second)
    assert first_key != second_key
    assert first_key.startswith("AIAT-")
    assert all(char.isalnum() or char in "._-" for char in first_key)
    assert validate_credential_references({"webhook_secret_ref": "pm-webhook"})["webhook_secret_ref"] == "pm-webhook"


def test_registry_allows_reviewed_future_provider_without_replacing_builtins() -> None:
    registry = ProviderRegistry()

    class FutureProvider(FakeProvider):
        kind = "future"

    registry.register("future", lambda _resolver: FutureProvider())
    assert isinstance(registry.get("future"), FutureProvider)
    with pytest.raises(ValueError):
        registry.register("github", lambda _resolver: FutureProvider())


@pytest.mark.asyncio
async def test_fake_provider_projection_is_idempotent_and_attributed() -> None:
    provider = FakeProvider()
    conn = connection()
    first = await provider.project_work_item(conn, item(), idempotency_key="same-key")
    second = await provider.project_work_item(conn, item(), external_id=first.external_id, idempotency_key="same-key")
    assert first.external_id == second.external_id
    assert len(provider.calls) == 2
    assert provider.objects[first.external_id].metadata["idempotency_key"] == "same-key"


@pytest.mark.asyncio
async def test_bootstrap_digest_detects_plan_changes() -> None:
    plan = BootstrapPlan(
        connection_id=uuid4(),
        provider_kind="fake",
        actions=[BootstrapAction(action="adopt", resource="workspace")],
    )
    digest = plan.digest()
    plan.actions[0].desired["changed"] = True
    assert plan.digest() != digest


def test_lifecycle_plan_digest_is_deterministic_and_excludes_status() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 7, 29, tzinfo=UTC)
    plan = PMLifecycleTransitionPlan(
        target_type="pm_binding",
        target_id=uuid4(),
        connection_id=uuid4(),
        binding_id=uuid4(),
        expected_connection_status="SHADOW",
        expected_binding_status="SHADOW",
        expected_connection_revision=2,
        expected_binding_revision=7,
        desired_binding_status="READ_ONLY",
        observed_versions={"binding_revision": 7},
        operations=[{"operation": "set_binding_status", "to": "READ_ONLY"}],
        gate_results={"drift": 0},
        evidence_refs={"reconciliation_run_id": str(uuid4())},
        rollback_operations=[{"operation": "set_binding_status", "to": "SHADOW"}],
        created_by="operator",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    digest = plan.digest()
    plan.status = LifecyclePlanStatus.APPROVED
    assert plan.digest() == digest
    assert plan.digest() == PMLifecycleTransitionPlan.model_validate(plan.model_dump()).digest()


def test_read_only_binding_policy_keeps_outbound_and_blocks_inbound_mutation() -> None:
    policy = pm_binding_effective_policy("READ_ONLY", "SHADOW", "both")
    assert policy["outbound_projection"] is True
    assert policy["inbound_evidence"] is True
    assert policy["inbound_canonical_mutation"] is False
    active = pm_binding_effective_policy("ACTIVE", "ACTIVE", "both")
    assert active["inbound_canonical_mutation"] is True


def test_provider_url_rejects_embedded_credentials() -> None:
    with pytest.raises(ValueError):
        ProviderConnection(
            provider_kind="youtrack",
            display_name="bad",
            base_url="https://user:password@example.invalid",
            credential_ref="secret",
        )


@pytest.mark.asyncio
async def test_github_repository_scope_rejects_path_traversal() -> None:
    provider = GitHubProvider()
    with pytest.raises(ValueError):
        await provider.project_work_item(
            connection("github", repository="owner/../other"),
            item(),
            idempotency_key="scope-check",
        )


@pytest.mark.asyncio
async def test_github_ref_validation_rejects_path_and_control_characters() -> None:
    provider = GitHubProvider()
    with pytest.raises(ValueError):
        provider._safe_git_ref("refs/heads/../../main", field="branch")
    with pytest.raises(ValueError):
        provider._safe_git_ref("feature?query", field="branch")
    with pytest.raises(ValueError):
        await provider.read_work_item(connection("github", repository="acme/app"), "../../etc/passwd")


@pytest.mark.asyncio
async def test_github_list_changes_paginates_before_advancing_cursor() -> None:
    provider = GitHubProvider()
    first_page = [
        {
            "number": index,
            "title": f"issue {index}",
            "body": "body",
            "state": "open",
            "updated_at": "2026-07-28T00:00:00Z",
        }
        for index in range(1, 101)
    ]
    second_page = [
        {
            "number": 101,
            "title": "issue 101",
            "body": "body",
            "state": "open",
            "updated_at": "2026-07-29T00:00:00Z",
        }
    ]
    provider.http.request = AsyncMock(
        side_effect=[
            httpx.Response(200, json=first_page, request=httpx.Request("GET", "https://api.github.com/issues")),
            httpx.Response(200, json=second_page, request=httpx.Request("GET", "https://api.github.com/issues")),
        ]
    )

    objects, cursor = await provider.list_changes(connection("github", repository="acme/app"))

    assert len(objects) == 101
    assert objects[-1].external_id == "101"
    assert cursor == "2026-07-29T00:00:00Z"
    assert provider.http.request.await_args_list[0].kwargs["params"]["page"] == 1
    assert provider.http.request.await_args_list[1].kwargs["params"]["page"] == 2


@pytest.mark.asyncio
async def test_youtrack_projection_uses_provider_port_and_idempotency() -> None:
    provider = YouTrackProvider()
    provider.http.request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"id": "YT-42", "idReadable": "AIAT-42", "updated": 7},
            request=httpx.Request("POST", "https://youtrack.example/api/issues"),
        )
    )
    conn = connection("youtrack", project_id="0-0")
    result = await provider.project_work_item(conn, item(), idempotency_key="yt-1")
    assert result.external_id == "YT-42"
    provider.http.request.assert_awaited_once()
    call = provider.http.request.await_args
    assert call.args[1] == "POST"
    assert call.kwargs["headers"]["Idempotency-Key"] == "yt-1"


@pytest.mark.asyncio
async def test_youtrack_list_changes_paginates_before_advancing_cursor() -> None:
    provider = YouTrackProvider()
    first_page = [
        {
            "id": f"issue-{index}",
            "idReadable": f"AIAT-{index}",
            "summary": f"issue {index}",
            "updated": 1000,
            "project": {"id": "0-1"},
        }
        for index in range(1, 101)
    ]
    second_page = [
        {
            "id": "issue-101",
            "idReadable": "AIAT-101",
            "summary": "issue 101",
            "updated": 2000,
            "project": {"id": "0-1"},
        }
    ]
    provider.http.request = AsyncMock(
        side_effect=[
            httpx.Response(200, json=first_page, request=httpx.Request("GET", "https://youtrack.example/api/issues")),
            httpx.Response(200, json=second_page, request=httpx.Request("GET", "https://youtrack.example/api/issues")),
        ]
    )

    objects, cursor = await provider.list_changes(connection("youtrack", project_id="0-1"))

    assert len(objects) == 101
    assert objects[-1].external_id == "issue-101"
    assert cursor == "2000"
    assert provider.http.request.await_args_list[0].kwargs["params"]["$skip"] == 0
    assert provider.http.request.await_args_list[1].kwargs["params"]["$skip"] == 100


@pytest.mark.asyncio
async def test_youtrack_projection_rejects_identifier_path_injection() -> None:
    provider = YouTrackProvider()
    with pytest.raises(ValueError):
        await provider.project_work_item(
            connection("youtrack", project_id="0-0"),
            item(),
            external_id="../../users/me",
            idempotency_key="yt-scope-check",
        )


@pytest.mark.asyncio
async def test_youtrack_least_privilege_expects_project_creator_and_created_project_owner() -> None:
    provider = YouTrackProvider()
    conn = connection(
        "youtrack",
        project_id="0-0",
        permission_evidence={
            "global_roles": ["Observer", "Project Creator"],
            "global_permissions": ["Create Project"],
            "project_roles": {"0-0": ["Project Admin"]},
            "created_project_ownership": True,
        },
    )
    report = await provider.verify_least_privilege(conn)
    assert report["ok"] is True
    assert report["observed"]["global"] == ["create_project", "observer", "project_creator"]
    assert report["expected"]["global_permissions"] == ["Create Project"]
    assert report["deletion_policy"]["permanent_delete"] == "explicit_operator_approval_required"


@pytest.mark.asyncio
async def test_youtrack_least_privilege_does_not_certify_without_live_evidence() -> None:
    report = await YouTrackProvider().verify_least_privilege(connection("youtrack", project_id="0-0"))
    assert report["ok"] is False
    assert report["missing"] == ["permission_evidence (live YouTrack least-privilege certification)"]


@pytest.mark.asyncio
async def test_youtrack_least_privilege_rejects_unrelated_global_admin_and_delete() -> None:
    provider = YouTrackProvider()
    conn = connection(
        "youtrack",
        project_id="0-0",
        permission_evidence={
            "global_roles": ["Observer", "Project Creator", "System Admin", "User Manager"],
            "global_permissions": [
                "Create Project",
                "Low-level Admin Write",
                "Organization Administration",
                "Authentication Administration",
                "Global App Administration",
                "Delete Project",
            ],
            "project_roles": {"0-0": ["Project Admin"]},
            "created_project_ownership": True,
        },
    )
    report = await provider.verify_least_privilege(conn)
    assert report["ok"] is False
    assert {
        "system_admin",
        "user_manager",
        "low_level_admin_write",
        "organization_administration",
        "authentication_administration",
        "global_app_administration",
        "delete_project",
    }.issubset(
        set(report["forbidden"])
    )


@pytest.mark.asyncio
async def test_youtrack_least_privilege_requires_existing_project_admin_evidence() -> None:
    provider = YouTrackProvider()
    conn = connection(
        "youtrack",
        project_id="0-0",
        permission_evidence={
            "global_roles": ["Observer", "Project Creator"],
            "created_project_ownership": True,
        },
    )
    report = await provider.verify_least_privilege(conn)
    assert report["ok"] is False
    assert any("Project Admin" in item for item in report["missing"])


@pytest.mark.asyncio
async def test_youtrack_bootstrap_plans_project_creation_with_creator_ownership() -> None:
    provider = YouTrackProvider()
    provider.http.request = AsyncMock(
        return_value=httpx.Response(
            200,
            json=[],
            request=httpx.Request("GET", "https://youtrack.example/api/admin/projects"),
        )
    )
    plan = await provider.plan_bootstrap(
        connection("youtrack", permission_evidence={
            "global_roles": ["Observer", "Project Creator"],
            "created_project_ownership": True,
        }),
        {"project_name": "AIAT Delivery", "project_short_name": "AIAT"},
    )
    create = next(action for action in plan.actions if action.action == "create_project")
    assert create.desired["owner"] == "integration_user"
    assert create.desired["project_admin"] is True
    assert "desired.project_name" not in " ".join(plan.blockers)


@pytest.mark.asyncio
async def test_youtrack_project_projection_creates_with_integration_user_as_leader() -> None:
    provider = YouTrackProvider()
    provider.http.request = AsyncMock(
        side_effect=[
            httpx.Response(
                200,
                json={"id": "1-1", "login": "aiat-integration"},
                request=httpx.Request("GET", "https://youtrack.example/api/users/me"),
            ),
            httpx.Response(
                200,
                json={"id": "0-42", "shortName": "AIAT"},
                request=httpx.Request("POST", "https://youtrack.example/api/admin/projects"),
            ),
        ]
    )
    conn = connection("youtrack", project_short_name="AIAT")
    result = await provider.project_project(
        conn,
        CanonicalProject(id=uuid4(), name="AIAT Delivery"),
        idempotency_key="project-create",
    )
    assert result.external_id == "0-42"
    create_call = provider.http.request.await_args_list[1]
    assert create_call.args[2] == "/api/admin/projects"
    assert create_call.kwargs["json_body"]["leader"] == {"id": "1-1"}


@pytest.mark.asyncio
async def test_youtrack_project_archive_is_the_only_automated_deactivation_path() -> None:
    provider = YouTrackProvider()
    provider.http.request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"id": "0-42", "archived": True},
            request=httpx.Request("POST", "https://youtrack.example/api/admin/projects/0-42"),
        )
    )
    result = await provider.archive_project(
        connection("youtrack", project_id="0-42"),
        "0-42",
        idempotency_key="project-archive",
    )
    assert result.status.value == "synced"
    assert "permanent deletion" in str(result.message)
    call = provider.http.request.await_args
    assert call.kwargs["json_body"] == {"archived": True}


@pytest.mark.asyncio
async def test_github_source_control_projection_is_repository_scoped() -> None:
    provider = GitHubProvider()
    provider.http.request = AsyncMock(
        return_value=httpx.Response(
            201,
            json={"number": 9, "html_url": "https://github.com/acme/app/pull/9", "updated_at": "2026-07-28T00:00:00Z"},
            request=httpx.Request("POST", "https://api.github.com/repos/acme/app/pulls"),
        )
    )
    conn = connection("github", repository="acme/app", capability_profile="delivery")
    result = await provider.project_pull_request(
        conn,
        {"title": "delivery", "head": "aiat/run", "base": "main", "idempotency_key": "gh-1"},
    )
    assert result.external_id == "9"
    path = provider.http.request.await_args.args[2]
    assert path == "/repos/acme/app/pulls"


@pytest.mark.asyncio
async def test_github_run_credential_uses_injected_server_broker() -> None:
    broker = AsyncMock(return_value={"token": "short-lived", "expires_at": "2026-07-28T01:00:00Z"})
    provider = GitHubProvider(run_credential_broker=broker)
    conn = connection("github", repository="acme/app", capability_profile="delivery")
    result = await provider.mint_run_credential(conn, "acme/app", {"contents": "write"})
    assert result["token"] == "short-lived"
    broker.assert_awaited_once_with(conn, "acme/app", {"contents": "write"})


@pytest.mark.asyncio
async def test_github_api_requests_use_installation_broker_when_app_is_configured() -> None:
    broker = AsyncMock(return_value={"token": "installation", "expires_at": "soon"})
    provider = GitHubProvider(run_credential_broker=broker)
    conn = connection(
        "github",
        repository="acme/app",
        github_app_id="123",
        github_installation_id="77",
    )
    conn = conn.model_copy(update={"capability_profile": "checks"})
    assert await provider._api_token(conn) == "installation"
    broker.assert_awaited_once_with(
        conn,
        "acme/app",
        {"metadata": "read", "issues": "write", "contents": "write", "pull_requests": "write", "checks": "write"},
    )


def test_github_webhook_signature_uses_raw_body() -> None:
    secret = "hook-secret"
    conn = connection("github", webhook_secret_test_only=secret)
    provider = GitHubProvider()
    body = json.dumps({"action": "opened", "issue": {"id": 7}}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert provider.verify_webhook(conn, body, {"X-Hub-Signature-256": f"sha256={signature}"})
    assert not provider.verify_webhook(conn, body + b" ", {"X-Hub-Signature-256": f"sha256={signature}"})


def test_youtrack_webhook_requires_test_only_token_without_async_resolver() -> None:
    provider = YouTrackProvider()
    conn = connection("youtrack", webhook_token_test_only="token", webhook_header="X-YouTrack-Token")
    assert provider.verify_webhook(conn, b"{}", {"X-YouTrack-Token": "token"})
    assert not provider.verify_webhook(conn, b"{}", {"X-YouTrack-Token": "wrong"})


def test_normalized_commands_are_deduplicated_by_connection_delivery() -> None:
    provider = FakeProvider()
    conn = connection()
    event = ExternalEvent(
        connection_id=conn.id,
        provider_delivery_id="delivery-1",
        event_type="work_item.updated",
        payload={"object": {"external_id": "42", "provider_version": "7", "fields": {"status": "done"}}},
        verified=True,
    )
    command = provider.normalize_webhook(event)
    assert command is not None
    assert command.idempotency_key == f"{conn.id}:delivery-1"
    assert command.expected_provider_version == "7"


def test_youtrack_webhook_resolves_readable_issue_and_comments_shape() -> None:
    provider = YouTrackProvider()
    conn = connection("youtrack")
    event = ExternalEvent(
        connection_id=conn.id,
        provider_delivery_id="delivery-youtrack-1",
        event_type="commentUpdated",
        payload={
            "id": "AIAT-3",
            "project": {"id": "0-1", "shortName": "AIAT"},
            "comments": [{"text": "human edit", "author": {"login": "admin"}}],
            "timestamp": "2026-07-29T21:00:00Z",
        },
        verified=True,
    )
    command = provider.normalize_webhook(event)
    assert command is not None
    assert command.external_id == "AIAT-3"
    assert command.operation == "comment"
    assert command.fields["comment"] == "human edit"
    assert command.actor is not None
    assert command.actor.actor_id == "admin"


def test_youtrack_issue_updated_preserves_aiat_revision_marker() -> None:
    provider = YouTrackProvider()
    conn = connection("youtrack")
    event = ExternalEvent(
        connection_id=conn.id,
        provider_delivery_id="delivery-youtrack-echo",
        event_type="issueUpdated",
        payload={
            "id": "3-23",
            "summary": "canonical",
            "description": "projected",
            "status": "backlog",
            "priority": "medium",
            "project": {"id": "0-1", "shortName": "AIAT"},
            "updatedBy": {"login": "AIAT_Agents"},
            "changedFields": [{"name": "AIAT Revision", "value": 2, "oldValue": 1}],
            "updated": 1785370223232,
        },
        verified=True,
    )
    command = provider.normalize_webhook(event)
    assert command is not None
    assert command.fields["_aiat_marker_revision"] == 2
    assert command.fields["title"] == "canonical"
    assert command.fields["description"] == "projected"


def test_lifecycle_digest_excludes_execution_status_but_covers_operations() -> None:
    from datetime import UTC, datetime, timedelta
    from mas_core.integrations.contracts import LifecyclePlanStatus, PMLifecycleTransitionPlan

    now = datetime(2026, 7, 29, tzinfo=UTC)
    plan = PMLifecycleTransitionPlan(
        plan_id=uuid4(),
        target_type="pm_binding",
        target_id=uuid4(),
        connection_id=uuid4(),
        binding_id=uuid4(),
        expected_connection_status="SHADOW",
        expected_binding_status="SHADOW",
        expected_connection_revision=3,
        expected_binding_revision=7,
        desired_binding_status="READ_ONLY",
        operations=[{"operation": "set_binding_status", "from": "SHADOW", "to": "READ_ONLY"}],
        rollback_operations=[{"operation": "set_binding_status", "from": "READ_ONLY", "to": "SHADOW"}],
        created_by="operator",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    digest = plan.digest()
    plan.status = LifecyclePlanStatus.APPROVED
    assert plan.digest() == digest
    plan.operations[0]["to"] = "ACTIVE"
    assert plan.digest() != digest


def test_read_only_binding_with_shadow_connection_has_outbound_only_policy() -> None:
    from mas_core.integrations.contracts import pm_binding_effective_policy

    policy = pm_binding_effective_policy("READ_ONLY", "SHADOW", "both")
    assert policy["outbound_projection"] is True
    assert policy["inbound_evidence"] is True
    assert policy["inbound_canonical_mutation"] is False
