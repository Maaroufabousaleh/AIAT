from __future__ import annotations

import pytest


def test_orchestrator_auth_uses_distinct_operator_credential(monkeypatch):
    from tool_service.tools._orch_client import _auth_headers

    monkeypatch.setenv("MAS_API_KEY", "service-key")
    monkeypatch.setenv("AIAT_OPERATOR_API_KEY", "operator-key")
    context = {"caller_id": "cto-agent", "caller_role": "c_suite"}

    assert _auth_headers(context)["X-API-Key"] == "service-key"
    assert _auth_headers(context, principal="operator") == {
        "X-API-Key": "operator-key",
        "X-AIAT-Actor-Role": "c_suite",
        "X-AIAT-Actor-ID": "cto-agent",
    }


def test_orchestrator_auth_fails_closed_without_operator_credential(monkeypatch):
    from tool_service.tools._orch_client import _auth_headers

    monkeypatch.delenv("AIAT_OPERATOR_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="AIAT_OPERATOR_API_KEY"):
        _auth_headers(principal="operator")


@pytest.mark.anyio
async def test_issue_comment_uses_signed_caller_and_operator_principal(monkeypatch):
    from tool_service.tools import pm

    captured = {}

    async def fake_post(path, body=None, **kwargs):
        captured.update(path=path, body=body, kwargs=kwargs)
        return {"comment": body}

    monkeypatch.setattr(pm, "orch_post", fake_post)
    context = {"caller_id": "signed-cto", "caller_role": "c_suite"}
    result = await pm.IssueCommentTool().execute(
        project_id="project-1",
        issue_id="issue-1",
        body="governed comment",
        actor_id="forged-actor",
        agent_id="another-forged-actor",
        _aiat_context=context,
    )

    assert result["comment"]["actor_id"] == "signed-cto"
    assert captured == {
        "path": "/projects/project-1/issues/issue-1/comments",
        "body": {"body": "governed comment", "actor_id": "signed-cto"},
        "kwargs": {"context": context, "principal": "operator"},
    }


@pytest.mark.anyio
async def test_issue_comment_requires_signed_caller_context():
    from tool_service.tools.pm import IssueCommentTool

    with pytest.raises(ValueError, match="signed caller context"):
        await IssueCommentTool().execute(
            project_id="project-1",
            issue_id="issue-1",
            body="unattributed comment",
            actor_id="forged-actor",
        )


@pytest.mark.anyio
async def test_typed_issue_and_scm_writes_request_operator_principal(monkeypatch):
    from tool_service.tools import scm, sprint_kpi

    calls = []

    async def fake_post(path, body=None, **kwargs):
        calls.append((path, body, kwargs))
        return {"ok": True}

    monkeypatch.setattr(sprint_kpi, "orch_post", fake_post)
    monkeypatch.setattr(scm, "orch_post", fake_post)
    context = {"caller_id": "signed-cto", "caller_role": "c_suite"}

    await sprint_kpi.IssueCreateTool().execute(
        project_id="project-1",
        title="typed issue",
        _aiat_context=context,
    )
    await scm.SCMBranchCreateTool().execute(
        connection_id="connection-1",
        branch="review-fix",
        _aiat_context=context,
    )

    assert [call[2]["principal"] for call in calls] == ["operator", "operator"]
    assert all(call[2]["context"] == context for call in calls)
