"""Protect the durable relationship between a flow approval and its node execution."""

from __future__ import annotations

import ast
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0047_flow_approval_execution_binding.py"
)


def test_flow_approval_binding_migration_is_additive_and_follows_current_head() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"revision", "down_revision"}
    }

    assert assignments == {
        "revision": "0047_flow_approval_execution_binding",
        "down_revision": "0046_project_approval_inbox_index",
    }

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    add_column = next(
        call
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "op"
        and call.func.attr == "add_column"
    )
    assert ast.literal_eval(add_column.args[0]) == "approval_gates"
    column = add_column.args[1]
    assert isinstance(column, ast.Call)
    assert ast.literal_eval(column.args[0]) == "flow_node_execution_id"
    foreign_key = next(
        arg
        for arg in column.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "ForeignKey"
    )
    assert ast.literal_eval(foreign_key.args[0]) == "flow_node_executions.id"

    create_index = next(
        call
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "op"
        and call.func.attr == "create_index"
    )
    assert ast.literal_eval(create_index.args[0]) == "uq_approval_gates_flow_node_execution_id"
    assert ast.literal_eval(create_index.args[1]) == "approval_gates"
    assert ast.literal_eval(create_index.args[2]) == ["flow_node_execution_id"]
    assert ast.literal_eval(create_index.keywords[0].value) is True
