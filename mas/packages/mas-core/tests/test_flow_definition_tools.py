from mas_core.workflow.definition_tools import flow_definition_diff, flow_definition_hash


def test_flow_definition_hash_and_diff_are_deterministic() -> None:
    before = {
        "schema_version": "1.0",
        "nodes": [{"id": "start", "type": "start"}],
        "edges": [],
        "metadata": {"owner": "operator"},
    }
    after = {
        "metadata": {"owner": "operator", "purpose": "review"},
        "edges": [],
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "end", "type": "end"},
        ],
        "schema_version": "1.0",
    }

    assert flow_definition_hash(before) == flow_definition_hash(
        {"metadata": before["metadata"], "edges": [], "nodes": before["nodes"], "schema_version": "1.0"}
    )
    diff = flow_definition_diff(before, after)
    assert diff["nodes"]["added"] == [{"id": "end", "type": "end"}]
    assert diff["metadata_changed"] is True
