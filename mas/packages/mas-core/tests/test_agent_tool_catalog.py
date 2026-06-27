from mas_core.agent_runtime import tool_catalog
from mas_core.agent_runtime.config import AgentConfig
from mas_core.agent_runtime.tool_catalog import tool_definitions_for_agent
from mas_core.agent_runtime.worker import WorkerAgent
from mas_core.protocols.enums import AgentRole


def test_orchestrator_tool_catalog_is_manifest_driven_and_not_yaml_limited():
    tools = tool_definitions_for_agent(
        role=AgentRole.ORCHESTRATOR,
        team_id="exec_ceo",
        configured_tools=["project.create"],
    )
    names = {tool.function.name for tool in tools}

    assert "project.create" in names
    assert "capability.register" in names
    assert "time_now" in names


def test_tool_catalog_does_not_advertise_team_policy_denials():
    tools = tool_definitions_for_agent(
        role=AgentRole.C_SUITE,
        team_id="office_cfo",
    )
    names = {tool.function.name for tool in tools}

    assert "sprint.create" not in names
    assert "kpi.compute" not in names


def test_tool_catalog_advertises_manifest_allowed_tools_not_in_static_policy():
    tools = tool_definitions_for_agent(
        role=AgentRole.WORKER,
        team_id="dept_frontend",
    )
    names = {tool.function.name for tool in tools}

    assert "web_search" in names
    assert "sprint.create" not in names


def test_tool_catalog_filters_to_runtime_registered_tools():
    tools = tool_definitions_for_agent(
        role=AgentRole.WORKER,
        team_id="dept_frontend",
        runtime_tools=[
            {
                "tool_name": "web_search",
                "description": "Search the web.",
                "allowed_roles": ["worker"],
                "blocked_roles": [],
            }
        ],
    )
    names = {tool.function.name for tool in tools}

    assert names == {"web_search"}


def test_tool_catalog_static_fallback_filters_missing_optional_dependencies(monkeypatch):
    def fake_find_spec(module_name):
        if module_name == "playwright":
            return None
        return object()

    monkeypatch.setattr(tool_catalog, "find_spec", fake_find_spec)

    tools = tool_definitions_for_agent(
        role=AgentRole.WORKER,
        team_id="dept_frontend",
    )
    names = {tool.function.name for tool in tools}

    assert "browser_navigate" not in names


def test_worker_prompt_gets_runtime_tool_catalog():
    agent = WorkerAgent(
        AgentConfig(
            agent_id="worker_1",
            team_id="office_cto",
            agent_role=AgentRole.WORKER,
            agent_secret="secret",
            tool_definitions=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    )

    assert "Runtime Tool Catalog" in agent._system_prompt
    assert "`web_search`" in agent._system_prompt
