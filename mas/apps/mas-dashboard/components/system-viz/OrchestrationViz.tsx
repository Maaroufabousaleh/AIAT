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
import { clsx } from "clsx";
import { Play, Pause, FastForward, GitBranch, Layers, ArrowRight } from "lucide-react";

import type { OrchestrationFlow, WorkflowState } from "@/lib/system-viz-types";
import { TIER_COLORS } from "@/lib/system-viz-types";

interface OrchestrationNodeData extends Record<string, unknown> {
  label: string;
  type: string;
  state?: string;
  team?: string;
  approver?: string;
  flowId: string;
}

function FlowNode({ data, selected }: { data: OrchestrationNodeData; selected?: boolean }) {
  const typeColors: Record<string, string> = {
    start: "bg-green-600 border-green-400",
    end: "bg-red-600 border-red-400",
    task: "bg-blue-600 border-blue-400",
    approval: "bg-amber-600 border-amber-400",
    condition: "bg-purple-600 border-purple-400",
    parallel: "bg-cyan-600 border-cyan-400",
    join: "bg-indigo-600 border-indigo-400",
    switch: "bg-pink-600 border-pink-400",
    escalate: "bg-orange-600 border-orange-400",
  };

  return (
    <div
      className={clsx(
        "px-3 py-2 rounded-lg border-2 min-w-[120px] text-center transition-all",
        typeColors[data.type] || "bg-gray-600 border-gray-400",
        selected ? "ring-2 ring-white/50" : ""
      )}
    >
      <Handle type="target" position={Position.Top} className="!bg-white !w-2 !h-2" />
      <div className="text-sm font-medium text-white">{data.label}</div>
      <div className="text-xs text-white/70 capitalize">{data.type}</div>
      {data.state && (
        <div className="text-xs text-white/50 mt-1 font-mono">{data.state}</div>
      )}
      {data.team && (
        <div className="text-xs text-white/50">{data.team}</div>
      )}
      {data.approver && (
        <div className="text-xs text-white/50">Approver: {data.approver}</div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-white !w-2 !h-2" />
    </div>
  );
}

interface OrchestrationVizProps {
  flows: OrchestrationFlow[];
  states: WorkflowState[];
  selectedFlowId?: string | null;
  onFlowSelect?: (flowId: string | null) => void;
  highlightedPath?: string[] | null;
  onTracePath?: (nodeId: string) => void;
}

export function OrchestrationViz({
  flows,
  states,
  selectedFlowId,
  onFlowSelect,
  highlightedPath,
  onTracePath,
}: OrchestrationVizProps) {
  const [viewMode, setViewMode] = useState<"graph" | "states">("graph");

  const selectedFlow = flows.find(f => f.id === selectedFlowId);

  const { nodes, edges } = useMemo(() => {
    if (!selectedFlow) return { nodes: [] as Node[], edges: [] as Edge[] };

    const flowNodes: Node[] = selectedFlow.nodes.map((n, i) => ({
      id: n.id,
      type: "flowNode",
      position: {
        x: 150 + (i % 4) * 180,
        y: 80 + Math.floor(i / 4) * 120,
      },
      data: {
        label: n.label,
        type: n.type,
        state: n.state,
        team: n.team,
        approver: n.approver,
        flowId: selectedFlow.id,
      },
      style: {
        opacity: highlightedPath && !highlightedPath.includes(n.id) ? 0.4 : 1,
      },
    }));

    const flowEdges: Edge[] = selectedFlow.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      label: e.condition,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: {
        stroke: highlightedPath?.includes(e.source) && highlightedPath?.includes(e.target)
          ? "#3b82f6"
          : "#6b7280",
        strokeWidth: highlightedPath?.includes(e.source) && highlightedPath?.includes(e.target)
          ? 3
          : 1,
      },
      animated: highlightedPath?.includes(e.source) && highlightedPath?.includes(e.target),
    }));

    return { nodes: flowNodes, edges: flowEdges };
  }, [selectedFlow, highlightedPath]);

  const nodeTypes = useMemo(() => ({ flowNode: FlowNode }), []);

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      const data = node.data as OrchestrationNodeData;
      onTracePath?.(data.label);
    },
    [onTracePath]
  );

  if (!selectedFlowId) {
    return (
      <div className="h-full flex flex-col">
        <div className="flex-shrink-0 p-4 border-b border-gray-800">
          <h3 className="text-sm font-medium text-white mb-3">Select a Flow</h3>
          <div className="grid grid-cols-2 gap-2">
            {flows.map(flow => (
              <button
                type="button"
                key={flow.id}
                onClick={() => onFlowSelect?.(flow.id)}
                className="min-h-11 p-3 bg-gray-900 hover:bg-gray-800 rounded-lg text-left transition-colors"
              >
                <div className="flex items-center gap-2 mb-1">
                  <GitBranch size={14} className="text-blue-400" />
                  <span className="text-sm font-medium text-white">{flow.name}</span>
                </div>
                <p className="text-xs text-gray-500">{flow.description}</p>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex-shrink-0 flex items-center justify-between p-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onFlowSelect?.(null)}
            className="inline-flex min-h-11 items-center text-sm text-gray-400 hover:text-white"
          >
            ← Back
          </button>
          <span className="text-white font-medium">{selectedFlow?.name}</span>
        </div>
        
        <div className="flex items-center gap-1 bg-gray-900 rounded p-1">
          <button
            type="button"
            onClick={() => setViewMode("graph")}
            className={clsx(
              "min-h-11 px-2 py-1 text-xs rounded",
              viewMode === "graph" ? "bg-blue-600 text-white" : "text-white/70"
            )}
          >
            Graph
          </button>
          <button
            type="button"
            onClick={() => setViewMode("states")}
            className={clsx(
              "min-h-11 px-2 py-1 text-xs rounded",
              viewMode === "states" ? "bg-blue-600 text-white" : "text-white/70"
            )}
          >
            States
          </button>
        </div>
      </div>

      {viewMode === "graph" ? (
        <div className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={handleNodeClick}
            fitView
            className="bg-gray-950"
            defaultEdgeOptions={{ type: "smoothstep" }}
          >
            <Background color="#374151" gap={16} />
            <Controls className="!bg-gray-800 !border-gray-700" />
            <MiniMap className="!bg-gray-900 !border-gray-700" />
          </ReactFlow>
        </div>
      ) : (
        <div className="flex-1 overflow-auto p-4">
          <h4 className="text-sm font-medium text-gray-400 mb-3">Workflow States</h4>
          <div className="relative">
            <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-gray-700" />
            {states.map((state, idx) => (
              <div key={state.id} className="relative flex items-start gap-4 pb-4">
                <div className={clsx(
                  "w-8 h-8 rounded-full flex items-center justify-center z-10",
                  selectedFlow?.nodes.some(n => n.state === state.id)
                    ? "bg-blue-600 text-white"
                    : "bg-gray-800 text-gray-500"
                )}>
                  {idx + 1}
                </div>
                <div className="pt-1">
                  <div className="text-sm font-medium text-white">{state.label}</div>
                  <div className="text-xs text-gray-500">{state.description}</div>
                  <div className="text-xs text-gray-600 font-mono mt-1">{state.id}</div>
                </div>
              </div>
            ))}
          </div>

          <h4 className="text-sm font-medium text-gray-400 mt-6 mb-3">Transitions</h4>
          <div className="space-y-2">
              {Object.entries(
              flows.find(f => f.id === selectedFlowId)?.nodes.reduce((acc, node) => {
                if (node.state) {
                  const outgoing = flows.find(f => f.id === selectedFlowId)?.edges.filter(e => 
                    selectedFlow?.nodes.find(n => n.id === e.source)?.state === node.state
                  ) || [];
                  if (outgoing.length > 0) {
                    acc[node.state] = outgoing.map(e => {
                      const targetNode = selectedFlow?.nodes.find(n => n.id === e.target);
                      return targetNode?.state || e.target;
                    });
                  }
                }
                return acc;
              }, {} as Record<string, string[]>) || {}
            ).map(([from, to]: [string, string[]]) => (
              <div key={from} className="flex items-center gap-2 text-sm">
                <span className="text-gray-400 font-mono">{from}</span>
                <ArrowRight size={12} className="text-gray-600" />
                <span className="text-gray-300">{to.join(", ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
