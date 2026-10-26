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
from uuid import UUID, uuid4

import pytest
from conftest import PROJECT_ID, _fake_project

OPERATOR_HEADERS = {"X-API-Key": "test-operator-key"}

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
            headers=OPERATOR_HEADERS,
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
    resp = await client.post(
        "/projects", json={"name": "X"}, headers=OPERATOR_HEADERS
    )
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

        resp = await client.post(
            "/projects", json={"name": "Minimal"}, headers=OPERATOR_HEADERS
        )

    assert resp.status_code == 201


@pytest.mark.anyio
async def test_create_project_can_remain_prepared_without_starting_ceo_work(client):
    """An operator can create a project before queuing provider-backed work."""
    project = _fake_project("INIT")
    storage = _make_storage(project=project)
    ctrl = _make_controller()
    _patch_state(storage, ctrl)
    provision_workspace = AsyncMock()

    with (
        patch(
            "orchestrator_api.main._publish_router_envelope",
            new=AsyncMock(return_value=True),
        ) as publish,
        patch(
            "orchestrator_api.main._invoke_project_repository_tool",
            new=provision_workspace,
        ),
    ):
        resp = await client.post(
            "/projects",
            json={
                "name": "Prepared project",
                "start_immediately": False,
                "workspace": {"mode": "none"},
            },
            headers=OPERATOR_HEADERS,
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == str(project["id"])
    assert resp.json()["state"] == project["state"] == "INIT"
    storage.create_project.assert_awaited_once()
    assert storage.create_project.await_args.kwargs["created_by"] == "operator"
    assert storage.create_project.await_args.kwargs["human_requester"] == "operator"
    ctrl.transition.assert_not_awaited()
    publish.assert_not_awaited()
    provision_workspace.assert_not_awaited()


@pytest.mark.anyio
async def test_project_create_idempotency_replays_same_record_and_rejects_key_reuse(client):
    """Retries return one project, while a reused key cannot change its payload."""
    projects = {}
    storage = MagicMock()

    async def create_project(**kwargs):
        project_id = kwargs["project_id"]
        if project_id in projects:
            raise RuntimeError("duplicate project id")
        project = {
            "id": project_id,
            "name": kwargs["name"],
            "description": kwargs["description"],
            "state": "INIT",
            "created_by": kwargs["created_by"],
            "config": kwargs["config"],
        }
        projects[project_id] = project
        return project

    storage.create_project = AsyncMock(side_effect=create_project)
    storage.get_project = AsyncMock(side_effect=lambda project_id: projects.get(project_id))
    storage.create_flow_instance = AsyncMock()
    _patch_state(storage, _make_controller())
    payload = {
        "name": "One project despite retries",
        "description": "Stable operation identity",
        "start_immediately": False,
        "idempotency_key": "worker-run-42:project.create:call-7",
    }

    first = await client.post("/projects", json=payload, headers=OPERATOR_HEADERS)
    replay = await client.post("/projects", json=payload, headers=OPERATOR_HEADERS)
    conflict = await client.post(
        "/projects",
        json={**payload, "name": "Different project"},
        headers=OPERATOR_HEADERS,
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    assert storage.create_project.await_count == 1


@pytest.mark.anyio
async def test_create_project_rejects_service_and_worker_before_side_effects(
    client, monkeypatch
):
    """Only the operator may trigger provider-backed CEO work."""
    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    storage = _make_storage()
    ctrl = _make_controller()
    _patch_state(storage, ctrl)
    provision = AsyncMock()

    with (
        patch("orchestrator_api.main._invoke_project_repository_tool", provision),
        patch(
            "orchestrator_api.main._publish_router_envelope",
            new=AsyncMock(return_value=True),
        ) as publish,
    ):
        service_response = await client.post(
            "/projects",
            json={
                "name": "Unauthorized service project",
                "workspace": {"mode": "init"},
            },
            headers={"X-API-Key": "test-mas-key"},
        )
        worker_response = await client.post(
            "/projects",
            json={
                "name": "Unauthorized worker project",
                "workspace": {"mode": "init"},
            },
            headers={"X-API-Key": "test-worker-key"},
        )
        worker_draft_response = await client.post(
            "/projects",
            json={
                "name": "Unauthorized worker draft",
                "start_immediately": False,
                "workspace": {"mode": "none"},
            },
            headers={"X-API-Key": "test-worker-key"},
        )

    assert service_response.status_code == 403
    assert worker_response.status_code == 403
    assert worker_draft_response.status_code == 403
    storage.create_project.assert_not_awaited()
    ctrl.transition.assert_not_awaited()
    provision.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.anyio
async def test_governed_service_can_create_prepared_project_only(client):
    """The CEO project tool may create a draft without starting provider work."""
    storage = _make_storage(project=_fake_project("INIT"))
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with patch(
        "orchestrator_api.main._publish_router_envelope",
        new=AsyncMock(return_value=True),
    ) as publish:
        response = await client.post(
            "/projects",
            json={
                "name": "CEO prepared project",
                "start_immediately": False,
                "workspace": {"mode": "none"},
            },
        )

    assert response.status_code == 201
    assert response.json()["state"] == "INIT"
    assert storage.create_project.await_args.kwargs["created_by"] == "service"
    ctrl.transition.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.anyio
async def test_create_project_uses_transition_publisher_for_one_durable_start(client):
    """The workflow outbox owns the initial CEO directive delivery and replay."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("FEASIBILITY_CHECK", name="Started project")
    storage = _make_storage(project=project)
    ctrl = _make_controller(
        WorkflowTransitionResult(
            project_id=str(PROJECT_ID),
            prior_state=ProjectState.INIT,
            event=WorkflowEvent.PROJECT_CREATED,
            next_state=ProjectState.FEASIBILITY_CHECK,
            actor_id="human",
            context={},
            transition_id=UUID("00000000-0000-4000-a000-000000000043"),
        )
    )
    _patch_state(storage, ctrl)

    with patch(
        "orchestrator_api.main._publish_router_envelope",
        new=AsyncMock(return_value=True),
    ) as publish:
        resp = await client.post(
            "/projects",
            json={"name": "Started project"},
            headers=OPERATOR_HEADERS,
        )

    assert resp.status_code == 201
    ctrl.transition.assert_awaited_once()
    context = ctrl.transition.await_args.kwargs["context"]
    assert context == {"name": "Started project", "description": None}
    assert ctrl.transition.await_args.kwargs["actor_id"] == "operator"
    assert storage.create_project.await_args.kwargs["created_by"] == "operator"
    # The real WorkflowController invokes the durable transition publisher.
    # The route must not send a second directive with a new message identity.
    publish.assert_not_awaited()


@pytest.mark.anyio
async def test_start_prepared_project_transitions_with_durable_directive_intent(client):
    """Starting a prepared project records its durable directive intent."""
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    project = _fake_project("INIT", name="Prepared project")
    storage = _make_storage(project=project)
    ctrl = _make_controller(
        WorkflowTransitionResult(
            project_id=str(PROJECT_ID),
            prior_state=ProjectState.INIT,
            event=WorkflowEvent.PROJECT_CREATED,
            next_state=ProjectState.FEASIBILITY_CHECK,
            actor_id="operator",
            context={},
            transition_id=UUID("00000000-0000-4000-a000-000000000042"),
        )
    )
    _patch_state(storage, ctrl)

    resp = await client.post(
        f"/projects/{PROJECT_ID}/start",
        headers={"X-API-Key": "test-operator-key"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "project_id": str(PROJECT_ID),
        "state": "FEASIBILITY_CHECK",
        "status": "started",
        "directive_delivery": "durably_queued",
    }
    ctrl.transition.assert_awaited_once()
    transition = ctrl.transition.await_args.kwargs
    assert transition["current_state"].value == "INIT"
    assert transition["event"].value == "project_created"
    assert transition["context"]["name"] == "Prepared project"


@pytest.mark.anyio
async def test_start_prepared_project_is_idempotent_after_transition(client):
    """A repeated start cannot emit a second actionable CEO directive."""
    project = _fake_project("FEASIBILITY_CHECK", name="Already started")
    storage = _make_storage(project=project)
    ctrl = _make_controller()
    _patch_state(storage, ctrl)

    with patch(
        "orchestrator_api.main._publish_router_envelope",
        new=AsyncMock(return_value=True),
    ) as publish:
        resp = await client.post(
            f"/projects/{PROJECT_ID}/start",
            headers={"X-API-Key": "test-operator-key"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "already_started"
    ctrl.transition.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.anyio
async def test_start_prepared_project_requires_operator_credentials(client, monkeypatch):
    """Worker and service credentials cannot trigger provider-backed work."""
    from orchestrator_api.main import app

    monkeypatch.setenv("AIAT_WORKER_API_KEY", "test-worker-key")
    app.state.storage = _make_storage(project=_fake_project("INIT"))
    ctrl = _make_controller()
    app.state.controller = ctrl

    service_response = await client.post(f"/projects/{PROJECT_ID}/start")
    worker_response = await client.post(
        f"/projects/{PROJECT_ID}/start",
        headers={"X-API-Key": "test-worker-key"},
    )

    assert service_response.status_code == 403
    assert worker_response.status_code == 403
    ctrl.transition.assert_not_awaited()


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
            headers=OPERATOR_HEADERS,
        )

    assert resp.status_code == 201
    request = storage.create_project.await_args.kwargs
    assert request["name"] == "Context-rich project"
    assert request["initial_context"][0]["item_type"] == "TEXT"
    assert request["initial_context"][0]["created_by"] == "operator"
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
            headers=OPERATOR_HEADERS,
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
    storage = _make_storage(project=_fake_project("INIT"))
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


def _completion_transition_controller():
    from mas_core.workflow import WorkflowEvent, WorkflowTransitionResult
    from mas_core.workflow.states import ProjectState

    controller = MagicMock()
    controller.transition = AsyncMock(
        return_value=WorkflowTransitionResult(
            project_id=str(PROJECT_ID),
            prior_state=ProjectState.KPI_PERSISTENCE,
            event=WorkflowEvent.KPI_SAVED,
            next_state=ProjectState.COMPLETED,
            actor_id="operator",
            context={},
        )
    )
    return controller


def _completion_evidence_storage(project, *, audit_events=()):
    storage = _make_storage(project=project)
    storage.get_flow_instance_by_project = AsyncMock(return_value=None)
    storage.list_documents = AsyncMock(return_value=[])
    storage.list_artifacts = AsyncMock(return_value=[])
    storage.list_approval_gates = AsyncMock(return_value=[])
    storage.list_worker_runs = AsyncMock(return_value=[])
    storage.get_project_repository_record = AsyncMock(return_value=None)
    storage.get_project_history = AsyncMock(return_value=list(audit_events))
    return storage


@pytest.mark.anyio
async def test_project_completion_requires_persisted_software_delivery_evidence(client):
    project = _fake_project("KPI_PERSISTENCE")
    project["config"] = {
        "evidence_policy": {
            "policy_id": "software_delivery",
            "version": "1.0",
            "requirements": {},
        }
    }
    storage = _completion_evidence_storage(project)
    controller = _completion_transition_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={
            "event": "kpi_saved",
            "actor_id": "forged-actor",
            # Transition context cannot override the persisted evidence policy
            # or claim that the evidence checks have passed.
            "context": {
                "evidence_policy": {"policy_id": "manual"},
                "evidence_status": "complete",
            },
        },
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECT_EVIDENCE_INCOMPLETE"
    assert detail["policy_id"] == "software_delivery"
    assert detail["policy_version"] == "1.0"
    assert detail["evidence_status"] == "incomplete"
    assert detail["failed_check_names"] == [
        "flow_terminal",
        "documents_exist",
        "required_document_types",
        "document_bodies_retrievable",
        "artifacts_registered",
        "repository_known",
        "terminal_audit_present",
    ]
    assert detail["failed_check_count"] == len(detail["failed_check_names"])
    assert detail["failed_check_names_truncated"] is False
    controller.transition.assert_not_awaited()


@pytest.mark.anyio
async def test_project_completion_allows_complete_manual_evidence_policy(client):
    project = _fake_project("KPI_PERSISTENCE")
    project["revision"] = 7
    project["config"] = {
        "evidence_policy": {"policy_id": "manual", "version": "1.0", "requirements": {}}
    }
    storage = _completion_evidence_storage(
        project,
        audit_events=[{"id": "history-1", "to_state": "KPI_PERSISTENCE"}],
    )
    controller = _completion_transition_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "kpi_saved", "actor_id": "ignored-body-actor", "context": {}},
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["next_state"] == "COMPLETED"
    controller.transition.assert_awaited_once()
    assert controller.transition.await_args.kwargs["actor_id"] == "operator"
    assert controller.transition.await_args.kwargs["completion_policy"] == {
        "requires_flow_terminal": False,
        "allow_active_runs": False,
    }
    assert controller.transition.await_args.kwargs["expected_revision"] == 7
    storage.get_project_history.assert_awaited_once_with(PROJECT_ID)


@pytest.mark.anyio
async def test_project_completion_rejects_an_active_optional_flow(client):
    project = _fake_project("KPI_PERSISTENCE")
    project["config"] = {
        "evidence_policy": {"policy_id": "manual", "version": "1.0", "requirements": {}}
    }
    storage = _completion_evidence_storage(
        project,
        audit_events=[{"id": "history-1", "to_state": "KPI_PERSISTENCE"}],
    )
    storage.get_flow_instance_by_project = AsyncMock(
        return_value={"id": uuid4(), "project_id": PROJECT_ID, "status": "RUNNING"}
    )
    controller = _completion_transition_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "kpi_saved", "actor_id": "ignored-body-actor", "context": {}},
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROJECT_EVIDENCE_INCOMPLETE"
    assert response.json()["detail"]["failed_check_names"] == ["flow_not_active"]
    controller.transition.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("flow_status", ["FAILED", "CANCELLED"])
async def test_project_completion_requires_successful_required_flow(client, flow_status):
    project = _fake_project("KPI_PERSISTENCE")
    project["config"] = {
        "evidence_policy": {
            "policy_id": "software_delivery",
            "version": "1.0",
            "requirements": {},
        }
    }
    storage = _completion_evidence_storage(
        project,
        audit_events=[{"id": "history-1", "to_state": "KPI_PERSISTENCE"}],
    )
    storage.get_flow_instance_by_project = AsyncMock(
        return_value={"id": uuid4(), "project_id": PROJECT_ID, "status": flow_status}
    )
    storage.list_documents = AsyncMock(
        return_value=[
            {"id": uuid4(), "doc_type": kind, "blob_key": f"projects/{kind}.md"}
            for kind in ("PDR", "CDR", "RR")
        ]
    )
    storage.list_artifacts = AsyncMock(
        return_value=[{"id": uuid4(), "kind": "test-report", "metadata": {"project_id": str(PROJECT_ID)}}]
    )
    storage.get_project_repository_record = AsyncMock(
        return_value={"id": uuid4(), "initialized": True, "adapter_health": "ok"}
    )
    controller = _completion_transition_controller()
    _patch_state(storage, controller)

    response = await client.post(
        f"/projects/{PROJECT_ID}/transition",
        json={"event": "kpi_saved", "actor_id": "ignored-body-actor", "context": {}},
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROJECT_EVIDENCE_INCOMPLETE"
    assert response.json()["detail"]["failed_check_names"] == ["flow_terminal"]
    controller.transition.assert_not_awaited()


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
