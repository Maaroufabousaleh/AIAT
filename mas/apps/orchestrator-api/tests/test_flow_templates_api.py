from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_flow_template_catalog_is_public_and_sorted(client):
    response = await client.get("/flow-templates")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.flow-template.v1"
    assert [item["template_id"] for item in body["templates"]] == sorted(
        item["template_id"] for item in body["templates"]
    )
    assert len(body["templates"]) == 6


@pytest.mark.anyio
async def test_flow_from_template_reuses_validated_create_path(client):
    from orchestrator_api.main import app

    flow_id = uuid4()
    created = {"id": flow_id, "name": "Delivery", "version": 1}
    storage = MagicMock()
    storage.create_flow = AsyncMock(return_value=created)
    app.state.storage = storage

    try:
        response = await client.post(
            "/flows/from-template",
            json={"template_id": "software_delivery", "name": "Delivery"},
        )
    finally:
        app.state.storage = None

    assert response.status_code == 201
    assert response.json()["status"] == "created_from_template"
    assert response.json()["template_id"] == "software_delivery"
    payload = storage.create_flow.await_args.kwargs
    assert payload["definition_json"]["schema_version"] == "1.0"
    assert payload["definition_json"]["metadata"]["template_id"] == "software_delivery"


@pytest.mark.anyio
async def test_unknown_flow_template_is_rejected(client):
    response = await client.post("/flows/from-template", json={"template_id": "missing"})

    assert response.status_code == 404
