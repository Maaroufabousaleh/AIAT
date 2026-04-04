"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import { ArrowLeft, Save, Play, Pause, X, AlertTriangle, CheckCircle, XCircle } from "lucide-react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  MarkerType,
  Node,
  Edge,
  NodeTypes,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useFlowStore } from "@/lib/flow-store";
import {
  NODE_TYPE_LABELS,
  FLOW_NODE_COLORS,
  FLOW_STATUS_COLORS,
  type FlowNodeDefinition,
  type FlowEdgeDefinition,
  type FlowDefinition,
  type FlowNodeType,
  type FlowInstanceStatus,
} from "@/lib/flow-types";

const CUSTOM_NODE_TYPES: NodeTypes = {};

function FlowNodeComponent({ data, selected }: { data: { label: string; type: FlowNodeType }; selected?: boolean }) {
  return (
    <div className={clsx(
      "px-3 py-2 rounded-lg border-2 min-w-[100px] text-center",
      selected ? "border-blue-500" : "border-gray-600",
      FLOW_NODE_COLORS[data.type] || "bg-gray-600"
    )}>
      <Handle type="target" position={Position.Top} className="!bg-gray-400" />
      <div className="text-sm font-medium text-white">{data.label}</div>
      <div className="text-xs text-white/70">{NODE_TYPE_LABELS[data.type]}</div>
      <Handle type="source" position={Position.Bottom} className="!bg-gray-400" />
    </div>
  );
}

CUSTOM_NODE_TYPES.flowNode = FlowNodeComponent;

const NODE_TYPES_OPTIONS: FlowNodeType[] = ["start", "task", "approval", "condition", "parallel", "join", "switch", "escalate", "end"];

function convertToReactFlow(
  nodes: FlowNodeDefinition[],
  edges: FlowEdgeDefinition[]
): { nodes: Node[]; edges: Edge[] } {
  const flowNodes: Node[] = nodes.map((n) => ({
    id: n.id,
    type: "flowNode",
    position: n.position || { x: Math.random() * 400, y: Math.random() * 400 },
    data: { label: n.label, type: n.type },
  }));

  const flowEdges: Edge[] = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.condition,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { stroke: "#6b7280" },
  }));

  return { nodes: flowNodes, edges: flowEdges };
}

function convertFromReactFlow(nodes: Node[], edges: Edge[]): FlowDefinition {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.type as FlowNodeType,
      label: n.data.label as string,
      config: {},
      position: { x: n.position.x, y: n.position.y },
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      condition: e.label as string | undefined,
    })),
  };
}

export default function FlowEditorPage() {
  const router = useRouter();
  const { currentFlow, fetchFlow, createFlow, updateFlow, loading, error, setError } = useFlowStore();
  
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isActive, setIsActive] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([] as Edge[]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [nodeConfig, setNodeConfig] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [rawSwitchCases, setRawSwitchCases] = useState("");

  const isNew = !currentFlow?.id;

  useEffect(() => {
    const flowId = window.location.pathname.split("/").pop();
    if (flowId && flowId !== "new") {
      fetchFlow(flowId).then((flow) => {
        if (flow) {
          setName(flow.name);
          setDescription(flow.description || "");
          setIsActive(flow.is_active);
          const { nodes: flowNodes, edges: flowEdges } = convertToReactFlow(
            flow.definition_json?.nodes || [],
            flow.definition_json?.edges || []
          );
          setNodes(flowNodes);
          setEdges(flowEdges);
        }
      });
    }
  }, [fetchFlow, setNodes, setEdges]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, id: `e${Date.now()}`, markerEnd: { type: MarkerType.ArrowClosed } }, eds));
    },
    [setEdges]
  );

  const onNodeClick = useCallback((_: unknown, node: Node) => {
    setSelectedNode(node);
    setShowConfig(true);
    setNodeConfig((node.data?.config as Record<string, unknown>) || {});
    setRawSwitchCases(JSON.stringify((node.data?.config as Record<string, unknown>)?.switch_cases || {}, null, 2));
  }, []);

  const addNode = useCallback((type: FlowNodeType) => {
    const id = `${type}_${Date.now()}`;
    const label = NODE_TYPE_LABELS[type] || type;
    const newNode: Node = {
      id,
      type: "flowNode",
      position: { x: Math.random() * 300 + 100, y: Math.random() * 300 + 100 },
      data: { label, type },
    };
    setNodes((nds) => [...nds, newNode]);
  }, [setNodes]);

  const updateNodeLabel = useCallback((nodeId: string, label: string) => {
    setNodes((nds) =>
      nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, label } } : n))
    );
  }, [setNodes]);

  const updateNodeConfig = useCallback((config: Record<string, unknown>) => {
    setNodeConfig(config);
    if (selectedNode) {
      setNodes((nds) =>
        nds.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, config } } : n))
      );
    }
  }, [selectedNode, setNodes]);

  const deleteSelectedNode = useCallback(() => {
    if (selectedNode) {
      setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
      setEdges((eds) => eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id));
      setSelectedNode(null);
      setShowConfig(false);
    }
  }, [selectedNode, setNodes, setEdges]);

  const validateFlow = useCallback((): string | null => {
    if (!name.trim()) return "Flow name is required";
    if (nodes.length === 0) return "Flow must have at least one node";
    
    const startNodes = nodes.filter((n) => n.data.type === "start");
    if (startNodes.length !== 1) return "Flow must have exactly one start node";
    
    const endNodes = nodes.filter((n) => n.data.type === "end");
    if (endNodes.length === 0) return "Flow must have at least one end node";
    
    return null;
  }, [name, nodes]);

  const handleSave = async () => {
    const validation = validateFlow();
    if (validation) {
      setValidationError(validation);
      return;
    }
    setValidationError(null);
    setSaving(true);

    const definition = convertFromReactFlow(nodes, edges);

    try {
      if (isNew) {
        const created = await createFlow({
          name,
          description,
          definition_json: definition,
          is_active: isActive,
        });
        if (created) {
          router.push(`/flows/${created.id}`);
        }
      } else {
        await updateFlow(currentFlow!.id, {
          name,
          description,
          definition_json: definition,
          is_active: isActive,
        });
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-screen flex flex-col">
      <div className="flex items-center justify-between p-4 border-b border-gray-800 bg-gray-900">
        <div className="flex items-center gap-3">
          <Link href="/flows" className="p-1 text-gray-500 hover:text-gray-300">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Flow name..."
              className="text-lg font-semibold bg-transparent text-white placeholder-gray-500 focus:outline-none"
            />
            {currentFlow && (
              <p className="text-xs text-gray-500">
                v{currentFlow.version} · Updated {formatDistanceToNow(new Date(currentFlow.updated_at), { addSuffix: true })}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-gray-400 mr-2">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              className="rounded border-gray-600 bg-gray-800 text-blue-600"
            />
            Active
          </label>
          <button
            onClick={handleSave}
            disabled={saving || loading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 text-white text-sm rounded-lg"
          >
            <Save size={14} />
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      {validationError && (
        <div className="flex items-center gap-2 px-4 py-2 bg-red-900/30 border-b border-red-800 text-red-400 text-sm">
          <AlertTriangle size={14} />
          {validationError}
        </div>
      )}

      <div className="flex-1 flex">
        <div className="w-48 border-r border-gray-800 bg-gray-900 p-3 space-y-2">
          <p className="text-xs font-medium text-gray-500 uppercase">Add Node</p>
          {NODE_TYPES_OPTIONS.map((type) => (
            <button
              key={type}
              onClick={() => addNode(type)}
              className={clsx(
                "w-full px-2 py-1.5 text-xs text-left rounded border border-gray-700 hover:bg-gray-800 transition-colors",
                FLOW_NODE_COLORS[type]
              )}
            >
              {NODE_TYPE_LABELS[type]}
            </button>
          ))}
        </div>

        <div className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            nodeTypes={CUSTOM_NODE_TYPES}
            fitView
            className="bg-gray-950"
          >
            <Background color="#374151" gap={16} />
            <Controls className="!bg-gray-800 !border-gray-700" />
            <MiniMap className="!bg-gray-900 !border-gray-700" />
          </ReactFlow>
        </div>

        {showConfig && selectedNode && (
          <div className="w-72 border-l border-gray-800 bg-gray-900 p-4 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-white">Node Config</h3>
              <button onClick={() => setShowConfig(false)} className="text-gray-500 hover:text-gray-300">
                <X size={14} />
              </button>
            </div>
            
            <div>
              <label className="block text-xs text-gray-500 mb-1">Label</label>
              <input
                value={selectedNode.data.label as string}
                onChange={(e) => updateNodeLabel(selectedNode.id, e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Type</label>
              <div className={clsx(
                "px-2 py-1 rounded text-xs",
                FLOW_NODE_COLORS[selectedNode.data.type as FlowNodeType]
              )}>
                {NODE_TYPE_LABELS[selectedNode.data.type as FlowNodeType]}
              </div>
            </div>

            {selectedNode.data.type === "task" && (
              <>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Team ID</label>
                  <input
                    value={(nodeConfig.team_id as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, team_id: e.target.value })}
                    placeholder="dept_devops"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Action</label>
                  <input
                    value={(nodeConfig.action as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, action: e.target.value })}
                    placeholder="execute_task"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Timeout (seconds)</label>
                  <input
                    type="number"
                    value={Number(nodeConfig.timeout_seconds) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, timeout_seconds: parseInt(e.target.value) || 0 })}
                    placeholder="300"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Retries</label>
                  <input
                    type="number"
                    value={Number(nodeConfig.retries) || 0}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, retries: parseInt(e.target.value) || 0 })}
                    placeholder="3"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
              </>
            )}

            {selectedNode.data.type === "approval" && (
              <>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Approver Role</label>
                  <input
                    value={(nodeConfig.approver_role as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_role: e.target.value })}
                    placeholder="exec_ceo"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Or Approver User</label>
                  <input
                    value={(nodeConfig.approver_user as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_user: e.target.value })}
                    placeholder="human"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
              </>
            )}

            {selectedNode.data.type === "condition" && (
              <div>
                <label className="block text-xs text-gray-500 mb-1">Expression</label>
                <input
                  value={(nodeConfig.expression as string) || ""}
                  onChange={(e) => updateNodeConfig({ ...nodeConfig, expression: e.target.value })}
                  placeholder="node_X completed"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                />
              </div>
            )}

            {selectedNode.data.type === "switch" && (
              <>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Switch Key (context field)</label>
                  <input
                    value={(nodeConfig.switch_key as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, switch_key: e.target.value })}
                    placeholder="decision_result"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Switch Cases (JSON)</label>
                  <textarea
                    value={rawSwitchCases}
                    onChange={(e) => {
                      setRawSwitchCases(e.target.value);
                      try {
                        const parsed = JSON.parse(e.target.value);
                        updateNodeConfig({ ...nodeConfig, switch_cases: parsed });
                        setJsonError(null);
                      } catch (err) {
                        setJsonError("Invalid JSON");
                      }
                    }}
                    placeholder='{"approved": "node_approved", "rejected": "node_rejected"}'
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white h-20 font-mono"
                  />
                  {jsonError && <div className="text-xs text-red-400 mt-1">{jsonError}</div>}
                </div>
              </>
            )}

            {selectedNode.data.type === "escalate" && (
              <>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Escalate To Team</label>
                  <input
                    value={(nodeConfig.escalate_to_team as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_team: e.target.value })}
                    placeholder="exec_ceo"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Or Escalate To Agent</label>
                  <input
                    value={(nodeConfig.escalate_to_agent as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_agent: e.target.value })}
                    placeholder="agent_id"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                  />
                </div>
              </>
            )}

            {selectedNode.data.type === "parallel" && (
              <div>
                <label className="block text-xs text-gray-500 mb-1">Branches (comma-separated node IDs)</label>
                <input
                  value={((nodeConfig.branches as string[]) || []).join(", ")}
                  onChange={(e) => updateNodeConfig({ 
                    ...nodeConfig, 
                    branches: e.target.value.split(",").map(s => s.trim()).filter(Boolean)
                  })}
                  placeholder="branch_1, branch_2"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                />
              </div>
            )}

            <button
              onClick={deleteSelectedNode}
              className="w-full px-2 py-1.5 bg-red-600/20 text-red-400 border border-red-800 text-xs rounded hover:bg-red-600/40"
            >
              Delete Node
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
