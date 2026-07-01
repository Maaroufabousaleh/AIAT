from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import yaml

from mas_core.policy.tool_access import can_use_tool_with_metadata
from mas_core.protocols.enums import AgentRole
from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.evaluator import DEFAULT_GUARDED_CHECKS, MANDATORY_GUARDED_CHECKS
from mas_tools_sdk.manifest import TOOL_MANIFEST

EXPECTED_DEFAULT_AGENTS = {
    "ceo": "exec_ceo",
    "coo": "exec_coo",
    "cfo": "office_cfo",
    "cio": "office_cio",
    "chrm": "office_chrm",
    "cso": "office_cso",
    "cto": "office_cto",
    "production_pm": "dept_production",
    "system_pm": "dept_system",
    "qa_lead": "dept_qa",
    "devops_pm": "dept_devops",
    "financial_analyst": "office_cfo",
    "tech_analyst": "office_cio",
    "hr_analyst": "office_chrm",
    "security_analyst": "office_cso",
    "sprint_planner": "office_cto",
    "kpi_analyst": "office_cto",
    "requirements_writer": "dept_production",
    "planner": "dept_production",
    "cost_estimator": "dept_production",
    "system_architect": "dept_system",
    "solution_designer": "dept_system",
    "tech_writer": "dept_system",
    "tester": "dept_qa",
    "devops_eng": "dept_devops",
    "sre_agent": "dept_devops",
    "coding_worker": "dept_qa",
    "test_evaluation_worker": "dept_qa",
    "security_evaluator": "office_cso",
    "sandbox_evaluator": "office_cso",
    "research_worker": "dept_production",
    "code_review_worker": "dept_qa",
    "hiring_agent": "office_chrm",
    "license_provenance_evaluator": "office_cso",
    "tool_interface_auditor": "office_cio",
    "adapter_certifier": "office_cio",
    "budget_evaluator": "office_cfo",
    "policy_grant_reviewer": "office_cso",
    "human_approval_gate": "exec_ceo",
}

GOVERNANCE_ROLES = {
    "ceo": AgentRole.ORCHESTRATOR,
    "coo": AgentRole.EXECUTIVE,
    "cfo": AgentRole.C_SUITE,
    "cio": AgentRole.C_SUITE,
    "chrm": AgentRole.C_SUITE,
    "cso": AgentRole.C_SUITE,
    "cto": AgentRole.C_SUITE,
    "production_pm": AgentRole.ADMIN,
    "system_pm": AgentRole.ADMIN,
    "qa_lead": AgentRole.ADMIN,
    "devops_pm": AgentRole.ADMIN,
    "human_approval_gate": AgentRole.ORCHESTRATOR,
}

TEAM_ROLE_BY_AGENT_ROLE = {
    "orchestrator": AgentRole.ORCHESTRATOR,
    "executive": AgentRole.EXECUTIVE,
    "c_suite": AgentRole.C_SUITE,
    "admin": AgentRole.ADMIN,
    "worker": AgentRole.WORKER,
}

BANNED_DEFAULT_COMPONENTS = {
    "trufflehog",
    "plane",
    "openproject",
    "grafana",
    "vault",
    "zitadel",
    "neo4j",
}

OPTIONAL_EXTERNAL_ONLY_COMPONENTS = {"ansible"}


def _workers_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "workers"


def _teams_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "teams"


def _load_manifests() -> dict[str, WorkerManifest]:
    manifests = {}
    for path in _workers_dir().glob("*.yaml"):
        manifest = WorkerManifest.model_validate(yaml.safe_load(path.read_text()))
        manifests[manifest.metadata.id] = manifest
    return manifests


def _team_tag(manifest: WorkerManifest) -> str | None:
    return next(
        (
            tag
            for tag in manifest.metadata.tags
            if tag.startswith(("exec_", "office_", "dept_"))
        ),
        None,
    )


def _agent_role(agent_id: str) -> AgentRole:
    return GOVERNANCE_ROLES.get(agent_id, AgentRole.WORKER)


def _component_names(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).startswith("excluded_"):
                continue
            yield str(key).lower()
            yield from _component_names(child)
    elif isinstance(value, list | tuple | set):
        for child in value:
            yield from _component_names(child)
    elif isinstance(value, str):
        yield value.lower()


def test_default_shipped_agent_manifests_are_complete_and_team_tagged():
    manifests = _load_manifests()

    assert set(manifests) == set(EXPECTED_DEFAULT_AGENTS)
    assert {
        agent_id: _team_tag(manifest)
        for agent_id, manifest in manifests.items()
    } == EXPECTED_DEFAULT_AGENTS


def test_default_security_evaluator_excludes_trufflehog():
    manifest = WorkerManifest.model_validate(
        yaml.safe_load((_workers_dir() / "security_evaluator.yaml").read_text())
    )

    config = manifest.runtime.adapter_config
    assert "semgrep" in config["default_tools"]
    assert "skillspector" in config["default_tools"]
    assert "trufflehog" in config["excluded_default_tools"]


def test_hiring_defaults_make_license_and_provenance_mandatory_without_trufflehog():
    assert set(MANDATORY_GUARDED_CHECKS) == {"licensing", "provenance"}
    assert set(MANDATORY_GUARDED_CHECKS) <= set(DEFAULT_GUARDED_CHECKS)
    assert "trufflehog" not in DEFAULT_GUARDED_CHECKS


@pytest.mark.anyio
async def test_custom_hiring_evaluation_cannot_bypass_mandatory_gates(tmp_path, monkeypatch):
    from mas_core.worker_registry import evaluator

    async def passed(*_args, **_kwargs):
        return {"passed": True, "score": 100.0, "details": "passed"}

    monkeypatch.setattr(evaluator, "_check_provenance", passed)
    monkeypatch.setattr(evaluator, "_check_licensing", passed)
    monkeypatch.setattr(evaluator, "_check_semgrep", passed)
    storage = AsyncMock()

    async def store_report(**kwargs):
        return {"id": uuid4(), **kwargs}

    storage.create_evaluation_report.side_effect = store_report

    report = await evaluator.evaluate_repository(
        worker_id=uuid4(),
        source_repo="https://github.com/example/worker",
        storage=storage,
        checks=["semgrep"],
        mirror_path=tmp_path,
        worker={"sandbox_profile": "gvisor"},
    )

    assert {"provenance", "licensing", "semgrep"} <= set(report["checks"])
    assert "trufflehog" not in report["checks"]


def test_default_manifest_metadata_runtime_and_tools_are_production_ready():
    for agent_id, manifest in _load_manifests().items():
        assert manifest.metadata.evaluation_status == "approved", agent_id
        assert manifest.metadata.description, agent_id
        assert "Auto-generated baseline manifest" not in manifest.metadata.description
        assert manifest.runtime.adapter_config.get("entrypoint") == manifest.integration.adapter_entrypoint
        assert manifest.runtime_tier == "builtin" or manifest.integration.isolation_mode != "native"
        assert manifest.capabilities, agent_id
        assert manifest.checkpointing.enabled is True, agent_id
        assert manifest.observability.metrics_enabled is True, agent_id
        assert manifest.observability.traces_enabled is True, agent_id

        role = _agent_role(agent_id)
        team = _team_tag(manifest)
        for capability in manifest.capabilities:
            assert capability.description, (agent_id, capability.name)
            assert capability.required_tools, (agent_id, capability.name)
            for tool_name in capability.required_tools:
                entry = TOOL_MANIFEST[tool_name]
                assert can_use_tool_with_metadata(
                    role=role,
                    tool_name=tool_name,
                    sender_team=team,
                    allowed_roles=entry.get("allowed_roles") or (),
                    blocked_roles=entry.get("blocked_roles") or (),
                ) is True, (agent_id, role.value, team, tool_name)


def test_default_manifests_do_not_ship_license_risky_components_by_default():
    for agent_id, manifest in _load_manifests().items():
        components = set(_component_names(manifest.runtime.adapter_config))

        assert components.isdisjoint(BANNED_DEFAULT_COMPONENTS), agent_id
        for optional in OPTIONAL_EXTERNAL_ONLY_COMPONENTS:
            if optional in components:
                assert optional in {
                    item.lower()
                    for item in manifest.runtime.adapter_config.get(
                        "optional_external_adapters", []
                    )
                }, agent_id


def test_default_team_rosters_match_manifest_inventory_and_policy():
    seen_agents = {}

    for path in _teams_dir().glob("*.yaml"):
        team = yaml.safe_load(path.read_text())
        team_id = team["team_id"]
        entries = [team["admin"], *team.get("workers", [])]

        for entry in entries:
            agent_id = entry["agent_id"]
            assert agent_id not in seen_agents, agent_id
            seen_agents[agent_id] = team_id
            assert EXPECTED_DEFAULT_AGENTS[agent_id] == team_id

            role = TEAM_ROLE_BY_AGENT_ROLE[entry["role"]]
            for tool_name in entry.get("tools", []):
                tool_entry = TOOL_MANIFEST[tool_name]
                assert can_use_tool_with_metadata(
                    role=role,
                    tool_name=tool_name,
                    sender_team=team_id,
                    allowed_roles=tool_entry.get("allowed_roles") or (),
                    blocked_roles=tool_entry.get("blocked_roles") or (),
                ) is True, (agent_id, role.value, team_id, tool_name)

    assert seen_agents == EXPECTED_DEFAULT_AGENTS
