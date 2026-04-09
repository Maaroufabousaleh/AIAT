"use client";

import { useMemo, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Node,
  Edge,
  MarkerType,
  Handle,
  Position,
  NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { TeamHierarchyNode } from "@/lib/system-viz-types";
import { TIER_COLORS } from "@/lib/system-viz-types";

interface TeamNodeData extends Record<string, unknown> {
  label: string;
  teamId: string;
  tier: string;
  displayName: string;
  agentCount: number;
  isAdmin?: boolean;
}

function TeamNode({ data, selected }: { data: TeamNodeData; selected?: boolean }) {
  const tierColor = TIER_COLORS[data.tier] || "#6b7280";
  
  return (
    <div
      className={`
        min-w-[140px] px-3 py-2 rounded-lg border-2 bg-gray-900 transition-all
        ${selected ? "border-blue-500 ring-2 ring-blue-500/30" : "border-gray-700"}
        hover:border-gray-500 hover:shadow-lg
      `}
    >
      <Handle type="target" position={Position.Top} className="!bg-gray-400 !w-2 !h-2" />
      
      <div className="flex items-center gap-2 mb-1">
        <div
          className="w-2 h-2 rounded-full"
          style={{ backgroundColor: tierColor }}
        />
        <span className="text-xs text-gray-400 uppercase">{data.tier}</span>
      </div>
      
      <div className="text-sm font-semibold text-white text-center">{data.displayName}</div>
      <div className="text-xs text-gray-500 text-center">{data.label}</div>
      
      {data.isAdmin && (
        <div className="mt-1 text-xs text-blue-400 text-center">
          Admin: {data.agentCount} agent{data.agentCount > 1 ? "s" : ""}
        </div>
      )}
      {!data.isAdmin && data.agentCount > 0 && (
        <div className="mt-1 text-xs text-gray-500 text-center">
          {data.agentCount} worker{data.agentCount > 1 ? "s" : ""}
        </div>
      )}
      
      <Handle type="source" position={Position.Bottom} className="!bg-gray-400 !w-2 !h-2" />
    </div>
  );
}

interface HierarchyVizProps {
  hierarchy: TeamHierarchyNode[];
  onNodeClick?: (teamId: string) => void;
  selectedTeam?: string | null;
  highlightedPath?: string[] | null;
}

export function HierarchyViz({
  hierarchy,
  onNodeClick,
  selectedTeam,
  highlightedPath,
}: HierarchyVizProps) {
  const { nodes, edges } = useMemo(() => {
    const flowNodes: Node[] = [];
    const flowEdges: Edge[] = [];
    
    const Y_SPACING = 180;
    const X_SPACING = 200;
    
    const processNode = (
      node: TeamHierarchyNode,
      x: number,
      y: number,
      parentId?: string
    ) => {
      const isHighlighted = highlightedPath?.includes(node.teamId);
      
      flowNodes.push({
        id: node.teamId,
        type: "teamNode",
        position: { x, y },
        data: {
          label: node.teamId,
          teamId: node.teamId,
          tier: node.tier,
          displayName: node.displayName,
          agentCount: node.workers.length + 1,
          isAdmin: true,
        },
        style: {
          opacity: highlightedPath && !isHighlighted ? 0.4 : 1,
        },
      });
      
      if (parentId) {
        flowEdges.push({
          id: `${parentId}-${node.teamId}`,
          source: parentId,
          target: node.teamId,
          markerEnd: { type: MarkerType.ArrowClosed, color: "#6b7280" },
          style: {
            stroke: isHighlighted ? "#3b82f6" : "#6b7280",
            strokeWidth: isHighlighted ? 3 : 1,
          },
          animated: isHighlighted,
        });
      }
      
      if (node.children && node.children.length > 0) {
        const totalWidth = (node.children.length - 1) * X_SPACING;
        const startX = x - totalWidth / 2;
        
        node.children.forEach((child, index) => {
          processNode(child, startX + index * X_SPACING, y + Y_SPACING, node.teamId);
        });
      }
    };
    
    if (hierarchy.length > 0) {
      processNode(hierarchy[0], 400, 50);
    }
    
    return { nodes: flowNodes, edges: flowEdges };
  }, [hierarchy, highlightedPath]);

  const nodeTypes = useMemo(() => ({ teamNode: TeamNode }), []);

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      const data = node.data as TeamNodeData;
      onNodeClick?.(data.teamId);
    },
    [onNodeClick]
  );

  if (hierarchy.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        No hierarchy data available
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={handleNodeClick}
      fitView
      className="bg-gray-950"
      defaultEdgeOptions={{
        type: "smoothstep",
      }}
    >
      <Background color="#374151" gap={16} />
      <Controls className="!bg-gray-800 !border-gray-700" />
      <MiniMap
        className="!bg-gray-900 !border-gray-700"
        nodeColor={(node) => TIER_COLORS[(node.data as TeamNodeData).tier] || "#6b7280"}
        maskColor="rgba(0, 0, 0, 0.5)"
      />
    </ReactFlow>
  );
}