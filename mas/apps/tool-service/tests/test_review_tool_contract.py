from unittest.mock import AsyncMock

import pytest


@pytest.mark.anyio
async def test_review_submit_publishes_canonical_response_and_keeps_domain_fields(monkeypatch):
    import tool_service.tools.project as project_mod
    from tool_service.tools.project import ReviewSubmitResponseTool

    publish = AsyncMock(return_value={"entry_id": "router-entry"})
    monkeypatch.setattr(project_mod, "publish_message", publish)

    result = await ReviewSubmitResponseTool().execute(
        project_id="00000000-0000-4000-a000-000000000001",
        session_id="00000000-0000-4000-a000-000000000002",
        decision="APPROVE",
        severity="INFO",
        summary="Budget is within the approved envelope.",
        findings=["Contingency is 15 percent."],
        financial_score=92,
        _aiat_context={
            "caller_id": "cfo",
            "caller_role": "c_suite",
            "caller_team": "office_cfo",
        },
    )

    assert result == {"entry_id": "router-entry"}
    envelope = publish.await_args.args[0]
    assert envelope["msg_type"] == "REVIEW_RESPONSE"
    assert envelope["recipient_team"] == "exec_coo"
    assert envelope["sender_id"] == "cfo"
    assert envelope["sender_team"] == "office_cfo"
    assert envelope["payload"]["verdict"] == "APPROVED"
    bodies = [comment["body"] for comment in envelope["payload"]["comments"]]
    assert "Budget is within the approved envelope." in bodies
    assert "Contingency is 15 percent." in bodies
    assert "92" in bodies


@pytest.mark.anyio
async def test_review_submit_veto_publishes_blocker_response(monkeypatch):
    import tool_service.tools.project as project_mod
    from tool_service.tools.project import ReviewSubmitVetoTool

    publish = AsyncMock(return_value={"entry_id": "veto-entry"})
    monkeypatch.setattr(project_mod, "publish_message", publish)

    await ReviewSubmitVetoTool().execute(
        project_id="00000000-0000-4000-a000-000000000003",
        session_id="00000000-0000-4000-a000-000000000004",
        reason="A credential is exposed in the artifact.",
        evidence=["artifact://security/findings/1"],
        resolution_path="Remove the credential and rotate it before resubmission.",
        _aiat_context={
            "caller_id": "cso",
            "caller_role": "c_suite",
            "caller_team": "office_cso",
        },
    )

    envelope = publish.await_args.args[0]
    assert envelope["sender_id"] == "cso"
    assert envelope["sender_team"] == "office_cso"
    assert envelope["payload"]["verdict"] == "REJECTED"
    assert envelope["payload"]["veto"] is True
    assert envelope["payload"]["comments"][0]["severity"] == "BLOCKER"
    assert envelope["payload"]["comments"][0]["veto"] is True
    assert "rotate it" in envelope["payload"]["comments"][0]["body"]


@pytest.mark.anyio
async def test_privileged_ops_request_uses_audited_control_plane_route(monkeypatch):
    import tool_service.tools.project as project_mod
    from tool_service.tools.project import PrivilegedOpsRequestTool

    post = AsyncMock(return_value={"decision": "pending_approval"})
    monkeypatch.setattr(project_mod, "orch_post", post)

    result = await PrivilegedOpsRequestTool().execute(
        action="container.start",
        justification="Recover the controlled local service after a health failure.",
        payload={"container": "orchestrator-api"},
        _aiat_context={"caller_id": "ceo", "caller_role": "orchestrator"},
    )

    assert result == {"decision": "pending_approval"}
    args = post.await_args
    assert args.args[0] == "/ceo/privileged-action"
    body = args.args[1]
    assert body["action"] == "container.start"
    assert body["actor_id"] == "ceo"
    assert body["payload"]["container"] == "orchestrator-api"
    assert body["payload"]["justification"].startswith("Recover")
    assert args.kwargs["context"]["caller_role"] == "orchestrator"
