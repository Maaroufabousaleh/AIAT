"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import { WORKFLOW_STATES, STATE_COLORS, TERMINAL_STATES, type WorkflowState } from "@/lib/constants";
import { formatDistanceToNow, format } from "date-fns";
import { ArrowLeft, RefreshCw, CheckCircle, XCircle, RotateCcw, Archive, Play, Pause, StopCircle, GitBranch, ArrowRightCircle, FileText, Upload, Trash2, Plus, Link as LinkIcon } from "lucide-react";
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
  from_state?: WorkflowState;
  to_state: WorkflowState;
  event?: string;
  transitioned_at: string;
  triggered_by?: string;
  payload?: Record<string, unknown>;
}

interface Decision {
  id: string;
  decision_type?: string;
  gate_type?: string;
  prompt?: string;
  context?: unknown;
  created_at: string;
}

interface ContextItem {
  id: string;
  project_id: string;
  item_type: "FILE" | "URL" | "TEXT" | "DOCUMENT";
  name: string;
  description?: string;
  mime_type?: string;
  size_bytes?: number;
  blob_bucket?: string;
  blob_key?: string;
  url?: string;
  content_text?: string;
  metadata?: Record<string, unknown>;
  tags?: string[];
  created_by: string;
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

interface WorkspaceSummary {
  next_actions: Array<{ kind: string; label: string; severity: string }>;
  pending_approvals: Decision[];
  recent_activity: Array<{ event_type: string; occurred_at?: string; summary: string; actor?: string }>;
  worker_activity: Array<{ task_id?: string; agent_id?: string; team_id?: string; status?: string; updated_at?: string }>;
  artifacts: Array<{ id?: number; path: string; agent_id?: string; size_bytes?: number; created_at?: string }>;
  logs: Array<{ id?: string; level?: string; message?: string; created_at?: string }>;
  cost_usage: { available: boolean; reason?: string; total_cost_usd?: number; tool_calls?: number; llm_calls?: number };
  flow_instance?: FlowInstance | null;
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
  
  const [activeTab, setActiveTab] = useState<"workspace" | "workflow" | "flow" | "context">("workspace");
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [flowInstance, setFlowInstance] = useState<FlowInstance | null>(null);
  const [flowDefinition, setFlowDefinition] = useState<FlowDefinition | null>(null);
  const [nodeExecutions, setNodeExecutions] = useState<FlowNodeExecution[]>([]);
  const [flowLoading, setFlowLoading] = useState(false);
  const [showFlowSwitch, setShowFlowSwitch] = useState(false);
  const [availableFlows, setAvailableFlows] = useState<Flow[]>([]);
  const [flowError, setFlowError] = useState<string | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([] as Edge[]);
  const [contextItems, setContextItems] = useState<ContextItem[]>([]);
  const [contextLoading, setContextLoading] = useState(false);
  const [showContextUpload, setShowContextUpload] = useState(false);
  const [newContextName, setNewContextName] = useState("");
  const [newContextType, setNewContextType] = useState<"FILE" | "URL" | "TEXT">("FILE");
  const [newContextUrl, setNewContextUrl] = useState("");
  const [newContextText, setNewContextText] = useState("");
  const [newContextTags, setNewContextTags] = useState("");
  const [overrideNodeId, setOverrideNodeId] = useState("");
  const [overrideReason, setOverrideReason] = useState("");

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
        let executions: FlowNodeExecution[] = [];
        if (execsRes.ok) {
          const execs = await execsRes.json();
          executions = Array.isArray(execs) ? execs : [];
          setNodeExecutions(executions);
        } else {
          setNodeExecutions([]);
        }
        
        if (flowRes.ok) {
          const flow = await flowRes.json();
          setFlowDefinition(flow.definition_json);
          setOverrideNodeId(instance.active_node_ids?.[0] || flow.definition_json?.nodes?.[0]?.id || "");
          
          const { nodes: flowNodes, edges: flowEdges } = convertToReactFlow(
            flow.definition_json?.nodes || [],
            flow.definition_json?.edges || [],
            instance.active_node_ids || [],
            executions
          );
          setNodes(flowNodes);
          setEdges(flowEdges);
        }
      } else {
        setFlowInstance(null);
        setFlowDefinition(null);
        setNodeExecutions([]);
        setNodes([]);
        setEdges([]);
        setOverrideNodeId("");
        
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

  const loadContextData = useCallback(async () => {
    setContextLoading(true);
    try {
      const res = await fetch(`/api/projects/${id}/context`);
      if (res.ok) {
        const data = await res.json();
        setContextItems(Array.isArray(data) ? data : []);
      } else {
        setContextItems([]);
      }
    } catch {
      setContextItems([]);
    } finally {
      setContextLoading(false);
    }
  }, [id]);

  const loadWorkspace = useCallback(async () => {
    setWorkspaceLoading(true);
    try {
      const res = await fetch(`/api/projects/${id}/workspace`);
      if (res.ok) {
        setWorkspace(await res.json());
      } else {
        setWorkspace(null);
      }
    } catch {
      setWorkspace(null);
    } finally {
      setWorkspaceLoading(false);
    }
  }, [id]);

  const completedNodeIds = useMemo(
    () => nodeExecutions.filter((execution) => execution.status === "COMPLETED").map((execution) => execution.node_id),
    [nodeExecutions]
  );

  const nextPossibleTransitions = useMemo(() => {
    if (!flowDefinition || !flowInstance) return [];
    const completed = new Set(completedNodeIds);

    return (flowInstance.active_node_ids || []).flatMap((nodeId) =>
      (flowDefinition.edges || [])
        .filter((edge) => edge.source === nodeId && !completed.has(edge.target))
        .map((edge) => {
          const targetNode = flowDefinition.nodes.find((node) => node.id === edge.target);
          return {
            edgeId: edge.id,
            condition: edge.condition,
            sourceId: nodeId,
            targetId: edge.target,
            targetLabel: targetNode?.label || edge.target,
          };
        })
    );
  }, [completedNodeIds, flowDefinition, flowInstance]);

  const overrideableNodes = useMemo(() => flowDefinition?.nodes || [], [flowDefinition]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (activeTab === "workspace") {
      loadWorkspace();
    }
  }, [activeTab, loadWorkspace]);
  
  useEffect(() => {
    if (activeTab === "flow") {
      loadFlowData();
    }
  }, [activeTab, loadFlowData]);

  useEffect(() => {
    if (activeTab === "context") {
      loadContextData();
    }
  }, [activeTab, loadContextData]);

  useEffect(() => {
    if (!project || TERMINAL_STATES.includes(project.state)) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [project, load]);

  async function handleDecision(decisionId: string, decision: "APPROVED" | "REJECTED" | "EDITS") {
    const loadingKey = `${decisionId}-${decision}`;
    setActionLoading(loadingKey);
    try {
      await fetch(`/api/projects/${id}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision,
          decided_by: "operator",
          comments: `Submitted from project workspace: ${decision.toLowerCase()}`,
        }),
      });
      await Promise.all([load(), loadWorkspace()]);
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

  async function handleNodeAction(
    nodeId: string,
    action: string,
    options?: { approved?: boolean; decision?: string; error?: string }
  ) {
    if (!flowInstance) return;
    setActionLoading(`node-${nodeId}`);
    try {
      const res = await fetch(`/api/flows/instances/${flowInstance.id}/node-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId, action, ...options }),
      });
      if (res.ok) {
        await Promise.all([loadFlowData(), load()]);
      } else {
        const data = await res.json().catch(() => ({}));
        setFlowError(data.error || `Failed to ${action} node`);
      }
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

  async function handleOverrideFlowNode() {
    if (!flowInstance || !overrideNodeId) return;
    setActionLoading("override-flow-node");
    setFlowError(null);
    try {
      const res = await fetch(`/api/flows/instances/${flowInstance.id}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_node_id: overrideNodeId,
          actor_id: "human",
          actor_role: "human_operator",
          reason: overrideReason || undefined,
        }),
      });
      if (res.ok) {
        await Promise.all([loadFlowData(), load()]);
        setOverrideReason("");
      } else {
        const data = await res.json().catch(() => ({}));
        setFlowError(data.error || "Failed to override flow node");
      }
    } finally {
      setActionLoading(null);
    }
  }

  async function handleAddContextItem() {
    if (!newContextName.trim()) return;
    setActionLoading("add-context");
    try {
      const body: Record<string, unknown> = {
        item_type: newContextType,
        name: newContextName,
        tags: newContextTags.split(",").map(t => t.trim()).filter(Boolean),
      };
      if (newContextType === "URL" && newContextUrl) {
        body.url = newContextUrl;
      } else if (newContextType === "TEXT" && newContextText) {
        body.content_text = newContextText;
      }
      const res = await fetch(`/api/projects/${id}/context`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        await loadContextData();
        setNewContextName("");
        setNewContextUrl("");
        setNewContextText("");
        setNewContextTags("");
        setShowContextUpload(false);
      }
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDeleteContextItem(itemId: string) {
    if (!confirm("Delete this context item?")) return;
    setActionLoading(`delete-${itemId}`);
    try {
      const res = await fetch(`/api/projects/${id}/context/${itemId}`, { method: "DELETE" });
      if (res.ok) {
        await loadContextData();
      }
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
        <button
          onClick={activeTab === "flow" ? loadFlowData : activeTab === "workspace" ? loadWorkspace : load}
          className="p-2 text-gray-500 hover:text-gray-300"
        >
          <RefreshCw size={14} className={loading || flowLoading || workspaceLoading ? "animate-spin" : ""} />
        </button>
      </div>

      <div className="flex gap-1 border-b border-gray-800">
        <button
          onClick={() => setActiveTab("workspace")}
          data-testid="project-tab-workspace"
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
            activeTab === "workspace"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-gray-400 hover:text-gray-200"
          )}
        >
          Workspace
        </button>
        <button
          onClick={() => setActiveTab("workflow")}
          data-testid="project-tab-workflow"
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
          data-testid="project-tab-flow"
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
        <button
          onClick={() => setActiveTab("context")}
          data-testid="project-tab-context"
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "context"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-gray-400 hover:text-gray-200"
          )}
        >
          <FileText size={14} />
          Context
          {contextItems.length > 0 && (
            <span className="px-1.5 py-0.5 rounded text-xxs bg-gray-600">
              {contextItems.length}
            </span>
          )}
        </button>
      </div>

      {activeTab === "workspace" && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
            <div className="space-y-4">
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Next Operator Action</h2>
                <div className="space-y-2">
                  {(workspace?.next_actions ?? [{ kind: "loading", label: workspaceLoading ? "Loading workspace..." : "Workspace summary unavailable", severity: "medium" }]).map((action, index) => (
                    <div key={`${action.kind}-${index}`} className={clsx(
                      "rounded-lg border px-3 py-2 text-sm",
                      action.severity === "high" ? "border-amber-800 bg-amber-950/40 text-amber-200" :
                      action.severity === "medium" ? "border-blue-800 bg-blue-950/30 text-blue-200" :
                      "border-gray-800 bg-gray-950 text-gray-300"
                    )}>
                      {action.label}
                    </div>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Approvals</div>
                  <div className="text-xl font-semibold text-white">{workspace?.pending_approvals?.length ?? 0}</div>
                  <div className="text-xs text-gray-600 mt-1">pending</div>
                </div>
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Artifacts</div>
                  <div className="text-xl font-semibold text-white">{workspace?.artifacts?.length ?? 0}</div>
                  <div className="text-xs text-gray-600 mt-1">project-scoped</div>
                </div>
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Flow</div>
                  <div className="text-sm font-semibold text-white">{workspace?.flow_instance?.status ?? flowInstance?.status ?? "not attached"}</div>
                  <div className="text-xs text-gray-600 mt-1">active instance</div>
                </div>
              </div>

              {(workspace?.pending_approvals ?? []).length > 0 && (
                <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-amber-300 mb-3">Workspace Approvals</h2>
                  <div className="space-y-3">
                    {workspace!.pending_approvals.map((approval, index) => (
                      <div key={approval.id} className="rounded-lg bg-gray-950 border border-amber-900/60 p-3">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="text-xs font-medium text-gray-200">{approval.gate_type ?? approval.decision_type ?? "approval"}</div>
                            <div className="text-xxs text-gray-500 mt-1">
                              {approval.created_at ? format(new Date(approval.created_at), "MMM d HH:mm") : "created time unknown"}
                              {index === 0 ? " · next decision" : " · queued"}
                            </div>
                          </div>
                          {index === 0 && (
                            <div className="flex flex-wrap gap-2 justify-end">
                              <button
                                onClick={() => handleDecision(approval.id, "APPROVED")}
                                disabled={!!actionLoading}
                                className="flex items-center gap-1 px-2 py-1 bg-green-600/20 text-green-400 border border-green-800 text-xs rounded disabled:opacity-50"
                              >
                                <CheckCircle size={11} />{actionLoading === `${approval.id}-APPROVED` ? "..." : "Approve"}
                              </button>
                              <button
                                onClick={() => handleDecision(approval.id, "EDITS")}
                                disabled={!!actionLoading}
                                className="flex items-center gap-1 px-2 py-1 bg-blue-600/20 text-blue-400 border border-blue-800 text-xs rounded disabled:opacity-50"
                              >
                                <FileText size={11} />Edits
                              </button>
                              <button
                                onClick={() => handleDecision(approval.id, "REJECTED")}
                                disabled={!!actionLoading}
                                className="flex items-center gap-1 px-2 py-1 bg-red-600/20 text-red-400 border border-red-800 text-xs rounded disabled:opacity-50"
                              >
                                <XCircle size={11} />Reject
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Audit Timeline</h2>
                <div className="space-y-2 max-h-72 overflow-y-auto">
                  {(workspace?.recent_activity ?? []).length === 0 ? (
                    <div className="text-xs text-gray-500">No timeline events available.</div>
                  ) : workspace!.recent_activity.map((event, index) => (
                    <div key={`${event.event_type}-${index}`} className="rounded-lg bg-gray-950 border border-gray-800 p-3">
                      <div className="flex items-center justify-between gap-3 text-xs">
                        <div className="text-gray-200">{event.summary}</div>
                        <div className="text-gray-500">{event.occurred_at ? format(new Date(event.occurred_at), "MMM d HH:mm") : "unknown"}</div>
                      </div>
                      <div className="text-xxs text-gray-500 mt-1">
                        {event.event_type.replace(/_/g, " ")}{event.actor ? ` · ${event.actor}` : ""}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Artifacts</h2>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {(workspace?.artifacts ?? []).length === 0 ? (
                    <div className="text-xs text-gray-500">No artifacts registered for this project.</div>
                  ) : workspace!.artifacts.map((artifact) => (
                    <div key={`${artifact.id}-${artifact.path}`} className="rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs">
                      <div className="text-gray-200 break-all">{artifact.path}</div>
                      <div className="text-gray-500 mt-1">{artifact.agent_id || "unknown agent"}{artifact.size_bytes ? ` · ${artifact.size_bytes} bytes` : ""}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Worker Activity</h2>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {(workspace?.worker_activity ?? []).length === 0 ? (
                    <div className="text-xs text-gray-500">No project-scoped worker activity yet.</div>
                  ) : workspace!.worker_activity.map((task, index) => (
                    <div key={`${task.task_id}-${index}`} className="rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs">
                      <div className="text-gray-200">{task.agent_id || "agent"}</div>
                      <div className="text-gray-500 mt-1">{task.team_id || "team"} · {task.status || "unknown"}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Project Logs</h2>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {(workspace?.logs ?? []).length === 0 ? (
                    <div className="text-xs text-gray-500">Project-scoped log filtering is not available from the current container log source.</div>
                  ) : workspace!.logs.map((log, index) => (
                    <div key={`${log.id ?? log.created_at ?? "log"}-${index}`} className="rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs">
                      <div className="text-gray-200">{log.message ?? "log entry"}</div>
                      <div className="text-gray-500 mt-1">{log.level ?? "info"}{log.created_at ? ` · ${format(new Date(log.created_at), "MMM d HH:mm")}` : ""}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Cost And Usage</h2>
                {workspace?.cost_usage?.available ? (
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div><div className="text-sm text-white">${workspace.cost_usage.total_cost_usd ?? 0}</div><div className="text-xxs text-gray-500">cost</div></div>
                    <div><div className="text-sm text-white">{workspace.cost_usage.llm_calls ?? 0}</div><div className="text-xxs text-gray-500">LLM</div></div>
                    <div><div className="text-sm text-white">{workspace.cost_usage.tool_calls ?? 0}</div><div className="text-xxs text-gray-500">tools</div></div>
                  </div>
                ) : (
                  <div className="text-xs text-gray-500">{workspace?.cost_usage?.reason ?? "Usage telemetry unavailable."}</div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

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
                  const entry = history.find((h) => h.to_state === state);
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
                        {entry && <div className="text-xxs text-gray-600">{format(new Date(entry.transitioned_at), "MMM d HH:mm:ss")}{entry.triggered_by && ` · ${entry.triggered_by}`}</div>}
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
                        <div className="text-xs font-medium text-gray-200 mb-1">{d.gate_type ?? d.decision_type ?? "approval"}</div>
                        <p className="text-xs text-gray-400 mb-2">{d.prompt ?? "Operator decision required."}</p>
                        {Boolean(d.context) && <button onClick={() => setExpandedDecision(expandedDecision === d.id ? null : d.id)} className="text-xxs text-blue-400 mb-2">{expandedDecision === d.id ? "Hide context" : "Show context"}</button>}
                        {expandedDecision === d.id && <pre className="text-xxs text-gray-500 bg-gray-950 rounded p-2 overflow-x-auto mb-2">{JSON.stringify(d.context, null, 2)}</pre>}
                        <div className="flex gap-2">
                          <button onClick={() => handleDecision(d.id, "APPROVED")} disabled={!!actionLoading} className="flex items-center gap-1 px-2 py-1 bg-green-600/20 text-green-400 border border-green-800 text-xs rounded disabled:opacity-50"><CheckCircle size={11} />{actionLoading === `${d.id}-APPROVED` ? "..." : "Approve"}</button>
                          <button onClick={() => handleDecision(d.id, "REJECTED")} disabled={!!actionLoading} className="flex items-center gap-1 px-2 py-1 bg-red-600/20 text-red-400 border border-red-800 text-xs rounded disabled:opacity-50"><XCircle size={11} />Reject</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Project History</h2>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {history.length === 0 ? (
                    <div className="text-xs text-gray-500">No history yet.</div>
                  ) : history.map((entry, index) => (
                    <div key={`${entry.transitioned_at}-${index}`} className="rounded-lg bg-gray-950 border border-gray-800 p-3">
                      <div className="flex items-center justify-between gap-3 text-xs">
                        <div className="text-gray-200">{(entry.event || "transition").replace(/_/g, " ")}</div>
                        <div className="text-gray-500">{format(new Date(entry.transitioned_at), "MMM d HH:mm:ss")}</div>
                      </div>
                      <div className="text-xxs text-gray-500 mt-1">
                        {(entry.from_state || entry.to_state).replace(/_/g, " ")} → {entry.to_state.replace(/_/g, " ")}
                        {entry.triggered_by && ` · ${entry.triggered_by}`}
                      </div>
                      {entry.payload && (
                        <div className="text-xxs text-gray-400 mt-1">
                          {entry.payload.to_node_id
                            ? `Override to ${String(entry.payload.to_node_id)}${entry.payload.reason ? `: ${String(entry.payload.reason)}` : ""}`
                            : JSON.stringify(entry.payload)}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
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
          {flowError && (
            <div className="rounded-xl border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-300">
              {flowError}
            </div>
          )}
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
                          data-testid={`assign-flow-${flow.id}`}
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
                    <button onClick={() => handleFlowAction("start")} disabled={!!actionLoading} data-testid="flow-start-button" className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg">
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
                    <button onClick={handleRetry} disabled={!!actionLoading} data-testid="flow-retry-button" className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-600/20 text-amber-400 border border-amber-800 rounded-lg">
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

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-gray-300 mb-3">Current Node</h2>
                  <div className="space-y-2">
                    {(flowInstance.active_node_ids || []).length === 0 ? (
                      <div className="text-xs text-gray-500">No active node yet.</div>
                    ) : flowInstance.active_node_ids.map((nodeId) => {
                      const node = flowDefinition?.nodes.find((item) => item.id === nodeId);
                      return (
                        <div key={nodeId} className="rounded-lg bg-gray-950 border border-gray-800 p-3">
                          <div className="text-sm text-white">{node?.label || nodeId}</div>
                          <div className="text-xxs text-gray-500 mt-1">{node?.type || "task"}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-gray-300 mb-3">Past Transitions</h2>
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {nodeExecutions.filter((exec) => exec.status !== "RUNNING").length === 0 ? (
                      <div className="text-xs text-gray-500">No completed steps yet.</div>
                    ) : nodeExecutions.filter((exec) => exec.status !== "RUNNING").map((exec, index) => (
                      <div key={`${exec.id || exec.node_id}-${index}`} className="rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs">
                        <div className="text-gray-200">{exec.node_label || exec.node_id}</div>
                        <div className="text-gray-500 mt-1">{exec.status}{exec.completed_at ? ` · ${format(new Date(exec.completed_at), "HH:mm:ss")}` : ""}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-gray-300 mb-3">Next Possible Transitions</h2>
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {nextPossibleTransitions.length === 0 ? (
                      <div className="text-xs text-gray-500">No outgoing transitions available.</div>
                    ) : nextPossibleTransitions.map((transition) => (
                      <div key={transition.edgeId} className="rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs">
                        <div className="text-gray-200">{transition.targetLabel}</div>
                        <div className="text-gray-500 mt-1">
                          From {transition.sourceId}
                          {transition.condition ? ` · ${transition.condition}` : ""}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {(flowInstance.escalated_to || flowInstance.escalation_reason) && (
                <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-amber-300 mb-2">Escalation</h2>
                  <div className="text-xs text-amber-100">
                    {flowInstance.escalated_to ? `Escalated to ${flowInstance.escalated_to}` : "Escalation recorded"}
                  </div>
                  {flowInstance.escalation_reason && (
                    <div className="text-xxs text-amber-200/80 mt-1">{flowInstance.escalation_reason}</div>
                  )}
                </div>
              )}

              {(flowInstance.status === "RUNNING" || flowInstance.status === "WAITING_APPROVAL") && flowInstance.active_node_ids?.length > 0 && (
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-gray-300 mb-3">Active Nodes</h2>
                  <div className="space-y-2">
                    {flowInstance.active_node_ids.map((nodeId) => {
                      const activeNode = flowDefinition?.nodes.find((node) => node.id === nodeId);
                      const nodeExec = nodeExecutions.find(e => e.node_id === nodeId && e.status === "RUNNING");
                      return (
                        <div key={nodeId} className="flex items-center justify-between bg-gray-800 rounded-lg p-3">
                          <div>
                            <div className="text-sm font-medium text-white">{activeNode?.label || nodeId}</div>
                            <div className="text-xxs text-gray-500 mt-0.5">{activeNode?.type || "task"}</div>
                            {nodeExec && <div className="text-xs text-gray-500 mt-0.5">Running...</div>}
                          </div>
                          <div className="flex gap-2">
                            {activeNode?.type === "approval" ? (
                              <>
                                <button onClick={() => handleNodeAction(nodeId, "complete", { decision: "approved", approved: true })} disabled={!!actionLoading} data-testid="approval-approve-button" className="px-2 py-1 text-xs bg-green-600/20 text-green-400 border border-green-800 rounded hover:bg-green-600/40">
                                  Approve
                                </button>
                                <button onClick={() => handleNodeAction(nodeId, "complete", { decision: "edit_requested" })} disabled={!!actionLoading} data-testid="approval-edit-requested-button" className="px-2 py-1 text-xs bg-blue-600/20 text-blue-400 border border-blue-800 rounded hover:bg-blue-600/40">
                                  Request Edit
                                </button>
                                <button onClick={() => handleNodeAction(nodeId, "complete", { decision: "rejected", approved: false })} disabled={!!actionLoading} data-testid="approval-reject-button" className="px-2 py-1 text-xs bg-red-600/20 text-red-400 border border-red-800 rounded hover:bg-red-600/40">
                                  Reject
                                </button>
                              </>
                            ) : (
                              <>
                                <button onClick={() => handleNodeAction(nodeId, "complete")} disabled={!!actionLoading} data-testid="complete-node-button" className="px-2 py-1 text-xs bg-green-600/20 text-green-400 border border-green-800 rounded hover:bg-green-600/40">
                                  Complete
                                </button>
                                <button onClick={() => handleNodeAction(nodeId, "timeout", { error: "Timed out waiting for analysis" })} disabled={!!actionLoading} data-testid="timeout-node-button" className="px-2 py-1 text-xs bg-amber-600/20 text-amber-400 border border-amber-800 rounded hover:bg-amber-600/40">
                                  Timeout
                                </button>
                                <button onClick={() => handleNodeAction(nodeId, "fail")} disabled={!!actionLoading} data-testid="fail-node-button" className="px-2 py-1 text-xs bg-red-600/20 text-red-400 border border-red-800 rounded hover:bg-red-600/40">
                                  Fail
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">Manual Override</h2>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <select
                    value={overrideNodeId}
                    onChange={(e) => setOverrideNodeId(e.target.value)}
                    data-testid="override-node-select"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white"
                  >
                    {overrideableNodes.map((node) => (
                      <option key={node.id} value={node.id}>{node.label}</option>
                    ))}
                  </select>
                  <input
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    placeholder="Reason for override"
                    data-testid="override-reason-input"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white"
                  />
                  <button
                    onClick={handleOverrideFlowNode}
                    disabled={!overrideNodeId || actionLoading === "override-flow-node"}
                    data-testid="override-node-button"
                    className="px-3 py-2 text-xs font-medium bg-amber-600/20 text-amber-400 border border-amber-800 rounded-lg hover:bg-amber-600/40 disabled:opacity-50"
                  >
                    {actionLoading === "override-flow-node" ? "Overriding..." : "Override Node"}
                  </button>
                </div>
                <p className="text-xxs text-gray-500 mt-2">Overrides are logged into project history for audit visibility.</p>
              </div>

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

      {activeTab === "context" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="text-sm text-gray-400">
              {contextItems.length} context items attached to this project
            </div>
            <button
              onClick={() => setShowContextUpload(!showContextUpload)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600/20 text-blue-400 border border-blue-700 rounded-lg hover:bg-blue-600/40"
            >
              <Plus size={12} />
              Add Item
            </button>
          </div>

          {showContextUpload && (
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Type</label>
                  <select
                    value={newContextType}
                    onChange={(e) => setNewContextType(e.target.value as "FILE" | "URL" | "TEXT")}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
                  >
                    <option value="FILE">File Attachment</option>
                    <option value="URL">URL Link</option>
                    <option value="TEXT">Text Note</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Name</label>
                  <input
                    value={newContextName}
                    onChange={(e) => setNewContextName(e.target.value)}
                    placeholder="Item name..."
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
                  />
                </div>
              </div>

              {newContextType === "URL" && (
                <div>
                  <label className="block text-xs text-gray-500 mb-1">URL</label>
                  <input
                    value={newContextUrl}
                    onChange={(e) => setNewContextUrl(e.target.value)}
                    placeholder="https://..."
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
                  />
                </div>
              )}

              {newContextType === "TEXT" && (
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Content</label>
                  <textarea
                    value={newContextText}
                    onChange={(e) => setNewContextText(e.target.value)}
                    placeholder="Enter text content..."
                    rows={4}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
                  />
                </div>
              )}

              {newContextType === "FILE" && (
                <div className="border border-dashed border-gray-700 rounded-lg p-4 text-center">
                  <Upload size={24} className="mx-auto text-gray-500 mb-2" />
                  <p className="text-xs text-gray-500">File upload UI would go here</p>
                  <p className="text-xxs text-gray-600 mt-1">For now, enter file details manually</p>
                </div>
              )}

              <div>
                <label className="block text-xs text-gray-500 mb-1">Tags (comma-separated)</label>
                <input
                  value={newContextTags}
                  onChange={(e) => setNewContextTags(e.target.value)}
                  placeholder="requirements, architecture, notes"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
                />
              </div>

              <div className="flex gap-2">
                <button
                  onClick={handleAddContextItem}
                  disabled={actionLoading === "add-context" || !newContextName.trim()}
                  className="px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg hover:bg-green-600/40 disabled:opacity-50"
                >
                  {actionLoading === "add-context" ? "Adding..." : "Add Item"}
                </button>
                <button
                  onClick={() => setShowContextUpload(false)}
                  className="px-3 py-1.5 text-xs font-medium bg-gray-600/20 text-gray-400 border border-gray-700 rounded-lg"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {contextLoading ? (
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center text-gray-500 text-sm">
              Loading context items...
            </div>
          ) : contextItems.length === 0 ? (
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center">
              <FileText size={32} className="mx-auto text-gray-600 mb-3" />
              <p className="text-gray-400 text-sm">No context items yet</p>
              <p className="text-gray-500 text-xs mt-1">Add files, URLs, or notes to build project context</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {contextItems.map((item) => (
                <div key={item.id} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                  <div className="flex items-start gap-3">
                    <div className={clsx(
                      "p-2 rounded-lg",
                      item.item_type === "FILE" ? "bg-blue-900/30 text-blue-400" :
                      item.item_type === "URL" ? "bg-purple-900/30 text-purple-400" :
                      "bg-amber-900/30 text-amber-400"
                    )}>
                      {item.item_type === "FILE" ? <FileText size={16} /> : item.item_type === "URL" ? <LinkIcon size={16} /> : <FileText size={16} />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-white truncate">{item.name}</div>
                      <div className="text-xs text-gray-500 mt-0.5">
                        {item.item_type}
                        {item.size_bytes && ` · ${(item.size_bytes / 1024).toFixed(1)} KB`}
                      </div>
                      {item.description && (
                        <div className="text-xs text-gray-400 mt-1 line-clamp-2">{item.description}</div>
                      )}
                      {item.tags && item.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {item.tags.map((tag) => (
                            <span key={tag} className="px-1.5 py-0.5 bg-gray-800 text-gray-400 text-xxs rounded">
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => handleDeleteContextItem(item.id)}
                      disabled={actionLoading === `delete-${item.id}`}
                      className="p-1 text-gray-500 hover:text-red-400"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
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
