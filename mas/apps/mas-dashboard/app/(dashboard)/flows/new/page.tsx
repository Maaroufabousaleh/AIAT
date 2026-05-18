"use client";

import { useState, useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import { ArrowLeft, Save, AlertTriangle } from "lucide-react";
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

import {
  NODE_TYPE_LABELS,
  FLOW_NODE_COLORS,
  type FlowNodeType,
} from "@/lib/flow-types";
import { convertReactFlowToFlow } from "@/lib/flow-editor";

const CUSTOM_NODE_TYPES: NodeTypes = {};
let pendingConnectionSource: string | null = null;

function FlowNodeComponent({ id, data, selected }: { id: string; data: { label: string; type: FlowNodeType }; selected?: boolean }) {
  return (
    <div className={clsx(
      "px-3 py-2 rounded-lg border-2 min-w-[100px] text-center",
      selected ? "border-blue-500" : "border-gray-600",
      FLOW_NODE_COLORS[data.type] || "bg-gray-600"
    )}
      onMouseDown={(event) => {
        if ((event.target as HTMLElement).classList.contains("react-flow__handle-source")) {
          pendingConnectionSource = id;
        }
      }}
      onMouseUp={(event) => {
        if ((event.target as HTMLElement).classList.contains("react-flow__handle-target")) {
          if (pendingConnectionSource && pendingConnectionSource !== id) {
            window.dispatchEvent(new CustomEvent("flow-quick-connect", { detail: { source: pendingConnectionSource, target: id } }));
          }
          pendingConnectionSource = null;
        }
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="react-flow__handle-target !bg-gray-400"
      />
      <div className="text-sm font-medium text-white">{data.label}</div>
      <div className="text-xs text-white/70">{NODE_TYPE_LABELS[data.type]}</div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="react-flow__handle-source !bg-gray-400"
      />
    </div>
  );
}

CUSTOM_NODE_TYPES.flowNode = FlowNodeComponent;

const NODE_TYPES_OPTIONS: FlowNodeType[] = ["start", "task", "approval", "condition", "parallel", "join", "switch", "escalate", "end"];

export default function NewFlowPage() {
  const router = useRouter();
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

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, id: `e${Date.now()}`, markerEnd: { type: MarkerType.ArrowClosed } }, eds));
    },
    [setEdges]
  );

  useEffect(() => {
    const handleMouseDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.classList.contains("react-flow__handle-source")) return;
      pendingConnectionSource = target.closest(".react-flow__node")?.getAttribute("data-id") || null;
    };
    const handleMouseUp = (event: MouseEvent) => {
      const directTarget = document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null;
      const target = directTarget?.classList.contains("react-flow__handle-target")
        ? directTarget
        : Array.from(document.querySelectorAll<HTMLElement>(".react-flow__handle-target")).find((handle) => {
            const rect = handle.getBoundingClientRect();
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;
            return Math.hypot(centerX - event.clientX, centerY - event.clientY) <= 24;
          });
      if (!target) return;
      const connectionTarget = target.closest(".react-flow__node")?.getAttribute("data-id");
      if (pendingConnectionSource && connectionTarget && pendingConnectionSource !== connectionTarget) {
        window.dispatchEvent(new CustomEvent("flow-quick-connect", { detail: { source: pendingConnectionSource, target: connectionTarget } }));
      }
      pendingConnectionSource = null;
    };
    const handleQuickConnect = (event: Event) => {
      const { source, target } = (event as CustomEvent<{ source: string; target: string }>).detail;
      setEdges((eds) => {
        if (eds.some((edge) => edge.source === source && edge.target === target)) return eds;
        return addEdge({ source, target, id: `e${Date.now()}`, markerEnd: { type: MarkerType.ArrowClosed } }, eds);
      });
    };
    document.addEventListener("mousedown", handleMouseDown, true);
    document.addEventListener("mouseup", handleMouseUp, true);
    window.addEventListener("flow-quick-connect", handleQuickConnect);
    return () => {
      document.removeEventListener("mousedown", handleMouseDown, true);
      document.removeEventListener("mouseup", handleMouseUp, true);
      window.removeEventListener("flow-quick-connect", handleQuickConnect);
    };
  }, [setEdges]);

  const onNodeClick = useCallback((_: unknown, node: Node) => {
    setSelectedNode(node);
    setShowConfig(true);
    setNodeConfig((node.data?.config as Record<string, unknown>) || {});
    setRawSwitchCases(JSON.stringify((node.data?.config as Record<string, unknown>)?.switch_cases || {}, null, 2));
  }, []);

  const addNode = useCallback((type: FlowNodeType) => {
    const index = nodes.length;
    const id = `${type}_${Date.now()}`;
    const label = NODE_TYPE_LABELS[type] || type;
    const newNode: Node = {
      id,
      type: "flowNode",
      position: { x: (index % 4) * 180 + 80, y: Math.floor(index / 4) * 140 + 80 },
      data: { label, type },
    };
    setNodes((nds) => [...nds, newNode]);
  }, [nodes.length, setNodes]);

  const updateNodeLabel = useCallback((nodeId: string, label: string) => {
    setNodes((nds) =>
      nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, label } } : n))
    );
    setSelectedNode((node) => (node?.id === nodeId ? { ...node, data: { ...node.data, label } } : node));
  }, [setNodes]);

  const updateNodeConfig = useCallback((config: Record<string, unknown>) => {
    setNodeConfig(config);
    if (selectedNode) {
      setNodes((nds) =>
        nds.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, config } } : n))
      );
      setSelectedNode((node) => (node ? { ...node, data: { ...node.data, config } } : node));
    }
  }, [selectedNode, setNodes]);

  const switchCases = useMemo(() => (
    nodeConfig.switch_cases && typeof nodeConfig.switch_cases === "object" && !Array.isArray(nodeConfig.switch_cases)
      ? (nodeConfig.switch_cases as Record<string, string>)
      : {}
  ), [nodeConfig.switch_cases]);
  const switchCaseEntries = useMemo(() => Object.entries(switchCases), [switchCases]);

  const setSwitchCases = useCallback((cases: Record<string, string>) => {
    setRawSwitchCases(JSON.stringify(cases, null, 2));
    setJsonError(null);
    updateNodeConfig({ ...nodeConfig, switch_cases: cases });
  }, [nodeConfig, updateNodeConfig]);

  const updateSwitchCase = useCallback((index: number, field: "key" | "target", value: string) => {
    const entries = [...switchCaseEntries];
    const [currentKey, currentTarget] = entries[index] ?? ["", ""];
    entries[index] = field === "key" ? [value, currentTarget] : [currentKey, value];
    setSwitchCases(Object.fromEntries(entries.filter(([key]) => key.trim())));
  }, [setSwitchCases, switchCaseEntries]);

  const addSwitchCase = useCallback(() => {
    const nextCases = { ...switchCases };
    let index = switchCaseEntries.length + 1;
    let key = `case_${index}`;
    while (Object.prototype.hasOwnProperty.call(nextCases, key)) {
      index += 1;
      key = `case_${index}`;
    }
    nextCases[key] = "";
    setSwitchCases(nextCases);
  }, [setSwitchCases, switchCaseEntries.length, switchCases]);

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
    if (validation) { setValidationError(validation); return; }
    setValidationError(null);
    setSaving(true);
    const definition = convertReactFlowToFlow(nodes, edges);
    try {
      const res = await fetch("/api/flows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description, definition_json: definition, is_active: isActive }),
      });
      if (res.ok) {
        const created = await res.json();
        router.push(`/flows/${created.id}`);
      } else {
        const err = await res.json().catch(() => ({}));
        setValidationError(err.detail || err.error || "Failed to create flow");
      }
    } catch (e) {
      setValidationError("Network error");
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
              placeholder="New flow name..."
              data-testid="flow-name-input"
              className="text-lg font-semibold bg-transparent text-white placeholder-gray-500 focus:outline-none"
            />
            <p className="text-xs text-gray-500">New flow · unsaved</p>
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
            disabled={saving}
            data-testid="flow-save-button"
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 text-white text-sm rounded-lg"
          >
            <Save size={14} />
            {saving ? "Creating..." : "Create Flow"}
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
        {/* Left panel: node palette */}
        <div className="w-48 border-r border-gray-800 bg-gray-900 p-3 space-y-2">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-3">Node Types</p>
          {NODE_TYPES_OPTIONS.map((type) => (
            <button
              key={type}
              onClick={() => addNode(type)}
              data-testid={`add-node-${type}`}
              className={clsx(
                "w-full px-2 py-1.5 text-xs text-left rounded border border-gray-700 hover:opacity-90 transition-opacity",
                FLOW_NODE_COLORS[type]
              )}
            >
              + {NODE_TYPE_LABELS[type]}
            </button>
          ))}
          <div className="pt-4 border-t border-gray-800">
            <p className="text-xs text-gray-600 leading-relaxed">
              Click a node type to add it. Drag nodes to position. Connect nodes by dragging from the bottom handle.
            </p>
          </div>
        </div>

        {/* Canvas */}
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
            <MiniMap className="!bg-gray-900 !border-gray-700 !pointer-events-none" />
          </ReactFlow>
        </div>

        {/* Right panel: node config */}
        {showConfig && selectedNode && (
          <div className="w-72 border-l border-gray-800 bg-gray-900 p-4 space-y-4 overflow-y-auto">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-white">Node Config</h3>
              <button onClick={() => setShowConfig(false)} className="text-gray-500 hover:text-gray-300 text-lg leading-none">&times;</button>
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Label</label>
              <input
                value={selectedNode.data.label as string}
                onChange={(e) => updateNodeLabel(selectedNode.id, e.target.value)}
                data-testid="node-label-input"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Type</label>
              <div className={clsx("px-2 py-1 rounded text-xs", FLOW_NODE_COLORS[selectedNode.data.type as FlowNodeType])}>
                {NODE_TYPE_LABELS[selectedNode.data.type as FlowNodeType]}
              </div>
            </div>

            {selectedNode.data.type === "task" && (
              <>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Team ID</label>
                  <input value={(nodeConfig.team_id as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, team_id: e.target.value })} placeholder="dept_devops" data-testid="task-team-id-input" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Action</label>
                  <input value={(nodeConfig.action as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, action: e.target.value })} placeholder="execute_task" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Timeout (seconds)</label>
                  <input type="number" value={Number(nodeConfig.timeout_seconds) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, timeout_seconds: parseInt(e.target.value) || 0 })} placeholder="300" data-testid="task-timeout-input" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Retries</label>
                  <input type="number" value={Number(nodeConfig.retries) || 0} onChange={(e) => updateNodeConfig({ ...nodeConfig, retries: parseInt(e.target.value) || 0 })} placeholder="3" data-testid="task-retries-input" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Escalate To Team</label>
                  <input value={(nodeConfig.escalate_to_team as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_team: e.target.value })} placeholder="exec_ceo" data-testid="task-escalate-team-input" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Or Escalate To Agent</label>
                  <input value={(nodeConfig.escalate_to_agent as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_agent: e.target.value })} placeholder="agent_id" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
              </>
            )}

            {selectedNode.data.type === "approval" && (
              <>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Approver Role</label>
                  <input value={(nodeConfig.approver_role as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_role: e.target.value })} placeholder="exec_ceo" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Or Approver User</label>
                  <input value={(nodeConfig.approver_user as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_user: e.target.value })} placeholder="human" data-testid="approval-user-input" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
              </>
            )}

            {selectedNode.data.type === "condition" && (
              <div>
                <label className="block text-xs text-gray-500 mb-1">Expression</label>
                <input value={(nodeConfig.expression as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, expression: e.target.value })} placeholder="node_X completed" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
              </div>
            )}

            {selectedNode.data.type === "switch" && (
              <>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Switch Key</label>
                  <input value={(nodeConfig.switch_key as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, switch_key: e.target.value })} placeholder="decision_result" data-testid="switch-key-input" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Switch Cases (JSON)</label>
                  <div className="space-y-2 mb-2">
                    {switchCaseEntries.map(([caseKey, target], index) => (
                      <div key={`${caseKey}-${index}`} className="grid grid-cols-2 gap-2">
                        <input
                          value={caseKey}
                          onChange={(e) => updateSwitchCase(index, "key", e.target.value)}
                          placeholder="approved"
                          data-testid={`switch-case-key-${index}`}
                          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                        />
                        <input
                          value={target}
                          onChange={(e) => updateSwitchCase(index, "target", e.target.value)}
                          placeholder="target_node_id"
                          data-testid={`switch-case-target-${index}`}
                          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                        />
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={addSwitchCase}
                      data-testid="switch-case-add-button"
                      className="w-full px-2 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-200 text-xs rounded"
                    >
                      Add Case
                    </button>
                  </div>
                  <textarea
                    value={rawSwitchCases}
                    onChange={(e) => {
                      setRawSwitchCases(e.target.value);
                      try { updateNodeConfig({ ...nodeConfig, switch_cases: JSON.parse(e.target.value) }); setJsonError(null); }
                      catch { setJsonError("Invalid JSON"); }
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
                  <input value={(nodeConfig.escalate_to_team as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_team: e.target.value })} placeholder="exec_ceo" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Or Escalate To Agent</label>
                  <input value={(nodeConfig.escalate_to_agent as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_agent: e.target.value })} placeholder="agent_id" className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white" />
                </div>
              </>
            )}

            {selectedNode.data.type === "parallel" && (
              <div>
                <label className="block text-xs text-gray-500 mb-1">Branches (comma-separated node IDs)</label>
                <input
                  value={((nodeConfig.branches as string[]) || []).join(", ")}
                  onChange={(e) => updateNodeConfig({ ...nodeConfig, branches: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })}
                  placeholder="branch_1, branch_2"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
                />
              </div>
            )}

            <div>
              <label className="block text-xs text-gray-500 mb-1">Description (optional)</label>
              <textarea
                value={(nodeConfig.description as string) || ""}
                onChange={(e) => updateNodeConfig({ ...nodeConfig, description: e.target.value })}
                placeholder="What does this node do?"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white h-16"
              />
            </div>

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
