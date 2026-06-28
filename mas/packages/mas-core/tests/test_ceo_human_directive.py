from __future__ import annotations

from typing import Any

import pytest

from mas_core.agent_runtime.csuite import CSuiteAgent
from mas_core.llm_gateway.models import ToolDefinition, ToolFunction
from mas_core.protocols.envelope import MessageEnvelope, MessageType


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        function=ToolFunction(
            name=name,
            description=f"{name} test tool",
            parameters={"type": "object", "properties": {}},
        )
    )


class FakeCeo:
    def __init__(self) -> None:
        self.agent_id = "ceo"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_tool(self, tool_name: str, tool_kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, tool_kwargs))
        if tool_name == "project.create":
            return {
                "id": "11111111-1111-4111-8111-111111111111",
                "name": tool_kwargs["title"],
                "description": tool_kwargs["description"],
                "state": "INIT",
            }
        if tool_name == "project.status":
            return {
                "id": tool_kwargs["project_id"],
                "name": "Status Probe",
                "state": "FEASIBILITY_CHECK",
            }
        if tool_name == "project.list":
            return [
                {
                    "id": "33333333-3333-4333-8333-333333333333",
                    "name": "Alpha",
                    "state": "FEASIBILITY_REPORT",
                },
                {
                    "id": "44444444-4444-4444-8444-444444444444",
                    "name": "Beta",
                    "state": "COMPLETED",
                },
            ]
        if tool_name == "human.await_decision":
            return {
                "pending": True,
                "pending_count": 1,
                "gate_id": "gate-1",
                "gate_type": "human",
                "decisions": [
                    {
                        "id": "gate-1",
                        "gate_type": "human",
                        "title": "Approve launch?",
                    }
                ],
            }
        if tool_name == "capability.list_workers":
            return {
                "count": 2,
                "workers": [
                    {
                        "worker_id": "opencode_candidate",
                        "name": "OpenCode Candidate",
                        "status": "INACTIVE",
                        "evaluation_status": "pending",
                        "capabilities": ["code.generate"],
                    },
                    {
                        "worker_id": "tester",
                        "name": "Tester",
                        "status": "ACTIVE",
                        "capabilities": ["test.run"],
                    },
                ],
            }
        if tool_name == "capability.search":
            return {
                "query": {"name": tool_kwargs["name"]},
                "count": 1,
                "workers": [
                    {
                        "worker_id": "tester",
                        "name": "Tester",
                        "status": "ACTIVE",
                    }
                ],
            }
        return {"ok": True, "tool": tool_name, "args": tool_kwargs}


class FakeCeoSystemEvent:
    _specialization = "CEO"
    _storage = None

    def __init__(self) -> None:
        self.reactions: list[tuple[MessageEnvelope, str, str, str]] = []

    def _log_extra(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    async def _ceo_react_to_state(
        self,
        envelope: MessageEnvelope,
        event: str,
        to_state: str,
        project_id: str,
    ) -> None:
        self.reactions.append((envelope, event, to_state, project_id))


@pytest.mark.anyio
async def test_human_directive_normalizes_human_notify_markup_without_executing_tool():
    fake = FakeCeo()

    text = await CSuiteAgent._normalize_human_directive_response(
        fake,
        '<human.notify>{"message":"I can coordinate the departments.","project_id":"operator-direct"}</human.notify>',
        project_id="operator-direct",
        tools=[_tool("human.notify"), _tool("project.create")],
    )

    assert text == "I can coordinate the departments."
    assert fake.calls == []


@pytest.mark.anyio
async def test_human_directive_normalizes_human_notify_markup_without_registered_tool():
    fake = FakeCeo()

    text = await CSuiteAgent._normalize_human_directive_response(
        fake,
        '<human.notify>{"message":"Direct answer from the model."}</human.notify>',
        project_id="operator-direct",
        tools=[_tool("project.status")],
    )

    assert text == "Direct answer from the model."
    assert fake.calls == []


@pytest.mark.anyio
async def test_human_directive_executes_inline_action_tool_markup():
    fake = FakeCeo()

    text = await CSuiteAgent._normalize_human_directive_response(
        fake,
        '<project.status>{"project_id":"00000000-0000-4000-a000-000000000001"}</project.status>',
        project_id="00000000-0000-4000-a000-000000000001",
        tools=[_tool("human.notify"), _tool("project.status")],
    )

    assert fake.calls == [
        ("project.status", {"project_id": "00000000-0000-4000-a000-000000000001"})
    ]
    assert '"state": "FEASIBILITY_CHECK"' in text
    assert "project.status result:" in text


@pytest.mark.anyio
async def test_human_directive_strips_provider_thought_markup():
    fake = FakeCeo()

    text = await CSuiteAgent._normalize_human_directive_response(
        fake,
        "<thought>private reasoning</thought>Visible answer.</thought>",
        project_id="operator-direct",
        tools=[_tool("human.notify")],
    )

    assert text == "Visible answer."
    assert fake.calls == []


@pytest.mark.anyio
async def test_human_directive_command_creates_project_without_llm():
    fake = FakeCeo()

    text = await CSuiteAgent._handle_human_directive_command(
        fake,
        "Create a new project named ceo-command-probe with description 'Command path probe'.",
    )

    assert fake.calls == [
        (
            "project.create",
            {
                "title": "ceo-command-probe",
                "description": "Command path probe",
                "human_requester": "human_operator",
            },
        )
    ]
    assert text == (
        "Created project ceo-command-probe "
        "(ID 11111111-1111-4111-8111-111111111111, state INIT)."
    )


@pytest.mark.anyio
async def test_human_directive_command_gets_project_status_without_llm():
    fake = FakeCeo()

    text = await CSuiteAgent._handle_human_directive_command(
        fake,
        "What is the status of project 22222222-2222-4222-8222-222222222222?",
    )

    assert fake.calls == [
        ("project.status", {"project_id": "22222222-2222-4222-8222-222222222222"})
    ]
    assert text == "Project Status Probe is in state FEASIBILITY_CHECK."


@pytest.mark.anyio
async def test_human_directive_command_lists_projects_without_llm():
    fake = FakeCeo()

    text = await CSuiteAgent._handle_human_directive_command(
        fake,
        "List recent projects.",
    )

    assert fake.calls == [("project.list", {"limit": 10})]
    assert text == "Projects: Alpha [FEASIBILITY_REPORT]; Beta [COMPLETED]."


@pytest.mark.anyio
async def test_human_directive_command_lists_hiring_board_without_llm():
    fake = FakeCeo()

    text = await CSuiteAgent._handle_human_directive_command(
        fake,
        "Show hiring board status.",
    )

    assert fake.calls == [("capability.list_workers", {})]
    assert "Workers (2):" in text
    assert "OpenCode Candidate [INACTIVE" in text


@pytest.mark.anyio
async def test_human_directive_command_searches_worker_capability_without_llm():
    fake = FakeCeo()

    text = await CSuiteAgent._handle_human_directive_command(
        fake,
        "Search capability test.run.",
    )

    assert fake.calls == [("capability.search", {"name": "test.run"})]
    assert text == "Workers matching test.run: Tester."


@pytest.mark.anyio
async def test_human_directive_command_checks_pending_decisions_without_llm():
    fake = FakeCeo()

    text = await CSuiteAgent._handle_human_directive_command(
        fake,
        "Show pending decisions for project 22222222-2222-4222-8222-222222222222.",
    )

    assert fake.calls == [
        ("human.await_decision", {"project_id": "22222222-2222-4222-8222-222222222222"})
    ]
    assert text == "Pending decisions: Approve launch?."


@pytest.mark.anyio
async def test_human_directive_command_submits_cso_override_without_llm():
    fake = FakeCeo()

    text = await CSuiteAgent._handle_human_directive_command(
        fake,
        (
            "Override the CSO veto for project "
            "22222222-2222-4222-8222-222222222222 because 'Risk accepted by operator'."
        ),
    )

    assert fake.calls == [
        (
            "approval.override_cso",
            {
                "project_id": "22222222-2222-4222-8222-222222222222",
                "action": "approve",
                "reason": "Risk accepted by operator",
                "actor_id": "ceo",
            },
        )
    ]
    assert "CSO override submitted:" in text


def test_build_workflow_tool_definitions_exposes_full_ceo_tool_surface():
    tools = CSuiteAgent._build_workflow_tool_definitions(object())
    names = {tool.function.name for tool in tools}

    assert {
        "project.status",
        "project.list",
        "project.transition",
        "project.create",
        "review.aggregate",
        "approval.override_cso",
        "human.notify",
        "human.await_decision",
        "flow.list",
        "flow.recommend",
        "flow.assign",
        "flow.invoke",
        "flow.status",
        "flow.advance",
        "capability.list_workers",
        "capability.search",
    }.issubset(names)


@pytest.mark.anyio
@pytest.mark.parametrize("to_state", ["FEASIBILITY_REPORT", "ARCHIVED"])
async def test_ceo_system_event_workflow_notifications_do_not_call_llm_reaction(to_state: str):
    fake = FakeCeoSystemEvent()
    envelope = MessageEnvelope(
        msg_type=MessageType.SYSTEM_EVENT,
        sender_id="orchestrator",
        sender_role="orchestrator",
        sender_team="orchestrator",
        recipient_team="exec_ceo",
        project_id="33333333-3333-4333-8333-333333333333",
        payload={
            "event": "all_reviews_in",
            "to_state": to_state,
        },
    )

    await CSuiteAgent._handle_system_event(fake, envelope)

    assert fake.reactions == []
