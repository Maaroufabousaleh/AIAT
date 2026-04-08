"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import { WORKFLOW_STATES, STATE_COLORS, TERMINAL_STATES, type WorkflowState } from "@/lib/constants";
import { formatDistanceToNow, format } from "date-fns";
import { ArrowLeft, RefreshCw, CheckCircle, XCircle, RotateCcw, Archive, Play, Pause, StopCircle, GitBranch, ArrowRightCircle } from "lucide-react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Node,
  Edge,
  NodeTypes,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { Flow, FlowInstance, FlowNodeExecution, FlowDefinition, FlowNodeType, FlowInstanceStatus } from "@/lib/flow-types";
import { NODE_TYPE_LABELS, FLOW_NODE_COLORS, FLOW_STATUS_COLORS } from "@/lib/flow-types";

interface StateHistoryEntry {
  state: WorkflowState;
  entered_at: string;
  transitioned_by?: string;
}

interface Decision {
  id: string;
  decision_type: string;
  prompt: string;
  context?: unknown;
  created_at: string;
}

interface Project {
  id: string;
  name: string;
  description?: string;
  state: WorkflowState;
  created_at: string;
  updated_at: string;
}

const flowNodeTypes: NodeTypes = {};

function FlowNodeComponent({ data, selected }: { data: { label: string; type: FlowNodeType; status?: string }; selected?: boolean }) {
  const statusColors: Record<string, string> = {
    RUNNING: "ring-2 ring-blue-500",
    COMPLETED: "ring-2 ring-green-500",
    FAILED: "ring-2 ring-red-500",
  };
  
  return (
    <div className={clsx(
      "px-3 py-2 rounded-lg border-2 min-w-[100px] text-center",
      selected ? "border-blue-500" : "border-gray-600",
      FLOW_NODE_COLORS[data.type as FlowNodeType] || "bg-gray-600",
      statusColors[data.status || ""] || ""
    )}>
      <Handle type="target" position={Position.Top} className="!bg-gray-400" />
      <div className="text-sm font-medium text-white">{data.label}</div>
      <div className="text-xs text-white/70">{NODE_TYPE_LABELS[data.type as FlowNodeType]}</div>
      {data.status && data.status !== "RUNNING" && (
        <div className={clsx("text-xxs mt-1", data.status === "COMPLETED" ? "text-green-300" : "text-red-300")}>
          {data.status}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-gray-400" />
    </div>
  );
}

flowNodeTypes.flowNode = FlowNodeComponent;

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [history, setHistory] = useState<StateHistoryEntry[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [allowedTransitions, setAllowedTransitions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [expandedDecision, setExpandedDecision] = useState<string | null>(null);
  
  const [activeTab, setActiveTab] = useState<"workflow" | "flow">("workflow");
  const [flowInstance, setFlowInstance] = useState<FlowInstance | null>(null);
  const [flowDefinition, setFlowDefinition] = useState<FlowDefinition | null>(null);
  const [nodeExecutions, setNodeExecutions] = useState<FlowNodeExecution[]>([]);
  const [flowLoading, setFlowLoading] = useState(false);
  const [showFlowSwitch, setShowFlowSwitch] = useState(false);
  const [availableFlows, setAvailableFlows] = useState<Flow[]>([]);
  const [nodes, setNodes, onNodesChange] = useNodesState([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([] as Edge[]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [proj, hist, dec, trans] = await Promise.allSettled([
        fetch(`/api/projects/${id}`).then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/projects/${id}/state-history`).then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch(`/api/projects/${id}/decisions`).then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch(`/api/projects/${id}/transition`).then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      if (proj.status === "fulfilled" && proj.value) setProject(proj.value);
      if (hist.status === "fulfilled") setHistory(Array.isArray(hist.value) ? hist.value : hist.value?.history ?? []);
      if (dec.status === "fulfilled") setDecisions(Array.isArray(dec.value) ? dec.value : dec.value?.decisions ?? []);
      if (trans.status === "fulfilled") setAllowedTransitions(Array.isArray(trans.value) ? trans.value : trans.value?.transitions ?? []);
    } catch {
    } finally {
      setLoading(false);
    }
  }, [id]);

  const loadFlowData = useCallback(async () => {
    setFlowLoading(true);
    try {
      const instanceRes = await fetch(`/api/projects/${id}/flow-instance`);
      if (instanceRes.ok) {
        const instance = await instanceRes.json();
        setFlowInstance(instance);
        
        const [flowRes, execsRes] = await Promise.all([
          fetch(`/api/flows/${instance.flow_id}`),
          fetch(`/api/flows/instances/${instance.id}/executions`),
        ]);
        
        if (flowRes.ok) {
          const flow = await flowRes.json();
          setFlowDefinition(flow.definition_json);
          
          let executions: FlowNodeExecution[] = [];
          if (execsRes.ok) {
            const execs = await execsRes.json();
            executions = Array.isArray(execs) ? execs : [];
          }
          
          const { nodes: flowNodes, edges: flowEdges } = convertToReactFlow(
            flow.definition_json?.nodes || [],
            flow.definition_json?.edges || [],
            instance.active_node_ids || [],
            executions
          );
          setNodes(flowNodes);
          setEdges(flowEdges);
        }
        
        if (execsRes.ok) {
          const execs = await execsRes.json();
          setNodeExecutions(Array.isArray(execs) ? execs : []);
        }
      } else {
        setFlowInstance(null);
        setFlowDefinition(null);
        setNodeExecutions([]);
        setNodes([]);
        setEdges([]);
        
        const flowsRes = await fetch('/api/flows?is_active=true');
        if (flowsRes.ok) {
          const flows = await flowsRes.json();
          setAvailableFlows(flows || []);
        }
      }
    } catch {
      setFlowInstance(null);
    } finally {
      setFlowLoading(false);
    }
  }, [id, setNodes, setEdges]);

  useEffect(() => { load(); }, [load]);
  
  useEffect(() => {
    if (activeTab === "flow") {
      loadFlowData();
    }
  }, [activeTab, loadFlowData]);

  useEffect(() => {
    if (!project || TERMINAL_STATES.includes(project.state)) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [project, load]);

  async function handleDecision(decisionId: string, approved: boolean) {
    setActionLoading(decisionId);
    try {
      await fetch(`/api/projects/${id}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision_id: decisionId, approved }),
      });
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleAction(action: "retry" | "archive") {
    setActionLoading(action);
    try {
      await fetch(`/api/projects/${id}/${action}`, { method: "POST" });
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleTransition(event: string) {
    setActionLoading(event);
    try {
      await fetch(`/api/projects/${id}/transition`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event }),
      });
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleFlowAction(action: string) {
    if (!flowInstance) return;
    setActionLoading(action);
    try {
      await fetch(`/api/flows/instances/${flowInstance.id}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      await loadFlowData();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleNodeAction(nodeId: string, action: string, approved?: boolean) {
    if (!flowInstance) return;
    setActionLoading(`node-${nodeId}`);
    try {
      await fetch(`/api/flows/instances/${flowInstance.id}/node-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId, action, approved }),
      });
      await loadFlowData();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleSwitchFlow(newFlowId: string) {
    if (!flowInstance) return;
    setActionLoading("switch-flow");
    try {
      const res = await fetch(`/api/flows/instances/${flowInstance.id}/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flow_id: newFlowId, preserve_context: true }),
      });
      if (res.ok) {
        await loadFlowData();
        setShowFlowSwitch(false);
      } else {
        const d = await res.json();
        setFlowError(d.error || "Failed to switch flow");
      }
    } finally {
      setActionLoading(null);
    }
  }

  async function handleRetry() {
    if (!flowInstance) return;
    setActionLoading("retry");
    try {
      const res = await fetch(`/api/flows/instances/${flowInstance.id}/retry`, { method: "POST" });
      if (res.ok) {
        await loadFlowData();
      } else {
        const d = await res.json();
        setFlowError(d.error || "Failed to retry flow");
      }
    } finally {
      setActionLoading(null);
    }
  }

  async function openFlowSwitchModal() {
    setActionLoading("load-flows");
    try {
      const res = await fetch('/api/flows?is_active=true');
      if (res.ok) {
        const flows = await res.json();
        setAvailableFlows(flows || []);
      }
    } catch {
      setAvailableFlows([]);
    } finally {
      setActionLoading(null);
    }
    setShowFlowSwitch(true);
  }

  async function handleAssignFlow(flowId: string) {
    setActionLoading("assign-flow");
    try {
      const res = await fetch(`/api/projects/${id}/flow-instance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flow_id: flowId }),
      });
      if (res.ok) {
        await loadFlowData();
      } else {
        const d = await res.json();
        setFlowError(d.error || "Failed to assign flow");
      }
    } catch {
      setFlowError("Failed to assign flow");
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center h-full">
        <div className="text-gray-500">Loading...</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="p-6">
        <div className="text-red-400">Project not found</div>
        <Link href="/projects" className="text-blue-400 text-sm mt-2 inline-block">← Back to projects</Link>
      </div>
    );
  }

  const isFailed = project.state === "FAILED";
  const isTerminal = TERMINAL_STATES.includes(project.state);

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-start gap-4">
        <Link href="/projects" className="mt-1 p-1 rounded text-gray-500 hover:text-gray-300">
          <ArrowLeft size={16} />
        </Link>
        <div className="flex-1">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold text-white">{project.name}</h1>
            <span className={clsx(
              "px-2 py-0.5 rounded-full text-xs font-medium text-white",
              STATE_COLORS[project.state] ?? "bg-gray-600"
            )}>
              {project.state?.replace(/_/g, " ")}
            </span>
          </div>
          {project.description && <p className="text-sm text-gray-500 mt-1">{project.description}</p>}
          <p className="text-xs text-gray-600 mt-1">
            ID: {project.id} · Created {formatDistanceToNow(new Date(project.created_at), { addSuffix: true })}
          </p>
        </div>
        <button onClick={activeTab === "flow" ? loadFlowData : load} className="p-2 text-gray-500 hover:text-gray-300">
          <RefreshCw size={14} className={loading || flowLoading ? "animate-spin" : ""} />
        </button>
      </div>

      <div className="flex gap-1 border-b border-gray-800">
        <button
          onClick={() => setActiveTab("workflow")}
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
            activeTab === "workflow"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-gray-400 hover:text-gray-200"
          )}
        >
          Workflow
        </button>
        <button
          onClick={() => setActiveTab("flow")}
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "flow"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-gray-400 hover:text-gray-200"
          )}
        >
          <GitBranch size={14} />
          Flow
          {flowInstance && (
            <span className={clsx(
              "px-1.5 py-0.5 rounded text-xxs",
              FLOW_STATUS_COLORS[flowInstance.status as FlowInstanceStatus]
            )}>
              {flowInstance.status}
            </span>
          )}
        </button>
      </div>

      {activeTab === "workflow" && (
        <>
          <div className="flex flex-wrap gap-2">
            {allowedTransitions.map((event) => (
              <button
                key={event}
                onClick={() => handleTransition(event)}
                disabled={!!actionLoading}
                className="px-3 py-1.5 text-xs font-medium bg-blue-600/20 text-blue-400 border border-blue-700 hover:bg-blue-600/40 rounded-lg disabled:opacity-50"
              >
                {actionLoading === event ? "..." : event.replace(/_/g, " ")}
              </button>
            ))}
            {isFailed && (
              <>
                <button onClick={() => handleAction("retry")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-600/20 text-amber-400 border border-amber-700 rounded-lg disabled:opacity-50">
                  <RotateCcw size={12} /> {actionLoading === "retry" ? "..." : "Retry"}
                </button>
                <button onClick={() => handleAction("archive")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gray-600/20 text-gray-400 border border-gray-700 rounded-lg disabled:opacity-50">
                  <Archive size={12} /> {actionLoading === "archive" ? "..." : "Archive"}
                </button>
              </>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <h2 className="text-sm font-medium text-gray-300 mb-4">State History</h2>
              <div className="space-y-0">
                {WORKFLOW_STATES.map((state, i) => {
                  const entry = history.find((h) => h.state === state);
                  const isCurrent = project.state === state;
                  const isPast = entry !== undefined;
                  return (
                    <div key={state} className="flex gap-3 group">
                      <div className="flex flex-col items-center">
                        <div className={clsx("w-3 h-3 rounded-full flex-shrink-0 mt-0.5", isCurrent ? STATE_COLORS[state] : isPast ? "bg-gray-600" : "bg-gray-800 border border-gray-700")} />
                        {i < WORKFLOW_STATES.length - 1 && <div className={clsx("w-px flex-1 my-0.5", isPast ? "bg-gray-700" : "bg-gray-800")} />}
                      </div>
                      <div className="pb-3">
                        <div className={clsx("text-xs font-medium", isCurrent ? "text-white" : isPast ? "text-gray-400" : "text-gray-700")}>{state.replace(/_/g, " ")}</div>
                        {entry && <div className="text-xxs text-gray-600">{format(new Date(entry.entered_at), "MMM d HH:mm:ss")}{entry.transitioned_by && ` · ${entry.transitioned_by}`}</div>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="space-y-4">
              {decisions.length > 0 && (
                <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-amber-300 mb-3">Pending Decisions ({decisions.length})</h2>
                  <div className="space-y-3">
                    {decisions.map((d) => (
                      <div key={d.id} className="bg-gray-900 rounded-lg p-3 border border-gray-800">
                        <div className="text-xs font-medium text-gray-200 mb-1">{d.decision_type}</div>
                        <p className="text-xs text-gray-400 mb-2">{d.prompt}</p>
                        {Boolean(d.context) && <button onClick={() => setExpandedDecision(expandedDecision === d.id ? null : d.id)} className="text-xxs text-blue-400 mb-2">{expandedDecision === d.id ? "Hide context" : "Show context"}</button>}
                        {expandedDecision === d.id && <pre className="text-xxs text-gray-500 bg-gray-950 rounded p-2 overflow-x-auto mb-2">{JSON.stringify(d.context, null, 2)}</pre>}
                        <div className="flex gap-2">
                          <button onClick={() => handleDecision(d.id, true)} disabled={actionLoading === d.id} className="flex items-center gap-1 px-2 py-1 bg-green-600/20 text-green-400 border border-green-800 text-xs rounded disabled:opacity-50"><CheckCircle size={11} />{actionLoading === d.id ? "..." : "Approve"}</button>
                          <button onClick={() => handleDecision(d.id, false)} disabled={actionLoading === d.id} className="flex items-center gap-1 px-2 py-1 bg-red-600/20 text-red-400 border border-red-800 text-xs rounded disabled:opacity-50"><XCircle size={11} />Reject</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Monitor</h2>
                <div className="space-y-2">
                  <Link href="/ceo" className="flex items-center gap-2 text-xs text-blue-400">→ CEO Live Feed</Link>
                  <Link href="/streams" className="flex items-center gap-2 text-xs text-blue-400">→ All Agent Streams</Link>
                  <Link href="/logs" className="flex items-center gap-2 text-xs text-blue-400">→ Container Logs</Link>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {activeTab === "flow" && (
        <div className="space-y-4">
          {!flowInstance ? (
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
              <div className="text-center">
                <GitBranch size={32} className="mx-auto text-gray-600 mb-3" />
                <p className="text-gray-400 text-sm">No flow attached to this project.</p>
                <p className="text-gray-500 text-xs mt-1 mb-4">Select a flow below to start orchestrating this project.</p>
              </div>
              {actionLoading === "load-flows" ? (
                <div className="text-sm text-gray-500 py-4 text-center">Loading flows...</div>
              ) : (
                <div className="space-y-2 mt-4">
                  {availableFlows.length > 0 ? (
                    availableFlows.map((flow) => (
                      <button
                        key={flow.id}
                        onClick={() => handleAssignFlow(flow.id)}
                        disabled={actionLoading === "assign-flow"}
                        className="w-full text-left px-4 py-3 rounded-lg bg-gray-800 hover:bg-gray-700 border border-gray-700 transition-colors"
                      >
                        <div className="text-gray-100 font-medium text-sm">{flow.name}</div>
                        <div className="text-xs text-gray-500 mt-0.5">v{flow.version} · {flow.description || "No description"}</div>
                      </button>
                    ))
                  ) : (
                    <div className="text-sm text-gray-500 py-4 text-center">No active flows available. <Link href="/flows" className="text-blue-400">Create one first →</Link></div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className={clsx("px-2 py-1 rounded text-xs font-medium", FLOW_STATUS_COLORS[flowInstance.status as FlowInstanceStatus])}>
                    {flowInstance.status}
                  </span>
                  <span className="text-xs text-gray-500">v{flowInstance.flow_version}</span>
                </div>
                <div className="flex gap-2">
                  {flowInstance.status === "NOT_STARTED" && (
                    <button onClick={() => handleFlowAction("start")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg">
                      <Play size={12} /> Start
                    </button>
                  )}
                  {flowInstance.status === "RUNNING" && (
                    <>
                      <button onClick={() => handleFlowAction("pause")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-yellow-600/20 text-yellow-400 border border-yellow-800 rounded-lg">
                        <Pause size={12} /> Pause
                      </button>
                      <button onClick={() => handleFlowAction("cancel")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-red-600/20 text-red-400 border border-red-800 rounded-lg">
                        <StopCircle size={12} /> Cancel
                      </button>
                    </>
                  )}
                  {flowInstance.status === "PAUSED" && (
                    <>
                      <button onClick={() => handleFlowAction("resume")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg">
                        <Play size={12} /> Resume
                      </button>
                      <button onClick={() => handleFlowAction("cancel")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-red-600/20 text-red-400 border border-red-800 rounded-lg">
                        <StopCircle size={12} /> Cancel
                      </button>
                    </>
                  )}
                  {flowInstance.status === "WAITING_APPROVAL" && (
                    <button onClick={() => handleFlowAction("resume")} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg">
                      <Play size={12} /> Resume
                    </button>
                  )}
                  {(flowInstance.status === "FAILED" || flowInstance.status === "CANCELLED") && (
                    <button onClick={handleRetry} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-600/20 text-amber-400 border border-amber-800 rounded-lg">
                      <RotateCcw size={12} /> Retry
                    </button>
                  )}
                  <button onClick={openFlowSwitchModal} disabled={!!actionLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-purple-600/20 text-purple-400 border border-purple-800 rounded-lg">
                    <ArrowRightCircle size={12} /> Switch Flow
                  </button>
                </div>
              </div>

              <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden" style={{ height: "400px" }}>
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={flowNodeTypes}
                  fitView
                  className="bg-gray-950"
                >
                  <Background color="#374151" gap={16} />
                  <Controls className="!bg-gray-800 !border-gray-700" />
                </ReactFlow>
              </div>

              {flowInstance.status === "RUNNING" && flowInstance.active_node_ids?.length > 0 && (
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-gray-300 mb-3">Active Nodes</h2>
                  <div className="space-y-2">
                    {flowInstance.active_node_ids.map((nodeId) => {
                      const nodeExec = nodeExecutions.find(e => e.node_id === nodeId && e.status === "RUNNING");
                      return (
                        <div key={nodeId} className="flex items-center justify-between bg-gray-800 rounded-lg p-3">
                          <div>
                            <div className="text-sm font-medium text-white">{nodeId}</div>
                            {nodeExec && <div className="text-xs text-gray-500 mt-0.5">Running...</div>}
                          </div>
                          <div className="flex gap-2">
                            <button onClick={() => handleNodeAction(nodeId, "complete", true)} disabled={!!actionLoading} className="px-2 py-1 text-xs bg-green-600/20 text-green-400 border border-green-800 rounded hover:bg-green-600/40">
                              Complete
                            </button>
                            <button onClick={() => handleNodeAction(nodeId, "fail")} disabled={!!actionLoading} className="px-2 py-1 text-xs bg-red-600/20 text-red-400 border border-red-800 rounded hover:bg-red-600/40">
                              Fail
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {nodeExecutions.length > 0 && (
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-gray-300 mb-3">Execution History</h2>
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {nodeExecutions.map((exec, i) => (
                      <div key={exec.id || i} className="flex items-center gap-3 text-xs">
                        <div className={clsx("w-2 h-2 rounded-full", exec.status === "COMPLETED" ? "bg-green-500" : exec.status === "FAILED" ? "bg-red-500" : "bg-gray-600")} />
                        <div className="flex-1 text-gray-300">{exec.node_label || exec.node_id}</div>
                        <div className={clsx("px-1.5 py-0.5 rounded", exec.status === "COMPLETED" ? "bg-green-900/50 text-green-400" : exec.status === "FAILED" ? "bg-red-900/50 text-red-400" : "bg-gray-800 text-gray-400")}>
                          {exec.status}
                        </div>
                        <div className="text-gray-500">
                          {exec.completed_at ? format(new Date(exec.completed_at), "HH:mm:ss") : format(new Date(exec.started_at), "HH:mm:ss")}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {showFlowSwitch && flowInstance && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-semibold text-white mb-4">Switch Flow</h2>
            <p className="text-sm text-gray-400 mb-4">Select a new flow to replace the current one.</p>
            {actionLoading === "load-flows" ? (
              <div className="text-sm text-gray-500 py-4 text-center">Loading flows...</div>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {availableFlows.filter(f => f.id !== flowInstance.flow_id).map((flow) => (
                  <button
                    key={flow.id}
                    onClick={() => handleSwitchFlow(flow.id)}
                    disabled={actionLoading === "switch-flow"}
                    className="w-full text-left px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm"
                  >
                    <div className="text-gray-100 font-medium">{flow.name}</div>
                    <div className="text-xs text-gray-500">v{flow.version}</div>
                  </button>
                ))}
                {availableFlows.filter(f => f.id !== flowInstance.flow_id).length === 0 && (
                  <div className="text-sm text-gray-500 py-4 text-center">No other active flows available</div>
                )}
              </div>
            )}
            <button
              onClick={() => setShowFlowSwitch(false)}
              className="mt-4 w-full px-3 py-2 border border-gray-700 rounded-lg text-sm text-gray-400 hover:text-gray-100"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function convertToReactFlow(
  nodes: { id: string; type: string; label: string; config: Record<string, unknown>; position?: { x: number; y: number } }[],
  edges: { id: string; source: string; target: string; condition?: string }[],
  activeNodeIds: string[],
  executions: FlowNodeExecution[]
): { nodes: Node[]; edges: Edge[] } {
  const nodeStatus: Record<string, string> = {};
  executions.forEach(e => { nodeStatus[e.node_id] = e.status; });
  
  const flowNodes: Node[] = nodes.map((n) => ({
    id: n.id,
    type: "flowNode",
    position: n.position || { x: Math.random() * 400, y: Math.random() * 400 },
    data: { label: n.label, type: n.type, status: activeNodeIds.includes(n.id) ? "RUNNING" : nodeStatus[n.id] },
  }));

  const flowEdges: Edge[] = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.condition,
    style: { stroke: "#6b7280" },
  }));

  return { nodes: flowNodes, edges: flowEdges };
}
