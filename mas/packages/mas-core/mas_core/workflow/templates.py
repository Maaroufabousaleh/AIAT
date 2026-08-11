"""Reusable, validated flow definitions for the personal AIAT programme."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TEMPLATES: dict[str, dict[str, Any]] = {
    "software_delivery": {
        "name": "Software delivery",
        "description": "Requirements, approval, implementation, and QA evidence.",
        "definition_json": {
            "schema_version": "1.0",
            "metadata": {"template_id": "software_delivery", "evidence_policy": "software_delivery"},
            "nodes": [
                {"id": "start", "type": "start", "label": "Start", "config": {}},
                {"id": "requirements", "type": "task", "label": "Capture requirements", "config": {"team_id": "dept_production", "action": "requirements.capture"}},
                {"id": "approval", "type": "approval", "label": "Approve scope", "config": {"approver_role": "human_operator"}},
                {"id": "implementation", "type": "task", "label": "Implement change", "config": {"team_id": "dept_system", "action": "implementation.execute"}},
                {"id": "test", "type": "task", "label": "Run tests", "config": {"team_id": "dept_qa", "action": "test.execute"}},
                {"id": "end", "type": "end", "label": "Complete", "config": {}},
            ],
            "edges": [
                {"id": "start-requirements", "source": "start", "target": "requirements"},
                {"id": "requirements-approval", "source": "requirements", "target": "approval"},
                {"id": "approval-implementation", "source": "approval", "target": "implementation"},
                {"id": "implementation-test", "source": "implementation", "target": "test"},
                {"id": "test-end", "source": "test", "target": "end"},
            ],
        },
    },
    "research": {
        "name": "Research brief",
        "description": "Bounded research, review, and evidence-backed report.",
        "definition_json": {
            "schema_version": "1.0",
            "metadata": {"template_id": "research", "evidence_policy": "research"},
            "nodes": [
                {"id": "start", "type": "start", "label": "Start", "config": {}},
                {"id": "research", "type": "task", "label": "Research", "config": {"team_id": "office_cio", "action": "research.collect"}},
                {"id": "review", "type": "approval", "label": "Review findings", "config": {"approver_role": "cio"}},
                {"id": "report", "type": "task", "label": "Write report", "config": {"team_id": "dept_system", "action": "document.write"}},
                {"id": "end", "type": "end", "label": "Complete", "config": {}},
            ],
            "edges": [
                {"id": "start-research", "source": "start", "target": "research"},
                {"id": "research-review", "source": "research", "target": "review"},
                {"id": "review-report", "source": "review", "target": "report"},
                {"id": "report-end", "source": "report", "target": "end"},
            ],
        },
    },
    "hiring": {
        "name": "Worker hiring",
        "description": "Candidate intake, provenance/security review, and approval.",
        "definition_json": {
            "schema_version": "1.0",
            "metadata": {"template_id": "hiring", "evidence_policy": "manual"},
            "nodes": [
                {"id": "start", "type": "start", "label": "Start", "config": {}},
                {"id": "intake", "type": "task", "label": "Candidate intake", "config": {"team_id": "office_chrm", "action": "hiring.intake"}},
                {"id": "security", "type": "task", "label": "Security review", "config": {"team_id": "office_cso", "action": "security.evaluate"}},
                {"id": "approval", "type": "approval", "label": "Approve candidate", "config": {"approver_role": "ceo"}},
                {"id": "end", "type": "end", "label": "Complete", "config": {}},
            ],
            "edges": [
                {"id": "start-intake", "source": "start", "target": "intake"},
                {"id": "intake-security", "source": "intake", "target": "security"},
                {"id": "security-approval", "source": "security", "target": "approval"},
                {"id": "approval-end", "source": "approval", "target": "end"},
            ],
        },
    },
    "incident_response": {
        "name": "Incident response",
        "description": "Triage, parallel containment/diagnosis, approval, and closure.",
        "definition_json": {
            "schema_version": "1.0",
            "metadata": {"template_id": "incident_response", "evidence_policy": "operations"},
            "nodes": [
                {"id": "start", "type": "start", "label": "Start", "config": {}},
                {"id": "triage", "type": "task", "label": "Triage", "config": {"team_id": "dept_devops", "action": "incident.triage"}},
                {"id": "parallel", "type": "parallel", "label": "Contain and diagnose", "config": {"branches": ["contain", "diagnose"]}},
                {"id": "contain", "type": "task", "label": "Contain", "config": {"team_id": "dept_devops", "action": "incident.contain"}},
                {"id": "diagnose", "type": "task", "label": "Diagnose", "config": {"team_id": "office_cso", "action": "incident.diagnose"}},
                {"id": "join", "type": "join", "label": "Join evidence", "config": {}},
                {"id": "approval", "type": "approval", "label": "Approve closure", "config": {"approver_role": "human_operator"}},
                {"id": "end", "type": "end", "label": "Closed", "config": {}},
            ],
            "edges": [
                {"id": "start-triage", "source": "start", "target": "triage"},
                {"id": "triage-parallel", "source": "triage", "target": "parallel"},
                {"id": "parallel-contain", "source": "parallel", "target": "contain", "label": "contain"},
                {"id": "parallel-diagnose", "source": "parallel", "target": "diagnose", "label": "diagnose"},
                {"id": "contain-join", "source": "contain", "target": "join"},
                {"id": "diagnose-join", "source": "diagnose", "target": "join"},
                {"id": "join-approval", "source": "join", "target": "approval"},
                {"id": "approval-end", "source": "approval", "target": "end"},
            ],
        },
    },
    "integration_rollout": {
        "name": "Integration rollout",
        "description": "Plan, canary, human approval, and controlled rollout.",
        "definition_json": {
            "schema_version": "1.0",
            "metadata": {"template_id": "integration_rollout", "evidence_policy": "operations"},
            "nodes": [
                {"id": "start", "type": "start", "label": "Start", "config": {}},
                {"id": "plan", "type": "task", "label": "Plan integration", "config": {"team_id": "office_cio", "action": "integration.plan"}},
                {"id": "canary", "type": "task", "label": "Run canary", "config": {"team_id": "dept_devops", "action": "integration.canary"}},
                {"id": "approval", "type": "approval", "label": "Approve rollout", "config": {"approver_role": "human_operator"}},
                {"id": "rollout", "type": "task", "label": "Roll out", "config": {"team_id": "dept_devops", "action": "integration.rollout"}},
                {"id": "end", "type": "end", "label": "Complete", "config": {}},
            ],
            "edges": [
                {"id": "start-plan", "source": "start", "target": "plan"},
                {"id": "plan-canary", "source": "plan", "target": "canary"},
                {"id": "canary-approval", "source": "canary", "target": "approval"},
                {"id": "approval-rollout", "source": "approval", "target": "rollout"},
                {"id": "rollout-end", "source": "rollout", "target": "end"},
            ],
        },
    },
    "self_improvement": {
        "name": "Guarded self-improvement",
        "description": "Create a bounded project, pass independent gates, run shadow/canary, obtain human approval, and retain rollback evidence.",
        "definition_json": {
            "schema_version": "1.0",
            "metadata": {
                "template_id": "self_improvement",
                "evidence_policy": "software_delivery",
                "lifecycle_contract": "aiat.self-improvement.v1",
                "required_gates": ["coding", "testing", "review", "security", "migration", "rollback", "human_approval"],
                "promotion_mode": "shadow_canary_human_approval",
            },
            "nodes": [
                {"id": "start", "type": "start", "label": "Start", "config": {}},
                {"id": "proposal", "type": "task", "label": "Propose improvement", "config": {"team_id": "exec_cto", "action": "improvement.propose"}},
                {"id": "implementation", "type": "task", "label": "Implement", "config": {"team_id": "dept_system", "action": "improvement.implement"}},
                {"id": "test", "type": "task", "label": "Test", "config": {"team_id": "dept_qa", "action": "improvement.test"}},
                {"id": "review", "type": "task", "label": "Review change", "config": {"team_id": "dept_qa", "action": "improvement.review"}},
                {"id": "security", "type": "task", "label": "Security review", "config": {"team_id": "office_cso", "action": "security.evaluate"}},
                {"id": "migration", "type": "task", "label": "Prepare migration", "config": {"team_id": "dept_devops", "action": "improvement.migration"}},
                {"id": "rollback", "type": "task", "label": "Prepare rollback", "config": {"team_id": "dept_devops", "action": "improvement.rollback_plan"}},
                {"id": "shadow", "type": "task", "label": "Run shadow", "config": {"team_id": "dept_devops", "action": "improvement.shadow"}},
                {"id": "canary", "type": "task", "label": "Run canary", "config": {"team_id": "dept_devops", "action": "improvement.canary"}},
                {"id": "approval", "type": "approval", "label": "Approve promotion", "config": {"approver_role": "human_operator"}},
                {"id": "promotion", "type": "task", "label": "Promote", "config": {"team_id": "dept_devops", "action": "improvement.promote"}},
                {"id": "end", "type": "end", "label": "Complete", "config": {}},
            ],
            "edges": [
                {"id": "start-proposal", "source": "start", "target": "proposal"},
                {"id": "proposal-implementation", "source": "proposal", "target": "implementation"},
                {"id": "implementation-test", "source": "implementation", "target": "test"},
                {"id": "test-review", "source": "test", "target": "review"},
                {"id": "review-security", "source": "review", "target": "security"},
                {"id": "security-migration", "source": "security", "target": "migration"},
                {"id": "migration-rollback", "source": "migration", "target": "rollback"},
                {"id": "rollback-shadow", "source": "rollback", "target": "shadow"},
                {"id": "shadow-canary", "source": "shadow", "target": "canary"},
                {"id": "canary-approval", "source": "canary", "target": "approval"},
                {"id": "approval-promotion", "source": "approval", "target": "promotion"},
                {"id": "promotion-end", "source": "promotion", "target": "end"},
            ],
        },
    },
}


def flow_template_catalog() -> dict[str, Any]:
    """Return deterministic template metadata and definitions."""

    return {
        "schema_version": "aiat.flow-template.v1",
        "templates": [
            {
                "template_id": template_id,
                "name": entry["name"],
                "description": entry["description"],
                "definition_json": deepcopy(entry["definition_json"]),
            }
            for template_id, entry in sorted(_TEMPLATES.items())
        ],
    }


def flow_template(template_id: str) -> dict[str, Any] | None:
    entry = _TEMPLATES.get(str(template_id))
    if entry is None:
        return None
    return {
        "template_id": str(template_id),
        "name": entry["name"],
        "description": entry["description"],
        "definition_json": deepcopy(entry["definition_json"]),
    }
