"""Protect the bounded approval inbox's supporting schema index."""

from __future__ import annotations

import ast
from contextlib import suppress
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0046_project_approval_inbox_index.py"
)


def _literal_assignments(tree: ast.Module) -> dict[str, object]:
    result: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    with suppress(ValueError, TypeError):
                        result[target.id] = ast.literal_eval(node.value)
    return result


def test_approval_inbox_migration_follows_current_head_and_adds_keyset_index() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    assignments = _literal_assignments(tree)

    assert assignments["revision"] == "0046_project_approval_inbox_index"
    assert assignments["down_revision"] == "0045_worker_tool_effects"

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    create_index = next(
        call
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "op"
        and call.func.attr == "create_index"
    )
    assert ast.literal_eval(create_index.args[0]) == "ix_approval_gates_status_created_id"
    assert ast.literal_eval(create_index.args[1]) == "approval_gates"
    assert ast.literal_eval(create_index.args[2]) == ["status", "created_at", "id"]
