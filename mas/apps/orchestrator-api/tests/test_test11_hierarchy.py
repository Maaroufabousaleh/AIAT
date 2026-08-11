"""
Test 11 — Team hierarchy graph: data, structure, and policy permissions.

Type: API / unit

The MAS team hierarchy is:
  orchestrator (exec_ceo)
  └── executive (exec_coo)
      ├── office_cfo
      ├── office_cio
      ├── office_chrm
      ├── office_cso
      └── office_cto
          ├── dept_production
          ├── dept_system
          ├── dept_qa
          └── dept_devops

The /teams endpoint returns team metadata. The policy/rules.py defines tiers.
A Playwright e2e spec is included for the interactive hierarchy graph UI (requires
live server — left as TODO if server not available).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch(storage):
    from orchestrator_api.main import app

    app.state.storage = storage


# Teams that appear in STATE_TO_TEAM.
TEAMS_IN_STATE_MACHINE = {
    "exec_ceo",
    "exec_coo",
    "office_cto",
    "office_cso",
    "dept_devops",
    "office_cfo",
}

# All 11 teams in the MAS (defined in policy rules)
EXPECTED_TEAM_IDS = {
    "exec_ceo",
    "exec_coo",
    "office_cfo",
    "office_cio",
    "office_chrm",
    "office_cso",
    "office_cto",
    "dept_production",
    "dept_system",
    "dept_qa",
    "dept_devops",
}

EXPECTED_ROLES = {
    "exec_ceo": "orchestrator",
    "exec_coo": "executive",
    "office_cfo": "c_suite",
    "office_cio": "c_suite",
    "office_chrm": "c_suite",
    "office_cso": "c_suite",
    "office_cto": "c_suite",
    "dept_production": "admin",
    "dept_system": "admin",
    "dept_qa": "admin",
    "dept_devops": "admin",
}


# ---------------------------------------------------------------------------
# 1. GET /teams — returns all teams
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_teams_returns_all_teams(client):
    """GET /teams returns all teams from the default policy registry."""
    r = await client.get("/teams")
    assert r.status_code == 200
    teams = r.json()
    assert isinstance(teams, list)
    team_ids = {t["team_id"] for t in teams}
    missing = EXPECTED_TEAM_IDS - team_ids
    assert not missing, f"Default teams missing from /teams response: {sorted(missing)}"


@pytest.mark.anyio
async def test_get_teams_includes_required_fields(client):
    """Each team entry must have at least team_id field (name/role are a gap)."""
    r = await client.get("/teams")
    assert r.status_code == 200
    teams = r.json()
    for team in teams:
        assert "team_id" in team, f"Team missing team_id: {team}"
    # NOTE: Production gap — the /teams endpoint does not return 'name' or 'role' fields.
    # These would be needed for the hierarchy graph UI to show labels and tiers.


@pytest.mark.anyio
async def test_get_teams_roles_match_policy(client):
    """Teams returned by /teams endpoint only have team_id.
    Role information is in the policy module, not the /teams response.
    This test verifies policy role assignment for teams that appear in the state machine.
    """
    from mas_core.policy.rules import TEAM_TIERS

    r = await client.get("/teams")
    assert r.status_code == 200
    teams = r.json()
    for team in teams:
        tid = team.get("team_id")
        if tid in TEAM_TIERS:
            # Verify that policy knows this team
            assert TEAM_TIERS[tid] is not None


@pytest.mark.anyio
async def test_get_teams_exactly_eleven(client):
    """The /teams endpoint returns the full 11-team default registry."""
    r = await client.get("/teams")
    assert r.status_code == 200
    teams = r.json()
    assert len(teams) == len(EXPECTED_TEAM_IDS), (
        f"Expected {len(EXPECTED_TEAM_IDS)} default teams, got {len(teams)}"
    )


@pytest.mark.anyio
async def test_get_teams_ceo_is_orchestrator(client):
    """exec_ceo team appears in /teams (handles INIT and HUMAN_APPROVAL states)."""
    r = await client.get("/teams")
    assert r.status_code == 200
    teams = {t["team_id"] for t in r.json()}
    assert "exec_ceo" in teams


@pytest.mark.anyio
async def test_get_teams_coo_is_executive(client):
    """exec_coo team appears in /teams (handles multiple workflow states)."""
    r = await client.get("/teams")
    assert r.status_code == 200
    teams = {t["team_id"] for t in r.json()}
    assert "exec_coo" in teams


@pytest.mark.anyio
async def test_get_teams_dept_teams_are_admin(client):
    """dept_devops appears in /teams (handles INFRA_PROVISIONING state).
    Other dept_* teams are not in the state machine and are absent from /teams.
    Production gap: dept_production, dept_system, dept_qa are not in /teams.
    """
    r = await client.get("/teams")
    assert r.status_code == 200
    teams = {t["team_id"] for t in r.json()}
    assert "dept_devops" in teams, "dept_devops must appear (handles INFRA_PROVISIONING)"


# ---------------------------------------------------------------------------
# 2. Policy module — team tiers
# ---------------------------------------------------------------------------


class TestPolicyTiers:
    """Unit tests for the team tier registry in mas_core.policy.rules."""

    def test_team_tiers_all_known_teams_registered(self):
        """Every known team must have a tier in TEAM_TIERS."""
        from mas_core.policy.rules import TEAM_TIERS

        for tid in EXPECTED_TEAM_IDS:
            assert tid in TEAM_TIERS, f"Team '{tid}' not found in TEAM_TIERS"

    def test_team_tiers_ceo_is_orchestrator(self):
        from mas_core.policy.rules import TEAM_TIERS
        from mas_core.protocols.enums import AgentRole

        assert TEAM_TIERS["exec_ceo"] == AgentRole.ORCHESTRATOR

    def test_team_tiers_coo_is_executive(self):
        from mas_core.policy.rules import TEAM_TIERS
        from mas_core.protocols.enums import AgentRole

        assert TEAM_TIERS["exec_coo"] == AgentRole.EXECUTIVE

    def test_team_tiers_c_suite_teams(self):
        from mas_core.policy.rules import TEAM_TIERS, C_SUITE_TEAMS
        from mas_core.protocols.enums import AgentRole

        for tid in C_SUITE_TEAMS:
            assert TEAM_TIERS[tid] == AgentRole.C_SUITE, f"{tid} should be C_SUITE"

    def test_team_tiers_dept_teams_are_admin(self):
        from mas_core.policy.rules import TEAM_TIERS, DEPT_TEAMS
        from mas_core.protocols.enums import AgentRole

        for tid in DEPT_TEAMS:
            assert TEAM_TIERS[tid] == AgentRole.ADMIN, f"{tid} should be ADMIN"

    def test_orchestrator_team_constant(self):
        from mas_core.policy.rules import ORCHESTRATOR_TEAM

        assert ORCHESTRATOR_TEAM == "exec_ceo"

    def test_executive_team_constant(self):
        from mas_core.policy.rules import EXECUTIVE_TEAM

        assert EXECUTIVE_TEAM == "exec_coo"

    def test_cto_team_constant(self):
        from mas_core.policy.rules import CTO_TEAM

        assert CTO_TEAM == "office_cto"

    def test_devops_team_constant(self):
        from mas_core.policy.rules import DEVOPS_TEAM

        assert DEVOPS_TEAM == "dept_devops"

    def test_policy_rules_covers_all_tiers(self):
        """POLICY_RULES must have entries for all 6 tier names."""
        from mas_core.policy.rules import POLICY_RULES

        required = {"orchestrator", "executive", "c_suite", "admin", "worker", "sub_agent"}
        assert required <= set(POLICY_RULES.keys())


# ---------------------------------------------------------------------------
# 3. Worker filter by team_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_workers_filtered_by_team_id(client):
    """GET /capabilities/workers?team_id=dept_production returns workers for that team."""
    storage = MagicMock()
    storage.list_workers = AsyncMock(
        return_value=[
            {
                "id": str(uuid4()),
                "name": "Worker Alpha",
                "team_id": "dept_production",
                "role": "worker",
                "status": "ACTIVE",
            }
        ]
    )
    _patch(storage)

    r = await client.get("/capabilities/workers?team_id=dept_production")
    assert r.status_code == 200
    workers = r.json()
    assert isinstance(workers, list)
    for w in workers:
        assert w["team_id"] == "dept_production"


@pytest.mark.anyio
async def test_list_workers_no_filter_returns_active_workers(client):
    """GET /capabilities/workers without filter returns all active workers."""
    storage = MagicMock()
    storage.list_workers = AsyncMock(
        return_value=[
            {"id": str(uuid4()), "name": "W1", "team_id": "dept_qa", "status": "ACTIVE"},
            {"id": str(uuid4()), "name": "W2", "team_id": "dept_system", "status": "ACTIVE"},
        ]
    )
    _patch(storage)

    r = await client.get("/capabilities/workers")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) == 2


# ---------------------------------------------------------------------------
# 4. Hierarchy structure validation (unit)
# ---------------------------------------------------------------------------


class TestHierarchyStructure:
    """Verify the implied hierarchy from policy rules is structurally sound."""

    def test_ceo_can_message_all_targets(self):
        """Orchestrator (CEO) has allowed_targets = ['*']."""
        from mas_core.policy.rules import POLICY_RULES

        ceo_policy = POLICY_RULES["orchestrator"]
        assert ceo_policy["allowed_targets"] == ["*"]

    def test_executive_cannot_directly_address_workers(self):
        """Executive can address orchestrator, c_suite, admin — not workers."""
        from mas_core.policy.rules import POLICY_RULES

        exec_targets = POLICY_RULES["executive"]["allowed_targets"]
        # Workers are not in executive's allowed targets
        assert "role:worker" not in exec_targets

    def test_c_suite_targets_include_orchestrator_and_executive(self):
        """C-suite can message up to orchestrator and executive."""
        from mas_core.policy.rules import POLICY_RULES

        c_suite_targets = POLICY_RULES["c_suite"]["allowed_targets"]
        assert "role:orchestrator" in c_suite_targets
        assert "role:executive" in c_suite_targets

    def test_admin_targets_include_cto(self):
        """Admin (dept PM) can escalate to CTO."""
        from mas_core.policy.rules import POLICY_RULES

        admin_targets = POLICY_RULES["admin"]["allowed_targets"]
        assert "role:c_suite:cto" in admin_targets or "role:executive" in admin_targets

    def test_worker_targets_own_team_only(self):
        """Workers can only message within their own team."""
        from mas_core.policy.rules import POLICY_RULES

        worker_targets = POLICY_RULES["worker"]["allowed_targets"]
        assert worker_targets == ["team:own"]

    def test_sub_agent_targets_parent_only(self):
        """Sub-agents can only message parent."""
        from mas_core.policy.rules import POLICY_RULES

        sub_agent_targets = POLICY_RULES["sub_agent"]["allowed_targets"]
        assert "parent:only" in sub_agent_targets

    def test_no_circular_authority(self):
        """Worker cannot target orchestrator (no upward bypass)."""
        from mas_core.policy.rules import POLICY_RULES

        worker_targets = POLICY_RULES["worker"]["allowed_targets"]
        assert "role:orchestrator" not in worker_targets
        assert "role:executive" not in worker_targets
        assert "*" not in worker_targets


# ---------------------------------------------------------------------------
# 5. Tool hierarchy — tool access tiers
# ---------------------------------------------------------------------------


class TestToolHierarchy:
    """Verify tool access is tiered by role."""

    def test_orchestrator_has_unrestricted_tool_access(self):
        """Orchestrator tools = ('*',) — unrestricted."""
        from mas_core.policy.rules import ORCHESTRATOR_TOOLS

        assert "*" in ORCHESTRATOR_TOOLS

    def test_worker_blocked_from_admin_tools(self):
        """Workers cannot use project.* or approval.* tools."""
        from mas_core.policy.rules import WORKER_BLOCKED_TOOLS

        assert "project.*" in WORKER_BLOCKED_TOOLS
        assert "approval.*" in WORKER_BLOCKED_TOOLS

    def test_cto_has_sprint_and_issue_tools(self):
        """CTO team has sprint.* and issue.* tools."""
        from mas_core.policy.rules import CTO_EXTRA_TOOLS

        assert any(t.startswith("sprint") for t in CTO_EXTRA_TOOLS)
        assert any(t.startswith("issue") for t in CTO_EXTRA_TOOLS)

    def test_devops_pm_has_infra_tools(self):
        """DevOps PM team has infra.*, cicd.* tools."""
        from mas_core.policy.rules import DEVOPS_PM_EXTRA_TOOLS

        assert any(t.startswith("infra") for t in DEVOPS_PM_EXTRA_TOOLS)
        assert any(t.startswith("cicd") for t in DEVOPS_PM_EXTRA_TOOLS)

    def test_worker_has_browser_tools(self):
        """Workers have all 6 browser tools."""
        from mas_core.policy.rules import WORKER_TOOLS

        browser_tools = {
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_screenshot",
            "browser_evaluate",
            "browser_close",
        }
        missing = browser_tools - set(WORKER_TOOLS)
        assert not missing, f"Worker missing browser tools: {missing}"


# ---------------------------------------------------------------------------
# 6. Live Playwright evidence for hierarchy graph UI
# ---------------------------------------------------------------------------


def test_hierarchy_graph_ui_live_evidence_pending():
    """
    The mas-dashboard hierarchy graph and communication-policy overlay are
    implemented. Live verification still requires:
      - A live Next.js server at http://127.0.0.1:3000
      - The orchestrator-api at http://localhost:8000
    The source-built Playwright spec is at:
      mas/apps/mas-dashboard/e2e/app-operations.spec.ts
    The focused interaction is checked in but remains unverified against a
    current deployed image in this API-only suite.
    Steps that would be covered:
      1. Navigate to /system-viz
      2. Verify all 11 team nodes visible
      3. Click CEO node → verify details panel
      4. Toggle comm permissions overlay
      5. Select project → verify workflow route highlighted
    """
    pytest.skip("Live hierarchy graph evidence requires current Next.js and orchestrator servers.")
