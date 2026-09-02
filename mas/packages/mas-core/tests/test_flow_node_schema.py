from __future__ import annotations

from uuid import UUID

from mas_core.workflow import (
    NODE_SCHEMA_VERSION,
    FlowNodeType,
    audit_legacy_task_aliases,
    migrate_legacy_task_aliases,
    node_schema_catalog,
    parse_flow_definition,
    validate_flow,
    validate_node_config_schema,
)


def test_node_catalog_is_versioned_and_covers_every_runtime_node_type() -> None:
    catalog = node_schema_catalog()

    assert catalog["schema_version"] == NODE_SCHEMA_VERSION
    assert set(catalog["node_types"]) == {node_type.value for node_type in FlowNodeType}
    assert catalog["additional_properties"] is True


def test_typed_schema_rejects_wrong_types_but_keeps_extension_fields_open() -> None:
    errors = validate_node_config_schema(
        FlowNodeType.PARALLEL,
        {"branches": "branch_a", "adapter_extension": {"enabled": True}},
    )

    assert "'branches' must be array" in errors
    assert not any("adapter_extension" in error for error in errors)


def test_task_legacy_assignment_fields_are_marked_as_compatibility_metadata() -> None:
    task_fields = {
        field["name"]: field
        for field in node_schema_catalog()["node_types"]["task"]["fields"]
    }

    assert task_fields["team_id"]["deprecated"] is True
    assert task_fields["action"]["deprecated"] is True
    assert task_fields["worker_id"].get("deprecated") is not True


def test_flow_definition_defaults_and_persists_node_schema_version() -> None:
    definition = parse_flow_definition(
        {
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [{"id": "e", "source": "start", "target": "end"}],
        }
    )

    assert definition.schema_version == NODE_SCHEMA_VERSION
    assert validate_flow(definition) == []


def test_parallel_join_topology_requires_declared_branch_edges() -> None:
    valid = parse_flow_definition(
        {
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {"id": "fanout", "type": "parallel", "config": {"branches": ["a", "b"]}},
                {"id": "a", "type": "task", "config": {"action": "a"}},
                {"id": "b", "type": "task", "config": {"action": "b"}},
                {"id": "join", "type": "join", "config": {}},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "fanout"},
                {"id": "e2", "source": "fanout", "target": "a"},
                {"id": "e3", "source": "fanout", "target": "b"},
                {"id": "e4", "source": "a", "target": "join"},
                {"id": "e5", "source": "b", "target": "join"},
                {"id": "e6", "source": "join", "target": "end"},
            ],
        }
    )

    assert validate_flow(valid) == []

    invalid = parse_flow_definition(
        {
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {"id": "fanout", "type": "parallel", "config": {"branches": ["a", "missing"]}},
                {"id": "a", "type": "task", "config": {"action": "a"}},
                {"id": "join", "type": "join", "config": {}},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "fanout"},
                {"id": "e2", "source": "fanout", "target": "a"},
                {"id": "e3", "source": "a", "target": "join"},
                {"id": "e4", "source": "join", "target": "end"},
            ],
        }
    )

    errors = validate_flow(invalid)
    assert any("unknown nodes" in error for error in errors)
    assert any("missing outgoing edges" in error for error in errors)
    assert any("at least two incoming" in error for error in errors)


def test_switch_topology_requires_case_targets_to_be_edges() -> None:
    definition = parse_flow_definition(
        {
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "switch",
                    "type": "switch",
                    "config": {"switch_key": "result", "switch_cases": {"ok": "done", "no": "missing"}},
                },
                {"id": "done", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "switch"},
                {"id": "e2", "source": "switch", "target": "done"},
            ],
        }
    )

    errors = validate_flow(definition)
    assert any("switch" in error and "unknown nodes" in error for error in errors)
    assert any("switch" in error and "missing outgoing edges" in error for error in errors)


def test_legacy_task_alias_audit_is_deterministic_and_never_guesses_worker_ids() -> None:
    definition = parse_flow_definition(
        {
            "nodes": [
                {"id": "z-task", "type": "task", "config": {"action": "legacy.run"}},
                {
                    "id": "a-task",
                    "type": "task",
                    "config": {"worker_id": "worker-1", "action": "legacy.run"},
                },
            ]
        }
    )

    findings = audit_legacy_task_aliases(definition)

    assert [item["node_id"] for item in findings] == ["a-task", "z-task"]
    assert findings[0]["disposition"] == "normalization_candidate"
    assert findings[1]["disposition"] == "manual_worker_binding_required"
    assert all("worker_id" not in item for item in findings)


def test_legacy_task_migration_is_explicit_immutable_and_model_policy_safe() -> None:
    worker_id = UUID("00000000-0000-4000-a000-000000000701")
    source = {
        "metadata": {"owner": "operator"},
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "legacy",
                "type": "task",
                "config": {"team_id": "dept_qa", "action": "test.run"},
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "a", "source": "start", "target": "legacy"},
            {"id": "b", "source": "legacy", "target": "end"},
        ],
    }

    result = migrate_legacy_task_aliases(source, worker_bindings={"legacy": worker_id})

    assert result["errors"] == []
    assert result["changed"] is True
    assert result["migrated_node_ids"] == ["legacy"]
    assert result["findings_after"] == []
    assert result["definition_json"]["nodes"][1]["config"] == {
        "worker_id": str(worker_id),
        "task_type": "test.run",
        "model_mode": "none",
    }
    assert source["nodes"][1]["config"] == {"team_id": "dept_qa", "action": "test.run"}


def test_legacy_task_migration_requires_bindings_and_rejects_unknown_nodes() -> None:
    source = {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "legacy", "type": "task", "config": {"action": "test.run"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "a", "source": "start", "target": "legacy"},
            {"id": "b", "source": "legacy", "target": "end"},
        ],
    }

    result = migrate_legacy_task_aliases(
        source,
        worker_bindings={"not-a-node": "00000000-0000-4000-a000-000000000701"},
    )

    assert result["missing_worker_bindings"] == ["legacy"]
    assert result["unknown_worker_bindings"] == ["not-a-node"]
    assert {error["code"] for error in result["errors"]} >= {
        "WORKER_BINDING_REQUIRED",
        "UNKNOWN_WORKER_BINDING_NODE",
    }
