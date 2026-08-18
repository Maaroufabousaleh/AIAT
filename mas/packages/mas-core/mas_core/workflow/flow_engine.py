"""Flow engine — validation, traversal, and idempotent execution for orchestration flows.

This module provides:
- Flow validation (single start, reachable end, no dead nodes, required config)
- Flow traversal (next node resolution for all node types)
- Idempotent advance/complete methods for runtime execution
- Execution result types
- Retry, timeout, escalation, and branching condition support
- Parallel branch tracking and join synchronization

Node types (v1):
- start      — entry point, has no incoming edges
- end        — terminal node, has no outgoing edges
- task       — executes an action via a team/agent, produces output
- approval   — requires human or role-based approval to proceed
- condition  — evaluates an expression, branches to true/false path
- parallel   — spawns multiple branches, waits for all to complete
- join       — waits for all incoming branches to arrive before proceeding
- switch     — selects a branch based on context value
- escalate   — delegates to a higher authority (agent/team) on failure
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class FlowNodeType(StrEnum):
    START = "start"
    END = "end"
    TASK = "task"
    APPROVAL = "approval"
    CONDITION = "condition"
    PARALLEL = "parallel"
    JOIN = "join"
    SWITCH = "switch"
    ESCALATE = "escalate"


class FlowInstanceStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FlowExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    RETRYING = "RETRYING"


VALID_NODE_TYPES = set(FlowNodeType)
VALID_STATUSES = set(FlowInstanceStatus)
VALID_EXECUTION_STATUSES = set(FlowExecutionStatus)


@dataclass(frozen=True)
class FlowNode:
    id: str
    type: FlowNodeType
    label: str
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def team_id(self) -> str | None:
        return self.config.get("team_id")

    @property
    def action(self) -> str | None:
        return self.config.get("action")

    @property
    def approver_role(self) -> str | None:
        return self.config.get("approver_role")

    @property
    def approver_user(self) -> str | None:
        return self.config.get("approver_user")

    @property
    def expression(self) -> str | None:
        return self.config.get("expression")

    @property
    def branches(self) -> list[str]:
        return self.config.get("branches", [])

    @property
    def timeout_seconds(self) -> int | None:
        val = self.config.get("timeout_seconds")
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    @property
    def retries(self) -> int:
        val = self.config.get("retries")
        try:
            return int(val) if val is not None else 0
        except (ValueError, TypeError):
            return 0

    @property
    def escalate_to_team(self) -> str | None:
        return self.config.get("escalate_to_team")

    @property
    def escalate_to_agent(self) -> str | None:
        return self.config.get("escalate_to_agent")

    @property
    def switch_key(self) -> str | None:
        return self.config.get("switch_key")

    @property
    def switch_cases(self) -> dict[str, str]:
        return self.config.get("switch_cases", {})


@dataclass(frozen=True)
class FlowEdge:
    id: str
    source: str
    target: str
    condition: str | None = None
    label: str | None = None

    @property
    def is_true_branch(self) -> bool:
        return self.condition in (None, "true")

    @property
    def is_false_branch(self) -> bool:
        return self.condition == "false"


@dataclass(frozen=True)
class FlowDefinition:
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    metadata: dict[str, Any] = field(default_factory=dict)
    # Version of the canonical node configuration catalogue used to author
    # this definition.  Definitions written before the catalogue existed are
    # read as v1 for a backwards-compatible migration window.
    schema_version: str = "1.0"

    def get_node(self, node_id: str) -> FlowNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def get_outgoing_edges(self, node_id: str) -> list[FlowEdge]:
        return [e for e in self.edges if e.source == node_id]

    def get_incoming_edges(self, node_id: str) -> list[FlowEdge]:
        return [e for e in self.edges if e.target == node_id]

    def get_start_nodes(self) -> list[FlowNode]:
        return [n for n in self.nodes if n.type == FlowNodeType.START]

    def get_end_nodes(self) -> list[FlowNode]:
        return [n for n in self.nodes if n.type == FlowNodeType.END]

    def get_nodes_by_type(self, node_type: FlowNodeType) -> list[FlowNode]:
        return [n for n in self.nodes if n.type == node_type]

    def get_parallel_branches(self, parallel_node_id: str) -> list[str]:
        node = self.get_node(parallel_node_id)
        if node is None or node.type != FlowNodeType.PARALLEL:
            return []
        return node.branches

    def validate_reachability(self) -> list[str]:
        errors = []
        start_nodes = self.get_start_nodes()
        if not start_nodes:
            return ["Flow has no start node"]
        start_id = start_nodes[0].id
        reachable = _compute_reachable(self, start_id)
        for node in self.nodes:
            if node.id not in reachable:
                errors.append(f"Node '{node.id}' is unreachable from start")
        return errors


class FlowValidationError(ValueError):
    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def validate_flow(definition: FlowDefinition) -> list[str]:
    """Validate a flow definition and return a list of error messages.

    Validation rules (v1):
    - Exactly one start node
    - At least one end node
    - All nodes must be reachable from start
    - All end nodes must be reachable from start
    - No disconnected nodes
    - Required config per node type:
      - task: requires "action" or "team_id"
      - approval: requires "approver_role" or "approver_user"
      - condition: requires "expression"
      - parallel: requires "branches" (array of node IDs)
      - join: no extra required config
    """
    from .node_schema import validate_node_schema_version

    errors: list[str] = validate_node_schema_version(definition.schema_version)

    if not definition.nodes:
        errors.append("Flow must have at least one node")
        return errors

    start_nodes = definition.get_start_nodes()
    if len(start_nodes) != 1:
        errors.append(f"Flow must have exactly one start node (found {len(start_nodes)})")

    end_nodes = definition.get_end_nodes()
    if not end_nodes:
        errors.append("Flow must have at least one end node")

    if errors:
        return errors

    node_ids = {n.id for n in definition.nodes}
    edge_source_ids = {e.source for e in definition.edges}
    edge_target_ids = {e.target for e in definition.edges}

    orphaned = node_ids - edge_source_ids - edge_target_ids
    orphaned = orphaned - {start_nodes[0].id}
    for nid in orphaned:
        node = definition.get_node(nid)
        if node and node.type != FlowNodeType.START:
            errors.append(f"Node '{nid}' is disconnected from the flow")

    reachable = _compute_reachable(definition, start_nodes[0].id)
    unreachable = node_ids - reachable
    if unreachable:
        errors.append(f"Nodes are unreachable from start: {unreachable}")

    for node in definition.nodes:
        node_errors = _validate_node_config(node)
        errors.extend(node_errors)

    # Typed node fields are necessary but not sufficient for control-flow
    # safety.  Keep the graph contract explicit so runtime traversal cannot
    # silently ignore a declared parallel branch or route a switch case to a
    # node that has no edge from the switch.
    errors.extend(_validate_control_flow_topology(definition))

    return errors


def _validate_control_flow_topology(definition: FlowDefinition) -> list[str]:
    """Validate graph relationships owned by parallel, join, and switch nodes.

    ``FlowNode.config`` declares the intended branch/case targets while edges
    describe the persisted graph.  Requiring the two views to agree prevents a
    definition from appearing valid in the editor but taking a different path
    at runtime.  This helper is deterministic and has no storage or provider
    side effects.
    """

    node_ids = {node.id for node in definition.nodes}
    errors: list[str] = []

    for node in definition.nodes:
        outgoing_targets = [edge.target for edge in definition.get_outgoing_edges(node.id)]
        outgoing_set = set(outgoing_targets)

        if node.type == FlowNodeType.PARALLEL:
            branches = [str(branch).strip() for branch in node.branches]
            if len(branches) != len(set(branches)):
                errors.append(f"Node '{node.id}' (parallel): branches must be unique")
            unknown = sorted(set(branches) - node_ids)
            if unknown:
                errors.append(
                    f"Node '{node.id}' (parallel): branches reference unknown nodes: {unknown}"
                )
            missing_edges = sorted(set(branches) - outgoing_set)
            if missing_edges:
                errors.append(
                    f"Node '{node.id}' (parallel): missing outgoing edges for branches: {missing_edges}"
                )
            undeclared_edges = sorted(outgoing_set - set(branches))
            if undeclared_edges:
                errors.append(
                    f"Node '{node.id}' (parallel): outgoing edges are not declared branches: {undeclared_edges}"
                )

        elif node.type == FlowNodeType.JOIN:
            incoming = definition.get_incoming_edges(node.id)
            if len(incoming) < 2:
                errors.append(
                    f"Node '{node.id}' (join): requires at least two incoming branch edges"
                )

        elif node.type == FlowNodeType.SWITCH:
            cases = {str(key): str(value) for key, value in node.switch_cases.items()}
            unknown = sorted(set(cases.values()) - node_ids)
            if unknown:
                errors.append(
                    f"Node '{node.id}' (switch): cases reference unknown nodes: {unknown}"
                )
            missing_edges = sorted(set(cases.values()) - outgoing_set)
            if missing_edges:
                errors.append(
                    f"Node '{node.id}' (switch): missing outgoing edges for cases: {missing_edges}"
                )

    return errors


def _compute_reachable(definition: FlowDefinition, start_id: str) -> set[str]:
    """Compute all nodes reachable from a given start node."""
    visited: set[str] = set()
    queue = deque([start_id])

    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)

        for edge in definition.get_outgoing_edges(node_id):
            if edge.target not in visited:
                queue.append(edge.target)

    return visited


def _validate_node_config(node: FlowNode) -> list[str]:
    """Validate required config for a node type."""
    from .node_schema import validate_node_config_schema

    # The schema catalogue owns common types/required fields.  The task policy
    # model below owns the deeper worker/model/checkpoint semantics.
    errors: list[str] = [
        f"Node '{node.id}' ({node.type.value}): {error}"
        for error in validate_node_config_schema(node.type, node.config)
    ]
    config = node.config

    if node.type == FlowNodeType.TASK:
        # Modern typed worker policy validation is additive to the legacy graph
        # rules. It rejects raw model IDs and invalid retry/cancellation policy
        # while keeping old action/team flows readable during migration.
        from .worker_policy import validate_task_policy

        errors.extend(f"Node '{node.id}' (task): {error}" for error in validate_task_policy(config))

    return errors


def parse_flow_definition(data: dict[str, Any]) -> FlowDefinition:
    """Parse a raw dict into a FlowDefinition.

    Expected format:
    {
        "nodes": [
            {"id": "start_1", "type": "start", "label": "Start", "config": {}},
            {"id": "task_1", "type": "task", "label": "Do Work", "config": {"action": "..."}},
            ...
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "task_1", "condition": null},
            ...
        ]
    }
    """
    nodes = []
    for n in data.get("nodes", []):
        try:
            node_type = FlowNodeType(n.get("type", ""))
        except ValueError:
            raise FlowValidationError(f"Invalid node type: {n.get('type')}") from None
        nodes.append(
            FlowNode(
                id=str(n["id"]),
                type=node_type,
                label=str(n.get("label", n["id"])),
                config=dict(n.get("config", {})),
            )
        )

    edges = []
    for e in data.get("edges", []):
        edges.append(
            FlowEdge(
                id=str(e["id"]),
                source=str(e["source"]),
                target=str(e["target"]),
                condition=e.get("condition"),
                label=e.get("label"),
            )
        )

    metadata = data.get("metadata", {})
    schema_version = str(
        data.get("schema_version")
        or metadata.get("node_schema_version")
        or "1.0"
    )
    return FlowDefinition(nodes=nodes, edges=edges, metadata=metadata, schema_version=schema_version)


@dataclass
class FlowTraversalResult:
    """Result of traversing the flow to find next executable nodes."""

    node_ids: list[str]
    is_blocked: bool = False
    block_reason: str | None = None


def get_next_nodes(
    definition: FlowDefinition,
    completed_node_ids: set[str],
    active_parallel_ids: set[str],
    context: dict[str, Any] | None = None,
) -> FlowTraversalResult:
    """Determine the next nodes that should execute.

    Parameters
    ----------
    definition : FlowDefinition
        The flow graph.
    completed_node_ids : set[str]
        Nodes that have finished executing.
    active_parallel_ids : set[str]
        Parallel branch roots that are currently executing.

    Returns
    -------
    FlowTraversalResult
        - node_ids: list of nodes to execute next
        - is_blocked: true if flow cannot proceed (e.g., join waiting for inputs)
        - block_reason: explanation if blocked
    """
    if not completed_node_ids and not active_parallel_ids:
        start_nodes = definition.get_start_nodes()
        if not start_nodes:
            return FlowTraversalResult(node_ids=[], is_blocked=True, block_reason="No start node")
        return FlowTraversalResult(node_ids=[start_nodes[0].id])

    next_ids: list[str] = []

    for node_id in completed_node_ids:
        source_node = definition.get_node(node_id)
        outgoing = definition.get_outgoing_edges(node_id)

        # ── CONDITION node completed: evaluate expression and follow matching branch ──
        if source_node is not None and source_node.type == FlowNodeType.CONDITION:
            expr = source_node.config.get("expression", "")
            result = _evaluate_condition(expr, completed_node_ids, context)
            for edge in outgoing:
                target = edge.target
                if target in completed_node_ids:
                    continue
                # edge.condition or edge.label determine the branch ("pass"/"true" vs "fail"/"false")
                edge_cond = (
                    edge.condition or edge.label
                )  # label is used as condition when condition is absent
                if (result and edge_cond in (None, "true", "pass")) or (
                    not result and edge_cond in ("false", "fail")
                ):
                    next_ids.append(target)
            continue  # condition node handled; skip generic outgoing logic

        # A completed switch owns its case selection.  Do not also walk every
        # outgoing edge through the generic path, or one traversal would
        # schedule both the selected and unselected branches.
        if source_node is not None and source_node.type == FlowNodeType.SWITCH:
            switch_key = source_node.config.get("switch_key", "")
            switch_cases = source_node.config.get("switch_cases", {})
            context_value = (context or {}).get(switch_key)
            matched_target = (
                switch_cases.get(str(context_value)) if context_value is not None else None
            )
            if matched_target and matched_target not in completed_node_ids:
                next_ids.append(matched_target)
            continue

        for edge in outgoing:
            target = edge.target

            target_node = definition.get_node(target)
            if target_node is None:
                continue

            if target_node.type == FlowNodeType.CONDITION:
                # Activate the condition node itself; branching happens when it completes
                if target not in completed_node_ids:
                    next_ids.append(target)

            elif target_node.type == FlowNodeType.JOIN:
                incoming = definition.get_incoming_edges(target)
                all_completed = all(e.source in completed_node_ids for e in incoming)
                if target not in completed_node_ids and all_completed:
                    next_ids.append(target)

            elif target_node.type == FlowNodeType.PARALLEL:
                # Activate the parallel node itself; fan-out to branches happens
                # when the parallel node is completed (via its outgoing edges).
                if target not in completed_node_ids:
                    next_ids.append(target)

            elif target_node.type == FlowNodeType.SWITCH:
                # A switch is a real control node, not an implicit edge.
                # Activate it first so the runtime can persist an execution
                # record and let the completed switch select exactly one case
                # through the source-node branch above.  Routing directly from
                # the predecessor skipped the switch execution entirely and
                # blocked when its context value was not set until completion.
                if target not in completed_node_ids:
                    next_ids.append(target)

            else:
                if target not in completed_node_ids:
                    next_ids.append(target)

    # Multiple completed branch nodes can point at one join, and a traversal
    # receives the full completed set on every call.  Preserve graph order but
    # never schedule the same node twice or re-activate a completed join.
    next_ids = list(dict.fromkeys(next_ids))
    if not next_ids:
        end_nodes = definition.get_end_nodes()
        if all(n.id in completed_node_ids for n in end_nodes):
            return FlowTraversalResult(node_ids=[], is_blocked=False)
        return FlowTraversalResult(
            node_ids=[], is_blocked=True, block_reason="No more nodes to execute"
        )

    return FlowTraversalResult(node_ids=next_ids)


def _evaluate_condition(
    expression: str, completed_node_ids: set[str], context: dict[str, Any] | None = None
) -> bool:
    expr = expression.strip()

    if expr.lower() == "always true":
        return True
    if expr.lower() == "always false":
        return False

    if " AND " in expr:
        parts = expr.split(" AND ", 1)
        return _evaluate_condition(parts[0], completed_node_ids, context) and _evaluate_condition(
            parts[1], completed_node_ids, context
        )

    if " OR " in expr:
        parts = expr.split(" OR ", 1)
        return _evaluate_condition(parts[0], completed_node_ids, context) or _evaluate_condition(
            parts[1], completed_node_ids, context
        )

    if expr.lower().endswith(" completed"):
        node_id = expr.lower()[:-10].strip()
        return node_id in completed_node_ids

    if expr.startswith("!context."):
        key = expr[len("!context.") :]
        if context is None:
            return True
        return not bool(context.get(key))

    if expr.startswith("context."):
        rest = expr[len("context.") :]
        for op_sym in ("==", "!=", ">=", "<=", ">", "<"):
            if op_sym in rest:
                parts = rest.split(op_sym, 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val_str = parts[1].strip()
                    if context is None:
                        return False
                    actual = context.get(key)
                    if actual is None:
                        return False
                    if op_sym in ("==", "!="):
                        expected = val_str.strip("\"'")
                        # Handle boolean literals
                        if expected.lower() == "true":
                            coerced = True
                        elif expected.lower() == "false":
                            coerced = False
                        else:
                            coerced = None
                        if coerced is not None and isinstance(actual, bool):
                            return (actual == coerced) if op_sym == "==" else (actual != coerced)
                        if isinstance(actual, (int, float)):
                            try:
                                return _cmp(actual, float(expected), op_sym)
                            except ValueError:
                                return (
                                    (str(actual) == expected)
                                    if op_sym == "=="
                                    else (str(actual) != expected)
                                )
                        return (
                            (str(actual).lower() == expected.lower())
                            if op_sym == "=="
                            else (str(actual).lower() != expected.lower())
                        )
                    if not isinstance(actual, (int, float)):
                        try:
                            actual = float(actual)
                        except (ValueError, TypeError):
                            return False
                    try:
                        expected_num = float(val_str)
                    except ValueError:
                        return False
                    return _cmp(actual, expected_num, op_sym)

        if context is None:
            return False
        return bool(context.get(rest.strip()))

    logger.warning("Unknown condition expression: %s", expression)
    return False


def _cmp(a: float, b: float, op: str) -> bool:
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    return False


@dataclass
class FlowAdvanceResult:
    """Result of advancing a flow instance."""

    success: bool
    new_status: FlowInstanceStatus
    active_node_ids: list[str]
    output: dict[str, Any] | None = None
    error: str | None = None


def serialize_flow_definition(definition: FlowDefinition) -> dict[str, Any]:
    """Serialize a FlowDefinition to a JSON-serializable dict."""
    return {
        "schema_version": definition.schema_version,
        "nodes": [
            {
                "id": n.id,
                "type": n.type.value,
                "label": n.label,
                "config": n.config,
            }
            for n in definition.nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source,
                "target": e.target,
                "condition": e.condition,
                "label": e.label,
            }
            for e in definition.edges
        ],
        "metadata": definition.metadata,
    }
