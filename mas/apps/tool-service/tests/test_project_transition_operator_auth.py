from __future__ import annotations

import pytest

from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest


@pytest.mark.anyio
async def test_governed_project_transition_tools_use_operator_credential(monkeypatch):
    from tool_service.tools import infra, project

    calls = []

    async def fake_post(path, body=None, *, context=None, principal="service"):
        calls.append(
            {
                "path": path,
                "body": body,
                "context": context,
                "principal": principal,
            }
        )
        return {"next_state": "IN_PROGRESS"}

    monkeypatch.setattr(project, "orch_post", fake_post)
    monkeypatch.setattr(infra, "orch_post", fake_post)
    context = {
        "caller_id": "signed-executive",
        "caller_role": "executive",
        "caller_team": "office_ceo",
        "project_id": "project-1",
    }

    await project.ProjectTransitionTool().execute(
        project_id="project-1",
        event="work_started",
        _aiat_context=context,
    )
    await project.DocumentSubmitTool().execute(
        project_id="project-1",
        doc_type="PDR",
        _aiat_context=context,
    )
    await project.ReviewAggregateTool().execute(
        project_id="project-1",
        verdict="APPROVED",
        _aiat_context=context,
    )
    await project.ApprovalOverrideCSOTool().execute(
        project_id="project-1",
        action="block",
        _aiat_context=context,
    )
    await infra.InfraReadySignalTool().execute(
        project_id="project-1",
        _aiat_context=context,
    )

    assert [call["body"]["event"] for call in calls] == [
        "work_started",
        "pdr_submitted",
        "all_reviews_in",
        "cso_veto",
        "infra_ready",
    ]
    assert all(call["principal"] == "operator" for call in calls)
    assert all(call["context"] == context for call in calls)
    assert all(call["path"] == "/projects/project-1/transition" for call in calls)


@pytest.mark.anyio
async def test_worker_role_is_denied_before_operator_transition_call(
    make_registry, monkeypatch
):
    from tool_service.tools import project

    calls = []

    async def fake_post(path, body=None, *, context=None, principal="service"):
        calls.append((path, body, principal))
        return {"next_state": "IN_PROGRESS"}

    monkeypatch.setattr(project, "orch_post", fake_post)
    response = await make_registry().execute(
        ToolRequest(
            caller_id="worker-1",
            caller_role=AgentRole.WORKER,
            caller_team="office_engineering",
            tool_name="project.transition",
            kwargs={"project_id": "project-1", "event": "work_started"},
        )
    )

    assert response.success is False
    assert response.error_code == "FORBIDDEN"
    assert calls == []
