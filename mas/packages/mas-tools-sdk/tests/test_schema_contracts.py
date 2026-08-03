from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup


class _Input(BaseModel):
    query: str = Field(min_length=1)


class _Output(BaseModel):
    ok: bool


class _TypedTool(BaseTool):
    name = "test.typed"
    group = ToolGroup.KPI_UTILITY
    description = "typed contract test tool"
    allowed_roles = [AgentRole.WORKER]
    input_model = _Input
    output_model = _Output

    async def execute(self, **kwargs):
        return {"ok": bool(kwargs["query"])}


def test_typed_manifest_contains_generated_json_schemas():
    entry = _TypedTool().to_manifest_entry()

    assert entry["schema_status"] == "declared"
    assert entry["input_schema"]["properties"]["query"]["minLength"] == 1
    assert entry["output_schema"]["properties"]["ok"]["type"] == "boolean"


def test_typed_input_preserves_non_user_aiat_context():
    normalized = _TypedTool().validate_input(
        {
            "query": "hello",
            "project_id": "project-1",
            "_aiat_context": {"caller_id": "worker-1"},
        }
    )

    assert normalized["query"] == "hello"
    assert normalized["project_id"] == "project-1"
    assert normalized["_aiat_context"]["caller_id"] == "worker-1"


def test_typed_input_and_output_reject_invalid_values():
    with pytest.raises(ValidationError):
        _TypedTool().validate_input({"query": ""})
    with pytest.raises(ValidationError):
        _TypedTool().validate_output({"ok": "not-a-boolean"})
