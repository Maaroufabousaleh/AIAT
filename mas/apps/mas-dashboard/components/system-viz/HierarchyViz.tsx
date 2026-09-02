"use client";

import { useMemo, useCallback, useState } from "react";
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

import type {
  PermissionData,
  TeamHierarchyNode,
} from "@/lib/system-viz-types";
import { TIER_COLORS } from "@/lib/system-viz-types";

interface TeamNodeData extends Record<string, unknown> {
  label: string;
  teamId: string;
  tier: string;
  displayName: string;
  agentCount: number;
  isAdmin?: boolean;
  policyState?: "allowed" | "denied" | "unknown";
  policySenderRole?: string;
}

const ROLE_LABELS: Record<string, string> = {
  orchestrator: "Orchestrator",
  executive: "Executive",
  c_suite: "C-Suite",
  admin: "Admin",
  worker: "Worker",
  sub_agent: "Sub-Agent",
};

function TeamNode({ data, selected }: { data: TeamNodeData; selected?: boolean }) {
  const tierColor = TIER_COLORS[data.tier] || "#6b7280";
  const policyColor =
    data.policyState === "allowed"
      ? "#34d399"
      : data.policyState === "denied"
        ? "#f87171"
        : "#475569";
  const policyLabel =
    data.policyState === "allowed"
      ? "Allowed path"
      : data.policyState === "denied"
        ? "Denied path"
        : "Policy path not evaluated";
  
  return (
    <div
      aria-label={`${data.displayName}: ${
        data.policySenderRole
          ? `${policyLabel} for ${ROLE_LABELS[data.policySenderRole] || data.policySenderRole}`
          : "communication policy overlay off"
      }`}
      className={`
        min-w-[140px] px-3 py-2 rounded-lg border-2 bg-gray-900 transition-all
        ${selected ? "border-blue-500 ring-2 ring-blue-500/30" : "border-gray-700"}
        hover:border-gray-500 hover:shadow-lg
      `}
      style={
        data.policyState
          ? {
              borderColor: policyColor,
              boxShadow: `0 0 0 1px ${policyColor}33`,
            }
          : undefined
      }
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

      {data.policyState && (
        <div
          className="mt-1 text-[10px] text-center font-medium"
          style={{ color: policyColor }}
        >
          {policyLabel}
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
  permissionData?: Pick<PermissionData, "communicationMatrix"> | null;
}

export function HierarchyViz({
  hierarchy,
  onNodeClick,
  selectedTeam,
  highlightedPath,
  permissionData,
}: HierarchyVizProps) {
  const [policyOverlay, setPolicyOverlay] = useState(false);
  const [policySenderRole, setPolicySenderRole] = useState("worker");
  const policyRoles = Object.keys(permissionData?.communicationMatrix ?? {});
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
      const policyEntry =
        permissionData?.communicationMatrix?.[policySenderRole]?.[node.teamId];
      const policyState = policyOverlay
        ? policyEntry
          ? policyEntry.allowed
            ? "allowed"
            : "denied"
          : "unknown"
        : undefined;
      
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
          policyState,
          policySenderRole: policyOverlay ? policySenderRole : undefined,
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
            stroke: isHighlighted
              ? "#3b82f6"
              : policyState === "allowed"
                ? "#34d399"
                : policyState === "denied"
                  ? "#f87171"
                  : "#6b7280",
            strokeWidth: isHighlighted || policyState ? 3 : 1,
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
  }, [hierarchy, highlightedPath, permissionData, policyOverlay, policySenderRole]);

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
    <div className="relative h-full min-h-[520px]">
      <div
        className="absolute left-3 right-3 top-3 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-slate-700/80 bg-slate-950/90 px-3 py-2 text-xs text-slate-300 shadow-lg"
        role="region"
        aria-label="Communication policy overlay controls"
      >
        <button
          type="button"
          aria-label="Toggle communication policy overlay"
          aria-pressed={policyOverlay}
          onClick={() => setPolicyOverlay((visible) => !visible)}
          disabled={policyRoles.length === 0}
          className="inline-flex min-h-11 items-center rounded-md border border-slate-600 px-2 py-1 font-medium text-slate-100 transition hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {policyOverlay ? "Hide communication policy" : "Show communication policy"}
        </button>
        {policyOverlay && (
          <label className="flex items-center gap-2" htmlFor="hierarchy-policy-role">
            <span className="text-slate-400">Sender role</span>
            <select
              id="hierarchy-policy-role"
              aria-label="Communication policy sender role"
              value={policySenderRole}
              onChange={(event) => setPolicySenderRole(event.target.value)}
              className="min-h-11 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
            >
              {policyRoles.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role] || role}
                </option>
              ))}
            </select>
          </label>
        )}
        {policyOverlay && (
          <span className="flex items-center gap-3" aria-label="Communication policy legend">
            <span className="text-emerald-300">● allowed</span>
            <span className="text-red-300">● denied</span>
          </span>
        )}
      </div>
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
          nodeColor={(node) => {
            const data = node.data as TeamNodeData;
            if (data.policyState === "allowed") return "#34d399";
            if (data.policyState === "denied") return "#f87171";
            return TIER_COLORS[data.tier] || "#6b7280";
          }}
          maskColor="rgba(0, 0, 0, 0.5)"
        />
      </ReactFlow>
    </div>
  );
}
