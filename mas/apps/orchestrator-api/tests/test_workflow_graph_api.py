from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_workflow_graph_matches_engine_tables(client):
    """GET /workflow/graph must mirror the engine's own state tables."""
    from mas_core.workflow.states import ProjectState
    from mas_core.workflow.transitions import TRANSITIONS, WILDCARD_TRANSITIONS

    response = await client.get("/workflow/graph")

    assert response.status_code == 200
    body = response.json()

    assert [s["id"] for s in body["states"]] == [s.value for s in ProjectState]
    expected = []
    for state in ProjectState:
        expected.extend(
            {"from": state.value, "to": str(target), "event": event.value}
            for event, target in TRANSITIONS.get(state, {}).items()
        )
        expected.extend(
            {"from": state.value, "to": str(target), "event": event.value}
            for event, target in WILDCARD_TRANSITIONS.items()
        )
    assert body["transitions"] == expected


@pytest.mark.anyio
async def test_workflow_graph_spot_checks_lifecycle_path(client):
    response = await client.get("/workflow/graph")

    assert response.status_code == 200
    body = response.json()
    by_from: dict[str, list[dict[str, str]]] = {}
    for transition in body["transitions"]:
        by_from.setdefault(transition["from"], []).append(transition)

    assert {
        "to": "FEASIBILITY_CHECK",
        "event": "project_created",
    } in [{"to": t["to"], "event": t["event"]} for t in by_from["INIT"]]
    assert by_from["ARCHIVED"] == [
        {"from": "ARCHIVED", "to": "FAILED", "event": event}
        for event in ("watchdog_timeout", "review_circuit_open", "unrecoverable_error")
    ]
