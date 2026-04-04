"""Flow engine — validation, traversal, and idempotent execution for orchestration flows.

This module provides:
- Flow validation (single start, reachable end, no dead nodes, required config)
- Flow traversal (next node resolution for all node types)
- Idempotent advance/complete methods for runtime execution
- Execution result types

Node types (v1):
- start      — entry point, has no incoming edges
- end        — terminal node, has no outgoing edges
- task       — executes an action, produces output
- approval   — requires human approval to proceed
- condition  — evaluates a simple expression, branches to true/false path
- parallel   — spawns multiple branches, waits for all to complete
- join       — waits for all incoming branches to arrive before proceeding
"""

from __future__ import annotations

import logging
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


VALID_NODE_TYPES = set(FlowNodeType)
VALID_STATUSES = set(FlowInstanceStatus)
VALID_EXECUTION_STATUSES = set(FlowExecutionStatus)


@dataclass(frozen=True)
class FlowNode:
    id: str
    type: FlowNodeType
    label: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FlowEdge:
    id: str
    source: str
    target: str
    condition: str | None = None


@dataclass(frozen=True)
class FlowDefinition:
    nodes: list[FlowNode]
    edges: list[FlowEdge]

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
    errors: list[str] = []

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

    return errors


def _compute_reachable(definition: FlowDefinition, start_id: str) -> set[str]:
    """Compute all nodes reachable from a given start node."""
    visited: set[str] = set()
    queue = [start_id]

    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)

        for edge in definition.get_outgoing_edges(node_id):
            if edge.target not in visited:
                queue.append(edge.target)

    return visited


def _validate_node_config(node: FlowNode) -> list[str]:
    """Validate required config for a node type."""
    errors: list[str] = []
    config = node.config

    if node.type == FlowNodeType.TASK:
        if not config.get("action") and not config.get("team_id"):
            errors.append(f"Node '{node.id}' (task): requires 'action' or 'team_id'")

    elif node.type == FlowNodeType.APPROVAL:
        if not config.get("approver_role") and not config.get("approver_user"):
            errors.append(
                f"Node '{node.id}' (approval): requires 'approver_role' or 'approver_user'"
            )

    elif node.type == FlowNodeType.CONDITION:
        if not config.get("expression"):
            errors.append(f"Node '{node.id}' (condition): requires 'expression'")

    elif node.type == FlowNodeType.PARALLEL:
        if not config.get("branches") or not isinstance(config.get("branches"), list):
            errors.append(f"Node '{node.id}' (parallel): requires 'branches' as array of node IDs")

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
            raise FlowValidationError(f"Invalid node type: {n.get('type')}")
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
            )
        )

    return FlowDefinition(nodes=nodes, edges=edges)


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
        outgoing = definition.get_outgoing_edges(node_id)
        for edge in outgoing:
            target = edge.target

            target_node = definition.get_node(target)
            if target_node is None:
                continue

            if target_node.type == FlowNodeType.CONDITION:
                expr = target_node.config.get("expression", "")
                result = _evaluate_condition(expr, completed_node_ids)
                if result and edge.condition in (None, "true"):
                    next_ids.append(target)
                elif not result and edge.condition == "false":
                    next_ids.append(target)

            elif target_node.type == FlowNodeType.JOIN:
                incoming = definition.get_incoming_edges(target)
                all_completed = all(e.source in completed_node_ids for e in incoming)
                if all_completed:
                    next_ids.append(target)

            elif target_node.type == FlowNodeType.PARALLEL:
                branches = target_node.config.get("branches", [])
                for branch_id in branches:
                    if branch_id not in completed_node_ids and branch_id not in active_parallel_ids:
                        next_ids.append(branch_id)

            else:
                if target not in completed_node_ids:
                    next_ids.append(target)

    if not next_ids:
        end_nodes = definition.get_end_nodes()
        if all(n.id in completed_node_ids for n in end_nodes):
            return FlowTraversalResult(node_ids=[], is_blocked=False)
        return FlowTraversalResult(
            node_ids=[], is_blocked=True, block_reason="No more nodes to execute"
        )

    return FlowTraversalResult(node_ids=next_ids)


def _evaluate_condition(expression: str, context: set[str]) -> bool:
    """Evaluate a simple condition expression.

    Supported v1 expressions:
    - "node_X completed" — checks if node_X is in completed_node_ids
    - "always true" — always returns True
    - "always false" — always returns False

    Examples:
    - "node_approval completed" -> True if "node_approval" in context
    - "always true" -> True
    """
    expr = expression.strip().lower()

    if expr == "always true":
        return True
    if expr == "always false":
        return False

    if expr.endswith(" completed"):
        node_id = expr[:-10].strip()
        return node_id in context

    logger.warning("Unknown condition expression: %s", expression)
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
            }
            for e in definition.edges
        ],
    }
