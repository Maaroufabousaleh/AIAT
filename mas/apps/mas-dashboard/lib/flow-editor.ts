import type { Edge, Node } from "@xyflow/react";

import type { FlowDefinition, FlowEdgeDefinition, FlowNodeDefinition, FlowNodeType } from "@/lib/flow-types";
import { FLOW_NODE_SCHEMA_CATALOG } from "@/lib/generated/flow-node-schemas";

export function getFlowNodeSchema(type: FlowNodeType) {
  return FLOW_NODE_SCHEMA_CATALOG.node_types[type];
}

export function convertFlowToReactFlow(
  nodes: FlowNodeDefinition[],
  edges: FlowEdgeDefinition[]
): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: nodes.map((node) => ({
      id: node.id,
      type: "flowNode",
      position: node.position || { x: Math.random() * 400, y: Math.random() * 400 },
      data: {
        label: node.label,
        type: node.type,
        config: node.config || {},
      },
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.condition,
    })),
  };
}

export function convertReactFlowToFlow(
  nodes: Node[],
  edges: Edge[],
  metadata?: Record<string, unknown>
): FlowDefinition {
  return {
    schema_version: FLOW_NODE_SCHEMA_CATALOG.schema_version,
    ...(metadata && Object.keys(metadata).length > 0 ? { metadata } : {}),
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.type as FlowNodeType,
      label: node.data.label as string,
      config: (node.data.config as Record<string, unknown>) || {},
      position: { x: node.position.x, y: node.position.y },
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      condition: edge.label as string | undefined,
    })),
  };
}
