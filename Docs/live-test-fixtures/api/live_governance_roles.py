"""Live governance/role/authority checks for G-001..G-015.

The fixture intentionally exercises the running API and tool-service rather than
calling policy helpers in-process.  A result with ``partial`` entries is useful
evidence: it records the exact role contract that still needs a code change.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx


TOOL_SECRET = os.environ["TOOL_SECRET"]
HEADERS = {"Authorization": f"Bearer {TOOL_SECRET}"}


async def run_tool(
    client: httpx.AsyncClient,
    name: str,
    *,
    caller_id: str,
    role: str,
    team: str | None,
    project_id: str | None = None,
    kwargs: dict | None = None,
) -> dict:
    response = await client.post(
        f"/tools/{name}/run",
        headers=HEADERS,
        json={
            "agent_id": caller_id,
            "sender_role": role,
            "sender_team": team,
            "project_id": project_id,
            "kwargs": kwargs or {},
        },
    )
    response.raise_for_status()
    return response.json()


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://orchestrator-api:8000", timeout=30) as api, httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=30
    ) as tools:
        company = (await api.get("/system/company"))
        company.raise_for_status()
        company_body = company.json()
        graph = await api.get("/system/org-graph")
        graph.raise_for_status()
        graph_body = graph.json()
        ceo_workers = await api.get("/capabilities/workers?team_id=exec_ceo")
        ceo_workers.raise_for_status()
        ceo = next((w for w in ceo_workers.json() if w.get("name") == "ceo"), None)
        assert company_body["company"]["seeded"] is True
        assert company_body["ceo"] == {
            "id": "ceo_agent",
            "name": "AIAT CEO",
            "role": "orchestrator",
            "team": "exec_ceo",
            "permanent": True,
        }
        assert ceo and ceo["status"] == "ACTIVE" and ceo["evaluation_status"] == "approved"
        assert any(n.get("type") == "ceo" and n.get("label") == "AIAT CEO" for n in graph_body["nodes"])

        normal = await api.post(
            "/ceo/privileged-action",
            json={"action": "project.status", "actor_id": "ceo", "actor_role": "ceo"},
        )
        normal.raise_for_status()
        normal_body = normal.json()
        assert normal_body["allowed"] is True and normal_body["level"] == "executive"

        request = await api.post(
            "/ceo/privileged-action",
            json={
                "action": "system.restart",
                "actor_id": "ceo",
                "actor_role": "ceo",
                "payload": {"reason": "governance live test"},
            },
        )
        request.raise_for_status()
        pending = request.json()
        assert pending["allowed"] is False and pending["decision"] == "pending_approval"
        record_id = pending["record_id"]
        pending_rows = await api.get("/ceo/privileged-actions/pending")
        pending_rows.raise_for_status()
        assert any(str(row["id"]) == record_id for row in pending_rows.json())
        approval = await api.post(
            f"/ceo/privileged-action/{record_id}/approve",
            json={"approved": True, "decided_by": "live-human", "reason": "approved in fixture"},
        )
        approval.raise_for_status()
        assert approval.json()["decision"] == "approved"
        audit = await api.get("/ceo/privileged-actions/audit?limit=200")
        audit.raise_for_status()
        audit_row = next(row for row in audit.json() if str(row["id"]) == record_id)
        assert audit_row["decision"] == "approved" and audit_row["decided_by"] == "live-human"

        worker_id = f"live-governance-{uuid.uuid4()}"
        grant = await tools.post(
            f"/tools/workers/{worker_id}/grants", headers=HEADERS, json={"tool_name": "time_now"}
        )
        grant.raise_for_status()
        allowed = await run_tool(
            tools,
            "time_now",
            caller_id=worker_id,
            role="worker",
            team="dept_system",
        )
        assert allowed["success"] is True
        grant_denial = await run_tool(
            tools,
            "web_search",
            caller_id=worker_id,
            role="worker",
            team="dept_system",
            kwargs={"query": "this must not run"},
        )
        assert grant_denial["success"] is False and grant_denial["error_code"] == "FORBIDDEN"
        revoke = await tools.delete(f"/tools/workers/{worker_id}/grants/time_now", headers=HEADERS)
        revoke.raise_for_status()
        revoked = await run_tool(
            tools,
            "time_now",
            caller_id=worker_id,
            role="worker",
            team="dept_system",
        )
        assert revoked["success"] is False and revoked["error_code"] == "FORBIDDEN"

        executive_human = await run_tool(
            tools,
            "human.await_decision",
            caller_id="coo",
            role="executive",
            team="exec_coo",
            project_id="operator-direct",
        )
        # The manifest itself is orchestrator-only; this negative call verifies
        # the enforcement path, not merely prompt text.
        assert executive_human["success"] is False and executive_human["error_code"] == "FORBIDDEN"

        manifest_response = await tools.get("/tools")
        manifest_response.raise_for_status()
        manifest = {entry["tool_name"]: entry for entry in manifest_response.json()["tools"]}
        expected_roles = {
            "department_task": {"orchestrator", "executive"},
            "document.create_draft": {"orchestrator", "executive", "admin"},
            "review.start_session": {"orchestrator", "executive"},
            "review.aggregate": {"orchestrator", "executive"},
            "capability.search": {"orchestrator", "executive", "c_suite", "admin"},
            "capability.list_workers": {"orchestrator", "executive", "c_suite", "admin"},
            "review.submit": {"orchestrator", "executive", "c_suite"},
            "sprint.create": {"orchestrator", "executive", "c_suite"},
        }
        role_contracts = {
            name: sorted(set(manifest[name]["allowed_roles"]) - set(manifest[name]["blocked_roles"]))
            for name in expected_roles
        }
        assert all(set(role_contracts[name]) == roles for name, roles in expected_roles.items())

        cfo_kpi = await run_tool(
            tools,
            "kpi.compute",
            caller_id="cfo",
            role="c_suite",
            team="office_cfo",
            project_id="00000000-0000-4000-8000-000000000000",
            kwargs={"project_id": "00000000-0000-4000-8000-000000000000"},
        )
        chrm_history = await run_tool(
            tools,
            "kpi.query_history",
            caller_id="chrm",
            role="c_suite",
            team="office_chrm",
            project_id="00000000-0000-4000-8000-000000000000",
            kwargs={"project_id": "00000000-0000-4000-8000-000000000000"},
        )

    static_prompts = {}
    prompt_dir = Path("/app/prompts")
    if prompt_dir.exists():
        for name in ("cfo", "cio", "chrm", "cso", "cto", "coo", "ceo"):
            text = (prompt_dir / f"{name}.md").read_text()
            static_prompts[name] = {
                "central_gateway": "centralized LLM" in text or "centralized gateway" in text,
                "credential_boundary": "credentials" in text.lower(),
            }

    print(
        json.dumps(
            {
                "status": "PASS",
                "ceo_worker_id": ceo["id"],
                "org_graph_nodes": len(graph_body["nodes"]),
                "privileged_record_id": record_id,
                "grant_worker_id": worker_id,
                "role_contracts": role_contracts,
                "cfo_kpi": cfo_kpi,
                "chrm_history": chrm_history,
                "static_prompts": static_prompts,
            },
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
