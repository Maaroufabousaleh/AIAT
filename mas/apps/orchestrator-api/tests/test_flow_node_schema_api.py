from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_flow_node_schema_catalog_is_public_and_versioned(client):
    response = await client.get("/flows/node-schemas")

    assert response.status_code == 200
    body = response.json()
    assert body["catalog_id"] == "aiat.flow-node-schemas"
    assert body["schema_version"] == "1.0"
    assert set(body["node_types"]) == {
        "start",
        "end",
        "task",
        "approval",
        "condition",
        "parallel",
        "join",
        "switch",
        "escalate",
    }
    assert body["node_types"]["task"]["required_any"] == ["worker_id", "team_id", "action"]
