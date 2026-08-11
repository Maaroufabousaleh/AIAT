"""
Tests for Projects CRUD endpoints:
  POST /projects
  GET  /projects
  GET  /projects/{id}
  DELETE /projects/{id}
  POST /projects/{id}/retry
  POST /projects/{id}/archive
  GET  /projects/{id}/allowed-transitions
  GET  /projects/{id}/state-history
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from conftest import PROJECT_ID, _fake_project

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_storage(project=None, projects=None):
    """Build a mock storage that looks real enough for the endpoints."""
    storage = MagicMock()
    storage.create_project = AsyncMock(return_value=_fake_project("INIT"))
    storage.get_project = AsyncMock(return_value=project)
    storage.delete_project = AsyncMock(return_value=project is not None)
    storage.list_projects = AsyncMock(return_value=projects if projects is not None else [])
    storage.get_project_history = AsyncMock(return_value=[])
    return storage


def _make_controller(result=None):
    """Build a mock WorkflowController."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    if result is None:
        result = WorkflowTransitionResult(
            project_id=str(PROJECT_ID),
            prior_state=ProjectState.INIT,
            event=WorkflowEvent.PROJECT_CREATED,
            next_state=ProjectState.FEASIBILITY_CHECK,
            actor_id="human",
            context={},
        )
    ctrl = MagicMock()
    ctrl.transition = AsyncMock(return_value=result)
    return ctrl


def _patch_state(storage, controller=None):
    """Directly set app.state attributes (bypasses monkeypatch limitation)."""
    from orchestrator_api.main import app

    app.state.storage = storage
    if controller is not None:
        app.state.controller = controller


# ── POST /projects ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_project_happy_path(client):
    """POST /projects creates a project and returns 201."""
    storage = _make_storage()
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=201)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post(
            "/projects",
            json={"name": "My Project", "description": "test", "human_requester": "alice"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Project"
    assert data["state"] == "INIT"
    storage.create_project.assert_awaited_once()


@pytest.mark.anyio
async def test_create_project_without_storage(client):
    """POST /projects returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.post("/projects", json={"name": "X"})
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_create_project_validation_error(client):
    """POST /projects returns 422 when name is missing."""
    resp = await client.post("/projects", json={})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_create_project_minimal_payload(client):
    """POST /projects succeeds with only name (description/requester optional)."""
    storage = _make_storage()
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=201)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post("/projects", json={"name": "Minimal"})

    assert resp.status_code == 201


@pytest.mark.anyio
async def test_create_project_retries_feasibility_when_router_rejects(client):
    """A rejected initial directive is retained for state-scoped delivery retry."""
    storage = _make_storage(project=_fake_project("FEASIBILITY_CHECK"))
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with (
        patch(
            "orchestrator_api.main._publish_router_envelope",
            new=AsyncMock(return_value=False),
        ) as publish,
        patch("orchestrator_api.main._schedule_stage_directive_retry") as schedule_retry,
    ):
        resp = await client.post(
            "/projects",
            json={"name": "Retry me", "description": "Router may be unavailable"},
        )

    assert resp.status_code == 201
    publish.assert_awaited_once()
    directive = publish.await_args.args[0]
    assert directive["payload"]["action"] == "START_FEASIBILITY"
    assert directive["payload"]["project_name"] == "Retry me"
    assert directive["payload"]["description"] == "Router may be unavailable"
    schedule_retry.assert_called_once_with(
        str(PROJECT_ID), "FEASIBILITY_CHECK", directive
    )


@pytest.mark.anyio
async def test_create_project_persists_initial_context_and_flow(client):
    """Creation forwards the starter brief and selected flow atomically."""
    storage = _make_storage()
    flow_id = UUID("00000000-0000-4000-a000-0000000000b1")
    storage.get_flow = AsyncMock(return_value={"id": flow_id, "version": 3})
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=201)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post(
            "/projects",
            json={
                "name": "  Context-rich project  ",
                "flow_id": str(flow_id),
                "config": {"repository_url": "https://github.com/org/repo"},
                "initial_context": [
                    {
                        "item_type": "text",
                        "name": "Requirements",
                        "content_text": "Build a safe project workspace.",
                        "tags": ["requirements"],
                    }
                ],
            },
        )

    assert resp.status_code == 201
    request = storage.create_project.await_args.kwargs
    assert request["name"] == "Context-rich project"
    assert request["initial_context"][0]["item_type"] == "TEXT"
    assert request["initial_context"][0]["created_by"] == "human"
    assert request["config"]["repository_url"].endswith("/repo")


@pytest.mark.anyio
async def test_create_project_provisions_managed_git_workspace(client):
    """Creation provisions the project-scoped Git workspace before the workflow starts."""
    storage = _make_storage()
    ctrl = _make_controller()
    _patch_state(storage, ctrl)
    provision = AsyncMock(
        return_value={
            "initialized": True,
            "workspace_path": "/workspace/project-1",
            "workspace_relative_path": "project-1",
            "branch": "main",
            "head": "abc123",
            "remote": None,
            "clean": True,
        }
    )

    with patch("orchestrator_api.main._invoke_project_repository_tool", provision), patch(
        "httpx.AsyncClient"
    ) as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=201)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post(
            "/projects",
            json={
                "name": "Git project",
                "workspace": {"mode": "init", "branch": "main"},
            },
        )

    assert resp.status_code == 201
    provision.assert_awaited_once()
    assert provision.await_args.kwargs["operation"] == "init"
    assert storage.create_project.await_args.kwargs["config"]["workspace"]["status"] == "PROVISIONING"


# ── GET /projects ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_projects_empty(client):
    """GET /projects returns empty list when no projects exist."""
    _patch_state(_make_storage(projects=[]))
    resp = await client.get("/projects")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_projects_returns_all(client):
    """GET /projects returns all stored projects."""
    projects = [_fake_project("INIT"), _fake_project("FEASIBILITY_CHECK")]
    _patch_state(_make_storage(projects=projects))
    resp = await client.get("/projects")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.anyio
async def test_list_projects_with_state_filter(client):
    """GET /projects?state=INIT forwards state filter to storage."""
    storage = _make_storage(projects=[_fake_project("INIT")])
    _patch_state(storage)
    resp = await client.get("/projects?state=INIT")
    assert resp.status_code == 200
    storage.list_projects.assert_awaited_once_with(state="INIT", limit=100, offset=0)


@pytest.mark.anyio
async def test_list_projects_pagination(client):
    """GET /projects?limit=10&offset=5 passes pagination params."""
    storage = _make_storage(projects=[])
    _patch_state(storage)
    resp = await client.get("/projects?limit=10&offset=5")
    assert resp.status_code == 200
    storage.list_projects.assert_awaited_once_with(state=None, limit=10, offset=5)


@pytest.mark.anyio
async def test_list_projects_invalid_limit(client):
    """GET /projects returns 422 for limit < 1."""
    resp = await client.get("/projects?limit=0")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_list_projects_503_no_storage(client):
    """GET /projects returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.get("/projects")
    assert resp.status_code == 503


# ── GET /projects/{id} ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_project_found(client):
    """GET /projects/{id} returns 200 with project data."""
    project = _fake_project("IN_PROGRESS")
    _patch_state(_make_storage(project=project))
    resp = await client.get(f"/projects/{PROJECT_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "IN_PROGRESS"


@pytest.mark.anyio
async def test_get_project_not_found(client):
    """GET /projects/{id} returns 404 when project does not exist."""
    _patch_state(_make_storage(project=None))
    resp = await client.get(f"/projects/{PROJECT_ID}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_project_invalid_uuid(client):
    """GET /projects/{id} returns 422 for non-UUID path param."""
    resp = await client.get("/projects/not-a-uuid")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_project_503_no_storage(client):
    """GET /projects/{id} returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_list_project_issues_reads_persisted_records(client):
    sprint_id = "00000000-0000-4000-a000-000000000010"
    storage = _make_storage(project=_fake_project("IN_PROGRESS"))
    storage.list_issues = AsyncMock(
        return_value=[
            {
                "id": "00000000-0000-4000-a000-000000000099",
                "project_id": PROJECT_ID,
                "sprint_id": sprint_id,
                "status": "backlog",
                "title": "Fix boundary",
                "assigned_team": "dept_qa",
            }
        ]
    )
    _patch_state(storage)

    resp = await client.get(
        f"/projects/{PROJECT_ID}/issues?sprint_id={sprint_id}"
        "&status=backlog&assigned_team=dept_qa"
    )

    assert resp.status_code == 200
    assert [issue["title"] for issue in resp.json()] == ["Fix boundary"]
    storage.list_issues.assert_awaited_once_with(
        project_id=PROJECT_ID,
        sprint_id=UUID(sprint_id),
        status="backlog",
        assigned_team="dept_qa",
    )


# ── GET /projects/{id}/allowed-transitions ────────────────────────────────────


@pytest.mark.anyio
async def test_allowed_transitions_known_state(client):
    """GET /projects/{id}/allowed-transitions returns valid events."""
    project = _fake_project("INIT")
    _patch_state(_make_storage(project=project))
    resp = await client.get(f"/projects/{PROJECT_ID}/allowed-transitions")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert "allowed_events" in data
    # INIT only allows project_created
    assert "project_created" in data["allowed_events"]


@pytest.mark.anyio
async def test_allowed_transitions_not_found(client):
    """GET /projects/{id}/allowed-transitions returns 404 when project missing."""
    _patch_state(_make_storage(project=None))
    resp = await client.get(f"/projects/{PROJECT_ID}/allowed-transitions")
    assert resp.status_code == 404


# ── GET /projects/{id}/state-history ─────────────────────────────────────────


@pytest.mark.anyio
async def test_state_history_empty(client):
    """GET /projects/{id}/state-history returns empty list when no history."""
    storage = _make_storage()
    storage.get_project_history = AsyncMock(return_value=[])
    _patch_state(storage)
    resp = await client.get(f"/projects/{PROJECT_ID}/state-history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_state_history_503_no_storage(client):
    """GET /projects/{id}/state-history returns 503 when storage is unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}/state-history")
    assert resp.status_code == 503


# ── GET /projects/{id}/evidence ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_project_evidence_uses_project_scoped_approval_gates(client):
    """Evidence remains available for a freshly created, incomplete project."""
    storage = _make_storage(project=_fake_project("FEASIBILITY_CHECK"))
    storage.list_documents = AsyncMock(return_value=[])
    storage.list_artifacts = AsyncMock(return_value=[])
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.list_worker_runs = AsyncMock(return_value=[])
    storage.get_project_repository_record = AsyncMock(return_value=None)
    _patch_state(storage)

    resp = await client.get(f"/projects/{PROJECT_ID}/evidence")

    assert resp.status_code == 200
    assert resp.json()["policy_id"] == "manual"
    assert resp.json()["status"] == "incomplete"
    storage.list_approval_gates.assert_awaited_once_with(project_id=PROJECT_ID)


@pytest.mark.anyio
async def test_project_evidence_package_groups_repository_security_deployment_and_cost_views(client):
    """The package is one read model over the existing project authorities."""

    project = _fake_project("IN_PROGRESS")
    project["config"] = {}
    storage = _make_storage(project=project)
    storage.list_documents = AsyncMock(
        return_value=[
            {"id": "doc-1", "doc_type": "PDR", "status": "APPROVED", "version": 1}
        ]
    )
    storage.list_artifacts = AsyncMock(
        return_value=[
            {
                "id": 1,
                "kind": "security-scan",
                "path": f"{PROJECT_ID}/security.json",
                "sha256": "abc",
                "metadata": {"project_id": str(PROJECT_ID), "license": "notice-only"},
            },
            {
                "id": 2,
                "kind": "deployment",
                "path": f"{PROJECT_ID}/deployment.json",
                "metadata": {"project_id": str(PROJECT_ID)},
            },
        ]
    )
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.list_worker_runs = AsyncMock(return_value=[])
    storage.get_project_repository_record = AsyncMock(
        return_value={
            "id": "repo-1",
            "initialized": True,
            "adapter_health": "ok",
            "branch": "main",
            "head_commit": "deadbeef",
        }
    )
    storage.get_project_history = AsyncMock(return_value=[])
    _patch_state(storage)

    response = await client.get(f"/projects/{PROJECT_ID}/evidence/package")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.project-evidence-package.v1"
    assert {item["category"] for item in body["items"]} >= {"security", "deployment", "repository"}
    assert body["notices"] == [
        {"artifact_id": "1", "field": "license", "value": "notice-only"}
    ]
    assert body["snapshot"] is None


@pytest.mark.anyio
async def test_project_evidence_package_snapshot_is_operator_only_and_idempotent(client):
    project = _fake_project("IN_PROGRESS")
    project["config"] = {}
    storage = _make_storage(project=project)
    storage.list_documents = AsyncMock(return_value=[])
    storage.list_artifacts = AsyncMock(return_value=[])
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.list_worker_runs = AsyncMock(return_value=[])
    storage.get_project_repository_record = AsyncMock(return_value=None)
    storage.get_project_history = AsyncMock(return_value=[])
    storage.create_project_evidence_package = AsyncMock(
        return_value={"id": "snapshot-1", "project_id": PROJECT_ID, "status": "incomplete"}
    )
    _patch_state(storage)

    denied = await client.post(f"/projects/{PROJECT_ID}/evidence/package")
    assert denied.status_code == 403

    allowed = await client.post(
        f"/projects/{PROJECT_ID}/evidence/package",
        headers={"X-API-Key": "test-operator-key"},
    )
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["stored"] is True
    assert body["snapshot"]["id"] == "snapshot-1"
    storage.create_project_evidence_package.assert_awaited_once()


@pytest.mark.anyio
async def test_project_evidence_resolves_milestone_before_company_default(client):
    project = _fake_project("IN_PROGRESS")
    project["company_id"] = "00000000-0000-4000-8000-000000000001"
    project["config"] = {}
    storage = _make_storage(project=project)
    storage.list_documents = AsyncMock(return_value=[])
    storage.list_artifacts = AsyncMock(return_value=[])
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.list_worker_runs = AsyncMock(return_value=[])
    storage.get_project_repository_record = AsyncMock(return_value=None)
    storage.list_sprints = AsyncMock(return_value=[{
        "milestone": "implementation", "sprint_number": 2, "status": "ACTIVE"
    }])
    storage.get_company_manifest = AsyncMock(return_value={
        "manifest_json": {
            "evidence_policy": {
                "default_policy": {"policy_id": "software_delivery", "version": "1.0", "requirements": {}},
                "milestone_policies": {
                    "implementation": {"policy_id": "operations", "version": "1.0", "requirements": {}}
                },
            }
        }
    })
    _patch_state(storage)

    response = await client.get(f"/projects/{PROJECT_ID}/evidence")

    assert response.status_code == 200
    assert response.json()["policy_id"] == "operations"


@pytest.mark.anyio
async def test_evidence_policy_catalog_is_available_to_operator_clients(client):
    response = await client.get("/evidence-policies")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.evidence-policy.v1"
    assert "software_delivery" in body["policies"]
    assert body["policies"]["software_delivery"]["required_document_types"] == ["PDR", "CDR", "RR"]


@pytest.mark.anyio
async def test_project_evidence_policy_selection_is_persisted(client):
    project = _fake_project("IN_PROGRESS")
    project["config"] = {}
    storage = _make_storage(project=project)
    storage.update_project_config = AsyncMock(return_value={**project, "config": {
        "evidence_policy": {
            "policy_id": "software_delivery",
            "version": "1.0",
            "requirements": {},
        }
    }})
    _patch_state(storage)

    response = await client.put(
        f"/projects/{PROJECT_ID}/evidence-policy",
        headers={"X-API-Key": "test-operator-key"},
        json={"policy_id": "software_delivery", "policy_version": "1.0", "requirements": {}},
    )

    assert response.status_code == 200
    assert response.json()["evidence_policy"]["policy_id"] == "software_delivery"
    storage.update_project_config.assert_awaited_once_with(
        PROJECT_ID,
        config={"evidence_policy": {"policy_id": "software_delivery", "version": "1.0", "requirements": {}}},
    )


@pytest.mark.anyio
async def test_milestone_evidence_policy_selection_is_persisted(client):
    project = _fake_project("IN_PROGRESS")
    project["config"] = {}
    storage = _make_storage(project=project)
    storage.update_project_config = AsyncMock(return_value={**project, "config": {
        "evidence_policy_milestones": {
            "implementation": {
                "policy_id": "operations",
                "version": "1.0",
                "requirements": {"required_artifact_kinds": ["deployment"]},
            }
        }
    }})
    _patch_state(storage)

    response = await client.put(
        f"/projects/{PROJECT_ID}/evidence-policy",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "policy_id": "operations",
            "policy_version": "1.0",
            "requirements": {"required_artifact_kinds": ["deployment"]},
            "scope": "milestone",
            "milestone": "implementation",
        },
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "milestone"
    assert response.json()["milestone"] == "implementation"
    storage.update_project_config.assert_awaited_once_with(
        PROJECT_ID,
        config={
            "evidence_policy_milestones": {
                "implementation": {
                    "policy_id": "operations",
                    "version": "1.0",
                    "requirements": {"required_artifact_kinds": ["deployment"]},
                }
            }
        },
    )


@pytest.mark.anyio
async def test_company_evidence_policy_is_persisted_in_manifest(client):
    from unittest.mock import patch

    from mas_core.company_manifest import DEFAULT_COMPANY_ID

    storage = _make_storage(project=None)
    storage.get_company = AsyncMock(return_value={"id": DEFAULT_COMPANY_ID})
    storage.get_company_manifest = AsyncMock(return_value={
        "manifest_json": {"slug": "aiat-default", "evidence_policy": {"required_for_completion": []}}
    })
    storage.apply_company_manifest = AsyncMock(return_value={"company": {"id": DEFAULT_COMPANY_ID}})
    _patch_state(storage)
    compiled_manifest = MagicMock()
    compiled_manifest_result = (compiled_manifest, "digest", {
        "slug": "aiat-default",
        "evidence_policy": {
            "default_policy": {
                "policy_id": "software_delivery",
                "version": "1.0",
                "requirements": {},
            }
        },
    })

    with patch("orchestrator_api.main.compile_company_manifest", return_value=compiled_manifest_result):
        response = await client.put(
            f"/companies/{DEFAULT_COMPANY_ID}/evidence-policy",
            headers={"X-API-Key": "test-operator-key"},
            json={"policy_id": "software_delivery", "policy_version": "1.0", "requirements": {}},
        )

    assert response.status_code == 200
    assert response.json()["evidence_policy"]["policy_id"] == "software_delivery"
    storage.apply_company_manifest.assert_awaited_once_with(
        company_id=DEFAULT_COMPANY_ID,
        manifest=compiled_manifest,
        digest="digest",
        canonical=compiled_manifest_result[2],
        source="api:company-evidence-policy",
        actor="operator",
    )


# ── POST /projects/{id}/retry ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_retry_failed_project(client):
    """POST /projects/{id}/retry succeeds for a FAILED project."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("FAILED")
    project["failed_from_state"] = "IN_PROGRESS"
    storage = _make_storage(project=project)

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.FAILED,
        event=WorkflowEvent.RETRY,
        next_state=ProjectState.IN_PROGRESS,
        actor_id="human",
        context={"last_safe_state": "IN_PROGRESS"},
    )
    ctrl = _make_controller(result=result)
    _patch_state(storage, ctrl)

    resp = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "retried"


@pytest.mark.anyio
async def test_retry_failed_project_recreates_human_checkpoint_gate(client):
    """Retrying into a human checkpoint restores its pending approval gate."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("FAILED")
    project["failed_from_state"] = "FEASIBILITY_REPORT"
    storage = _make_storage(project=project)
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.create_approval_gate = AsyncMock()

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.FAILED,
        event=WorkflowEvent.RETRY,
        next_state=ProjectState.FEASIBILITY_REPORT,
        actor_id="human",
        context={"last_safe_state": "FEASIBILITY_REPORT"},
    )
    _patch_state(storage, _make_controller(result=result))

    resp = await client.post(f"/projects/{PROJECT_ID}/retry")

    assert resp.status_code == 200
    storage.list_approval_gates.assert_awaited_once_with(
        project_id=PROJECT_ID,
        status="PENDING",
        limit=100,
    )
    storage.create_approval_gate.assert_awaited_once_with(
        project_id=PROJECT_ID,
        gate_type="feasibility",
    )


@pytest.mark.anyio
async def test_retry_non_failed_project_returns_409(client):
    """POST /projects/{id}/retry returns 409 if project is not in FAILED state."""
    project = _fake_project("IN_PROGRESS")
    _patch_state(_make_storage(project=project), _make_controller())
    resp = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_retry_not_found_returns_404(client):
    """POST /projects/{id}/retry returns 404 when project does not exist."""
    _patch_state(_make_storage(project=None))
    resp = await client.post(f"/projects/{PROJECT_ID}/retry")
    assert resp.status_code == 404


# ── POST /projects/{id}/archive ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_archive_completed_project(client):
    """POST /projects/{id}/archive succeeds for a COMPLETED project."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("COMPLETED")
    storage = _make_storage(project=project)

    result = WorkflowTransitionResult(
        project_id=str(PROJECT_ID),
        prior_state=ProjectState.COMPLETED,
        event=WorkflowEvent.ARCHIVE_REQUESTED,
        next_state=ProjectState.ARCHIVED,
        actor_id="human",
        context={},
    )
    ctrl = _make_controller(result=result)
    _patch_state(storage, ctrl)

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=200)))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post(f"/projects/{PROJECT_ID}/archive")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "archived"


@pytest.mark.anyio
async def test_archive_not_found_returns_404(client):
    """POST /projects/{id}/archive returns 404 when project does not exist."""
    _patch_state(_make_storage(project=None))
    resp = await client.post(f"/projects/{PROJECT_ID}/archive")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_archive_archived_project_is_idempotent(client):
    """POST /projects/{id}/archive succeeds when project is already archived."""
    storage = _make_storage(project=_fake_project("ARCHIVED"))
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    resp = await client.post(f"/projects/{PROJECT_ID}/archive")

    assert resp.status_code == 200
    assert resp.json() == {"status": "archived", "next_state": "ARCHIVED"}
    ctrl.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_project_happy_path(client):
    """DELETE /projects/{id} permanently deletes a project."""
    storage = _make_storage(project=_fake_project("ARCHIVED"))
    _patch_state(storage)

    resp = await client.delete(f"/projects/{PROJECT_ID}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    storage.delete_project.assert_awaited_once_with(PROJECT_ID)


@pytest.mark.anyio
async def test_delete_project_cleans_up_managed_workspace(client):
    """Deleting a project removes its project-scoped Git workspace first."""
    project = _fake_project("ARCHIVED")
    project["config"] = {
        "workspace": {
            "mode": "init",
            "workspace_relative_path": str(PROJECT_ID),
            "remote_name": "origin",
        }
    }
    storage = _make_storage(project=project)
    _patch_state(storage)
    cleanup = AsyncMock(return_value={"removed": True})

    with patch("orchestrator_api.main._invoke_project_repository_tool", cleanup):
        resp = await client.delete(f"/projects/{PROJECT_ID}")

    assert resp.status_code == 200
    cleanup.assert_awaited_once()
    assert cleanup.await_args.kwargs["operation"] == "remove"


@pytest.mark.anyio
async def test_delete_project_not_found_returns_404(client):
    """DELETE /projects/{id} returns 404 when project does not exist."""
    storage = _make_storage(project=None)
    storage.delete_project = AsyncMock(return_value=False)
    _patch_state(storage)

    resp = await client.delete(f"/projects/{PROJECT_ID}")

    assert resp.status_code == 404
