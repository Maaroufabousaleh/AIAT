"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import {
  WORKFLOW_STATES,
  STATE_COLORS,
  TERMINAL_STATES,
  type WorkflowState,
} from "@/lib/constants";
import { formatDistanceToNow } from "date-fns";
import { formatInTz } from "@/lib/datetime";
import {
  ArrowLeft,
  RefreshCw,
  CheckCircle,
  XCircle,
  RotateCcw,
  Archive,
  Play,
  Pause,
  StopCircle,
  GitBranch,
  ArrowRightCircle,
  FileText,
  Upload,
  Trash2,
  Plus,
  Link as LinkIcon,
  MoreVertical,
  ChevronDown,
  Inbox,
  Activity,
  Clock,
  ListChecks,
  Wallet,
} from "lucide-react";
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  NodeTypes,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type {
  Flow,
  FlowInstance,
  FlowNodeExecution,
  FlowDefinition,
  FlowNodeType,
  FlowInstanceStatus,
} from "@/lib/flow-types";
import {
  NODE_TYPE_LABELS,
  FLOW_NODE_COLORS,
  FLOW_STATUS_COLORS,
} from "@/lib/flow-types";
import {
  BulkActionBar,
  RowCheckbox,
  SelectAllCheckbox,
} from "@/components/ui/BulkActionBar";
import { useBulkSelection } from "@/lib/use-bulk-selection";
import { PageHeader } from "@/components/ui/PageHeader";
import { KpiCard } from "@/components/ui/KpiCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

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
  item_type: string;
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
  updated_at?: string;
  source?: "context" | "document" | string;
  read_only?: boolean;
  document_id?: string;
  doc_type?: string;
  version?: number;
  status?: string;
  lineage_id?: string;
}

interface Project {
  id: string;
  name: string;
  description?: string;
  state: WorkflowState;
  created_at: string;
  updated_at: string;
}

interface EvidenceCheck {
  name: string;
  required: boolean;
  passed: boolean;
  reason?: string | null;
  evidence_refs: string[];
}

interface ProjectEvidence {
  policy_id: string;
  policy_version: string;
  status: string;
  completeness_score: number;
  checks: EvidenceCheck[];
  evidence_refs: Record<string, string[]>;
}

interface RepositorySummary {
  status?: string;
  mode?: "clone" | "init" | "none" | string;
  repository_url?: string;
  workspace_path?: string;
  workspace_relative_path?: string;
  initialized?: boolean;
  remote?: string | null;
  remote_name?: string;
  branch?: string | null;
  head?: string | null;
  clean?: boolean | null;
  changes?: string[];
  error?: string;
}

interface WorkspaceSummary {
  repository?: RepositorySummary | null;
  next_actions: Array<{ kind: string; label: string; severity: string }>;
  pending_approvals: Decision[];
  recent_activity: Array<{
    event_type: string;
    occurred_at?: string;
    summary: string;
    actor?: string;
  }>;
  worker_activity: Array<{
    task_id?: string;
    agent_id?: string;
    team_id?: string;
    status?: string;
    updated_at?: string;
  }>;
  artifacts: Array<{
    id?: number;
    path: string;
    agent_id?: string;
    size_bytes?: number;
    created_at?: string;
  }>;
  logs: Array<{
    id?: string;
    level?: string;
    message?: string;
    created_at?: string;
  }>;
  cost_usage: {
    available: boolean;
    reason?: string;
    total_cost_usd?: number;
    tool_calls?: number;
    llm_calls?: number;
    failed_calls?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    source?: string;
  };
  flow_instance?: FlowInstance | null;
}

const flowNodeTypes: NodeTypes = {};

function FlowNodeComponent({
  data,
  selected,
}: {
  data: { label: string; type: FlowNodeType; status?: string };
  selected?: boolean;
}) {
  // Visual rings signal the execution status of each node inside the ReactFlow
  // canvas. We use accent colors (blue/green/red) so operators can spot
  // stuck vs. failed steps at a glance without reading tooltips.
  const statusColors: Record<string, string> = {
    RUNNING: "ring-2 ring-blue-400 shadow-md shadow-blue-500/30",
    COMPLETED: "ring-2 ring-emerald-400",
    FAILED: "ring-2 ring-rose-400",
  };

  return (
    <div
      className={clsx(
        "px-3 py-2 rounded-lg border-2 min-w-[100px] text-center transition-shadow",
        selected
          ? "border-blue-400 shadow-lg shadow-blue-500/30"
          : "border-slate-700",
        FLOW_NODE_COLORS[data.type as FlowNodeType] || "bg-slate-600",
        statusColors[data.status || ""] || "",
      )}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-400" />
      <div className="text-sm font-medium text-white">{data.label}</div>
      <div className="text-xs text-white/70">
        {NODE_TYPE_LABELS[data.type as FlowNodeType]}
      </div>
      {data.status && data.status !== "RUNNING" && (
        <div
          className={clsx(
            "text-xxs mt-1",
            data.status === "COMPLETED" ? "text-emerald-300" : "text-rose-300",
          )}
        >
          {data.status}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-slate-400"
      />
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

  const [activeTab, setActiveTab] = useState<
    "workspace" | "workflow" | "flow" | "context" | "evidence"
  >("workspace");
  // Sub-tabs within the workspace view let operators jump between the most
  // important slices (next action, project activity, live resources, spend)
  // without scrolling through every card on a tall screen.
  const [workspaceSubTab, setWorkspaceSubTab] = useState<
    "activity" | "resources" | "cost"
  >("activity");
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [repositoryError, setRepositoryError] = useState<string | null>(null);
  const [flowInstance, setFlowInstance] = useState<FlowInstance | null>(null);
  const [flowDefinition, setFlowDefinition] = useState<FlowDefinition | null>(
    null,
  );
  const [nodeExecutions, setNodeExecutions] = useState<FlowNodeExecution[]>([]);
  const [flowLoading, setFlowLoading] = useState(false);
  const [showFlowSwitch, setShowFlowSwitch] = useState(false);
  const [availableFlows, setAvailableFlows] = useState<Flow[]>([]);
  const [flowError, setFlowError] = useState<string | null>(null);
  // Overflow menu for secondary flow actions (override, switch flow) so the
  // primary action row stays compact and uncluttered.
  const [showFlowActionsMenu, setShowFlowActionsMenu] = useState(false);
  const flowActionsMenuRef = useRef<HTMLDivElement | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([] as Edge[]);
  const [contextItems, setContextItems] = useState<ContextItem[]>([]);
  const [contextLoading, setContextLoading] = useState(false);
  const [evidence, setEvidence] = useState<ProjectEvidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [showContextUpload, setShowContextUpload] = useState(false);
  const [newContextName, setNewContextName] = useState("");
  const [newContextType, setNewContextType] = useState<"FILE" | "URL" | "TEXT">(
    "FILE",
  );
  const [newContextUrl, setNewContextUrl] = useState("");
  const [newContextText, setNewContextText] = useState("");
  const [newContextTags, setNewContextTags] = useState("");
  const [overrideNodeId, setOverrideNodeId] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [bulkContextDeleting, setBulkContextDeleting] = useState(false);
  const [bulkContextError, setBulkContextError] = useState("");

  const contextItemIds = useMemo(
    () => contextItems.filter((c) => !c.read_only).map((c) => c.id),
    [contextItems],
  );
  const contextSelection = useBulkSelection(contextItemIds);
  const generatedDocumentCount = useMemo(
    () => contextItems.filter((item) => item.read_only).length,
    [contextItems],
  );
  useEffect(() => {
    contextSelection.prune();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextItemIds.join(",")]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [proj, hist, dec, trans] = await Promise.allSettled([
        fetch(`/api/projects/${id}`)
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null),
        fetch(`/api/projects/${id}/state-history`)
          .then((r) => (r.ok ? r.json() : []))
          .catch(() => []),
        fetch(`/api/projects/${id}/decisions`)
          .then((r) => (r.ok ? r.json() : []))
          .catch(() => []),
        fetch(`/api/projects/${id}/transition`)
          .then((r) => (r.ok ? r.json() : []))
          .catch(() => []),
      ]);
      if (proj.status === "fulfilled" && proj.value) setProject(proj.value);
      if (hist.status === "fulfilled")
        setHistory(
          Array.isArray(hist.value) ? hist.value : (hist.value?.history ?? []),
        );
      if (dec.status === "fulfilled")
        setDecisions(
          Array.isArray(dec.value) ? dec.value : (dec.value?.decisions ?? []),
        );
      if (trans.status === "fulfilled")
        setAllowedTransitions(
          Array.isArray(trans.value)
            ? trans.value
            : (trans.value?.transitions ?? []),
        );
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
          setOverrideNodeId(
            instance.active_node_ids?.[0] ||
              flow.definition_json?.nodes?.[0]?.id ||
              "",
          );

          const { nodes: flowNodes, edges: flowEdges } = convertToReactFlow(
            flow.definition_json?.nodes || [],
            flow.definition_json?.edges || [],
            instance.active_node_ids || [],
            executions,
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

        const flowsRes = await fetch("/api/flows?is_active=true");
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

  const loadEvidence = useCallback(async () => {
    setEvidenceLoading(true);
    try {
      const response = await fetch(`/api/projects/${id}/evidence`);
      setEvidence(response.ok ? await response.json() : null);
    } catch {
      setEvidence(null);
    } finally {
      setEvidenceLoading(false);
    }
  }, [id]);

  const loadWorkspace = useCallback(async () => {
    setWorkspaceLoading(true);
    try {
      const [res, repositoryRes] = await Promise.all([
        fetch(`/api/projects/${id}/workspace`),
        fetch(`/api/projects/${id}/repository`),
      ]);
      if (res.ok) {
        const data = await res.json();
        if (repositoryRes.ok) {
          const repositoryData = await repositoryRes.json();
          data.repository = repositoryData.workspace ?? null;
        }
        // Validate workspace data structure - ensure required fields exist
        if (data && typeof data === "object" && "recent_activity" in data) {
          setWorkspace(data);
        } else {
          console.error("Invalid workspace data structure:", data);
          setWorkspace(null);
        }
      } else {
        setWorkspace(null);
      }
    } catch (err) {
      console.error("Failed to load workspace:", err);
      setWorkspace(null);
    } finally {
      setWorkspaceLoading(false);
    }
  }, [id]);

  async function handleRepositoryAction(operation: "sync" | "status") {
    setActionLoading(`repository-${operation}`);
    setRepositoryError(null);
    try {
      const res = await fetch(`/api/projects/${id}/repository`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setRepositoryError(data.error || `Git ${operation} failed`);
      } else {
        await loadWorkspace();
      }
    } catch {
      setRepositoryError(`Git ${operation} failed`);
    } finally {
      setActionLoading(null);
    }
  }

  const completedNodeIds = useMemo(
    () =>
      nodeExecutions
        .filter((execution) => execution.status === "COMPLETED")
        .map((execution) => execution.node_id),
    [nodeExecutions],
  );

  const nextPossibleTransitions = useMemo(() => {
    if (!flowDefinition || !flowInstance) return [];
    const completed = new Set(completedNodeIds);

    return (flowInstance.active_node_ids || []).flatMap((nodeId) =>
      (flowDefinition.edges || [])
        .filter((edge) => edge.source === nodeId && !completed.has(edge.target))
        .map((edge) => {
          const targetNode = flowDefinition.nodes.find(
            (node) => node.id === edge.target,
          );
          return {
            edgeId: edge.id,
            condition: edge.condition,
            sourceId: nodeId,
            targetId: edge.target,
            targetLabel: targetNode?.label || edge.target,
          };
        }),
    );
  }, [completedNodeIds, flowDefinition, flowInstance]);

  const overrideableNodes = useMemo(
    () => flowDefinition?.nodes || [],
    [flowDefinition],
  );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (activeTab === "workspace") {
      loadWorkspace();
    }
  }, [activeTab, loadWorkspace]);

  // Close the flow actions overflow menu when clicking outside of it, and
  // when the user navigates away from the flow tab so it doesn't reopen
  // stale on a future visit.
  useEffect(() => {
    if (!showFlowActionsMenu) return;
    function handleClick(event: MouseEvent) {
      if (
        flowActionsMenuRef.current &&
        event.target instanceof Node &&
        !flowActionsMenuRef.current.contains(event.target)
      ) {
        setShowFlowActionsMenu(false);
      }
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setShowFlowActionsMenu(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [showFlowActionsMenu]);

  useEffect(() => {
    if (activeTab !== "flow") setShowFlowActionsMenu(false);
  }, [activeTab]);

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
    if (activeTab === "evidence") {
      loadEvidence();
    }
  }, [activeTab, loadEvidence]);

  useEffect(() => {
    if (!project || TERMINAL_STATES.includes(project.state)) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [project, load]);

  async function handleDecision(
    decisionId: string,
    decision: "APPROVED" | "REJECTED" | "EDITS",
  ) {
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
    options?: { approved?: boolean; decision?: string; error?: string },
  ) {
    if (!flowInstance) return;
    setActionLoading(`node-${nodeId}`);
    try {
      const res = await fetch(
        `/api/flows/instances/${flowInstance.id}/node-action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ node_id: nodeId, action, ...options }),
        },
      );
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
      const res = await fetch(
        `/api/flows/instances/${flowInstance.id}/switch`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ flow_id: newFlowId, preserve_context: true }),
        },
      );
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
      const res = await fetch(`/api/flows/instances/${flowInstance.id}/retry`, {
        method: "POST",
      });
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
      const res = await fetch("/api/flows?is_active=true");
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
      const res = await fetch(
        `/api/flows/instances/${flowInstance.id}/override`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_node_id: overrideNodeId,
            actor_id: "human",
            actor_role: "human_operator",
            reason: overrideReason || undefined,
          }),
        },
      );
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
        tags: newContextTags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
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
      const res = await fetch(`/api/projects/${id}/context/${itemId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        await loadContextData();
      }
    } finally {
      setActionLoading(null);
    }
  }

  async function handleBulkDeleteContextItems() {
    if (contextSelection.selectedCount === 0) return;
    const ids = Array.from(contextSelection.selected);
    setBulkContextDeleting(true);
    setBulkContextError("");
    let failed = 0;
    try {
      const results = await Promise.allSettled(
        ids.map(async (itemId) => {
          const res = await fetch(`/api/projects/${id}/context/${itemId}`, {
            method: "DELETE",
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
        }),
      );
      for (const r of results) if (r.status === "rejected") failed++;
      if (failed > 0) {
        setBulkContextError(
          `Deleted ${ids.length - failed} of ${ids.length} item${ids.length === 1 ? "" : "s"} (${failed} failed).`,
        );
      }
      await loadContextData();
      contextSelection.clear();
    } finally {
      setBulkContextDeleting(false);
    }
  }

  if (loading) {
    return (
      <div className="dashboard-page flex items-center justify-center h-full">
        <div className="text-slate-500 text-sm">Loading project…</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="dashboard-page">
        <ErrorBanner tone="error" title="Project not found">
          We could not locate this project. It may have been deleted, archived,
          or you may be following a stale link.
        </ErrorBanner>
        <Link
          href="/projects"
          className="text-sm text-blue-400 hover:text-blue-300 inline-flex items-center gap-1"
        >
          <ArrowLeft size={14} /> Back to projects
        </Link>
      </div>
    );
  }

  const isFailed = project.state === "FAILED";
  const isTerminal = TERMINAL_STATES.includes(project.state);

  // Refresh handler respects the active tab so operators get fresh data
  // for whichever surface they're currently inspecting.
  const handleRefresh = () => {
    if (activeTab === "flow") return loadFlowData();
    if (activeTab === "workspace") return loadWorkspace();
    if (activeTab === "context") return loadContextData();
    if (activeTab === "evidence") return loadEvidence();
    return load();
  };
  const isRefreshing =
    loading || flowLoading || workspaceLoading || contextLoading || evidenceLoading;

  return (
    <div className="dashboard-page">
      <PageHeader
        icon="folder-kanban"
        title={project.name}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-400">
            <span className="text-slate-500 font-mono text-xxs">
              {project.id}
            </span>
            {project.description && (
              <span className="text-slate-400 line-clamp-1 max-w-md">
                {project.description}
              </span>
            )}
            <span className="text-slate-500 text-xxs">
              Created{" "}
              {formatDistanceToNow(new Date(project.created_at), {
                addSuffix: true,
              })}
            </span>
          </span>
        }
        actions={
          <>
            <span
              className={clsx(
                "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium text-white border border-white/10",
                STATE_COLORS[project.state] ?? "bg-slate-600",
              )}
              aria-label={`Project state: ${project.state.replace(/_/g, " ")}`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-white/70" />
              {project.state?.replace(/_/g, " ")}
            </span>
            <button
              onClick={handleRefresh}
              title="Refresh"
              aria-label="Refresh project data"
              className="p-2 rounded-lg border border-slate-800 bg-slate-950/40 text-slate-400 hover:text-slate-100 hover:border-slate-600 hover:bg-slate-900 transition-colors disabled:opacity-50"
            >
              <RefreshCw
                size={15}
                className={isRefreshing ? "animate-spin" : ""}
              />
            </button>
          </>
        }
      />

      <div
        className="flex flex-wrap gap-1 border-b border-slate-800"
        role="tablist"
        aria-label="Project views"
      >
        <button
          onClick={() => setActiveTab("workspace")}
          data-testid="project-tab-workspace"
          role="tab"
          aria-selected={activeTab === "workspace"}
          aria-controls="project-panel-workspace"
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "workspace"
              ? "border-blue-400 text-blue-300"
              : "border-transparent text-slate-400 hover:text-slate-200",
          )}
        >
          Workspace
        </button>
        <button
          onClick={() => setActiveTab("workflow")}
          data-testid="project-tab-workflow"
          role="tab"
          aria-selected={activeTab === "workflow"}
          aria-controls="project-panel-workflow"
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "workflow"
              ? "border-blue-400 text-blue-300"
              : "border-transparent text-slate-400 hover:text-slate-200",
          )}
        >
          Workflow
          {history.length > 0 && (
            <span className="px-1.5 py-0.5 rounded text-xxs bg-slate-800 text-slate-300">
              {history.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab("flow")}
          data-testid="project-tab-flow"
          role="tab"
          aria-selected={activeTab === "flow"}
          aria-controls="project-panel-flow"
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "flow"
              ? "border-blue-400 text-blue-300"
              : "border-transparent text-slate-400 hover:text-slate-200",
          )}
        >
          <GitBranch size={14} />
          Flow
          {flowInstance && (
            <span
              className={clsx(
                "px-1.5 py-0.5 rounded text-xxs text-white",
                FLOW_STATUS_COLORS[flowInstance.status as FlowInstanceStatus],
              )}
            >
              {flowInstance.status}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab("context")}
          data-testid="project-tab-context"
          role="tab"
          aria-selected={activeTab === "context"}
          aria-controls="project-panel-context"
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "context"
              ? "border-blue-400 text-blue-300"
              : "border-transparent text-slate-400 hover:text-slate-200",
          )}
        >
          <FileText size={14} />
          Context
          {contextItems.length > 0 && (
            <span className="px-1.5 py-0.5 rounded text-xxs bg-slate-700 text-slate-200">
              {contextItems.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab("evidence")}
          data-testid="project-tab-evidence"
          role="tab"
          aria-selected={activeTab === "evidence"}
          aria-controls="project-panel-evidence"
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "evidence"
              ? "border-blue-400 text-blue-300"
              : "border-transparent text-slate-400 hover:text-slate-200",
          )}
        >
          Evidence
          {evidence && (
            <span className={clsx(
              "px-1.5 py-0.5 rounded text-xxs",
              evidence.status === "complete"
                ? "bg-emerald-950/60 text-emerald-300"
                : "bg-amber-950/60 text-amber-300",
            )}>
              {Math.round(evidence.completeness_score * 100)}%
            </span>
          )}
        </button>
      </div>

      {activeTab === "workspace" && (
        <div
          id="project-panel-workspace"
          role="tabpanel"
          aria-labelledby="project-tab-workspace"
          className="space-y-4"
        >
          {workspace === null && !workspaceLoading && (
            <ErrorBanner tone="error" title="Workspace data unavailable">
              The project workspace API may be unavailable or the project may
              not exist. Please try refreshing the page.
            </ErrorBanner>
          )}

          {/* Sub-tabs help operators focus on one slice of the workspace at a time
              (operator actions, project resources, or cost) without scrolling. */}
          <div
            className="flex flex-wrap gap-1 dashboard-toolbar"
            role="tablist"
            aria-label="Workspace sections"
          >
            <button
              onClick={() => setWorkspaceSubTab("activity")}
              role="tab"
              aria-selected={workspaceSubTab === "activity"}
              className={clsx(
                "px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors flex items-center gap-1.5",
                workspaceSubTab === "activity"
                  ? "bg-blue-500/15 text-blue-200 border-blue-400/40"
                  : "bg-slate-900/40 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700",
              )}
            >
              <Activity size={12} /> Activity
            </button>
            <button
              onClick={() => setWorkspaceSubTab("resources")}
              role="tab"
              aria-selected={workspaceSubTab === "resources"}
              className={clsx(
                "px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors flex items-center gap-1.5",
                workspaceSubTab === "resources"
                  ? "bg-blue-500/15 text-blue-200 border-blue-400/40"
                  : "bg-slate-900/40 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700",
              )}
            >
              <ListChecks size={12} /> Resources
            </button>
            <button
              onClick={() => setWorkspaceSubTab("cost")}
              role="tab"
              aria-selected={workspaceSubTab === "cost"}
              className={clsx(
                "px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors flex items-center gap-1.5",
                workspaceSubTab === "cost"
                  ? "bg-blue-500/15 text-blue-200 border-blue-400/40"
                  : "bg-slate-900/40 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700",
              )}
            >
              <Wallet size={12} /> Cost
            </button>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <KpiCard
              label="Approvals"
              value={workspace?.pending_approvals?.length ?? 0}
              hint="pending decisions"
              icon="check-circle"
              tone={
                (workspace?.pending_approvals?.length ?? 0) > 0
                  ? "warning"
                  : "neutral"
              }
            />
            <KpiCard
              label="Artifacts"
              value={workspace?.artifacts?.length ?? 0}
              hint="project-scoped"
              icon="file-text"
              tone="info"
            />
            <KpiCard
              label="Flow"
              value={
                workspace?.flow_instance?.status ??
                flowInstance?.status ??
                "not attached"
              }
              hint="active instance"
              icon="git-branch"
              tone={flowInstance?.status === "FAILED" ? "negative" : "info"}
            />
          </div>

          {workspaceSubTab === "activity" && (
            <div className="space-y-4">
              <div className="dashboard-surface p-4">
                <h2 className="text-sm font-medium text-white mb-3">
                  Next Operator Action
                </h2>
                <div className="space-y-2">
                  {(
                    workspace?.next_actions ?? [
                      {
                        kind: "loading",
                        label: workspaceLoading
                          ? "Loading workspace..."
                          : "Workspace summary unavailable",
                        severity: "medium",
                      },
                    ]
                  ).map((action, index) => (
                    <div
                      key={`${action.kind}-${index}`}
                      className={clsx(
                        "rounded-lg border px-3 py-2 text-sm",
                        action.severity === "high"
                          ? "border-amber-700/70 bg-amber-950/40 text-amber-100"
                          : action.severity === "medium"
                            ? "border-blue-700/70 bg-blue-950/30 text-blue-100"
                            : "border-slate-800 bg-slate-950/50 text-slate-300",
                      )}
                    >
                      {action.label}
                    </div>
                  ))}
                </div>
              </div>

              {(workspace?.pending_approvals ?? []).length > 0 && (
                <div className="dashboard-surface border-amber-700/50 bg-amber-950/15 p-4">
                  <h2 className="text-sm font-medium text-amber-200 mb-3">
                    Workspace Approvals
                  </h2>
                  <div className="space-y-3">
                    {workspace!.pending_approvals.map((approval, index) => (
                      <div
                        key={approval.id}
                        className="rounded-lg bg-slate-950/55 border border-amber-900/60 p-3"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="text-xs font-medium text-slate-100">
                              {approval.gate_type ??
                                approval.decision_type ??
                                "approval"}
                            </div>
                            <div className="text-xxs text-slate-500 mt-1">
                              {approval.created_at
                                ? formatInTz(approval.created_at, "MMM d HH:mm")
                                : "created time unknown"}
                              {index === 0 ? " · next decision" : " · queued"}
                            </div>
                          </div>
                          {index === 0 && (
                            <div className="flex flex-wrap gap-2 justify-end">
                              <button
                                onClick={() =>
                                  handleDecision(approval.id, "APPROVED")
                                }
                                disabled={!!actionLoading}
                                className="inline-flex items-center gap-1 px-2 py-1 bg-emerald-500/15 text-emerald-300 border border-emerald-500/40 text-xs rounded hover:bg-emerald-500/25 disabled:opacity-50 transition-colors"
                              >
                                <CheckCircle size={11} />
                                {actionLoading === `${approval.id}-APPROVED`
                                  ? "..."
                                  : "Approve"}
                              </button>
                              <button
                                onClick={() =>
                                  handleDecision(approval.id, "EDITS")
                                }
                                disabled={!!actionLoading}
                                className="inline-flex items-center gap-1 px-2 py-1 bg-blue-500/15 text-blue-300 border border-blue-500/40 text-xs rounded hover:bg-blue-500/25 disabled:opacity-50 transition-colors"
                              >
                                <FileText size={11} />
                                Edits
                              </button>
                              <button
                                onClick={() =>
                                  handleDecision(approval.id, "REJECTED")
                                }
                                disabled={!!actionLoading}
                                className="inline-flex items-center gap-1 px-2 py-1 bg-rose-500/15 text-rose-300 border border-rose-500/40 text-xs rounded hover:bg-rose-500/25 disabled:opacity-50 transition-colors"
                              >
                                <XCircle size={11} />
                                Reject
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="dashboard-surface p-4">
                <h2 className="text-sm font-medium text-white mb-3">
                  Audit Timeline
                </h2>
                <div className="space-y-2 max-h-72 overflow-y-auto">
                  {(workspace?.recent_activity ?? []).length === 0 ? (
                    <div className="text-xs text-slate-500">
                      No timeline events available.
                    </div>
                  ) : (
                    workspace!.recent_activity.map((event, index) => (
                      <div
                        key={`${event.event_type}-${index}`}
                        className="rounded-lg bg-slate-950/55 border border-slate-800 p-3"
                      >
                        <div className="flex items-center justify-between gap-3 text-xs">
                          <div className="text-slate-200">{event.summary}</div>
                          <div className="text-slate-500">
                            {event.occurred_at
                              ? formatInTz(event.occurred_at, "MMM d HH:mm")
                              : "unknown"}
                          </div>
                        </div>
                        <div className="text-xxs text-slate-500 mt-1">
                          {event.event_type.replace(/_/g, " ")}
                          {event.actor ? ` · ${event.actor}` : ""}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}

          {workspaceSubTab === "resources" && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="dashboard-surface p-4 md:col-span-2">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div>
                    <h2 className="text-sm font-medium text-white">
                      Git Workspace
                    </h2>
                    <p className="text-xs text-slate-500 mt-1">
                      Source code lives in the project-scoped tool workspace and is managed through the Git adapter.
                    </p>
                  </div>
                  {workspace?.repository && (
                    <button
                      type="button"
                      onClick={() => handleRepositoryAction("sync")}
                      disabled={
                        !!actionLoading ||
                        !workspace.repository.initialized ||
                        !workspace.repository.remote
                      }
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-blue-300 border border-blue-700/60 bg-blue-500/10 rounded-lg hover:bg-blue-500/20 disabled:opacity-50"
                    >
                      <RefreshCw size={12} />
                      {actionLoading === "repository-sync" ? "Syncing…" : "Sync"}
                    </button>
                  )}
                </div>
                {repositoryError && (
                  <div className="mb-3 rounded-lg border border-rose-800/70 bg-rose-950/30 px-3 py-2 text-xs text-rose-200">
                    {repositoryError}
                  </div>
                )}
                {!workspace?.repository ? (
                  <div className="text-xs text-slate-500">
                    No managed Git workspace is configured for this project.
                  </div>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                    <div>
                      <div className="text-slate-500">Location</div>
                      <div className="text-slate-200 mt-1 break-all">
                        {workspace.repository.workspace_path || workspace.repository.workspace_relative_path || "pending"}
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500">Status</div>
                      <div className="text-slate-200 mt-1">
                        {workspace.repository.status || "unknown"}
                        {workspace.repository.clean === false && " · uncommitted changes"}
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500">Remote</div>
                      <div className="text-slate-200 mt-1 break-all">
                        {workspace.repository.remote || workspace.repository.repository_url || "local repository"}
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500">Branch / commit</div>
                      <div className="text-slate-200 mt-1 break-all">
                        {workspace.repository.branch || "unknown"}
                        {workspace.repository.head ? ` · ${workspace.repository.head.slice(0, 12)}` : ""}
                      </div>
                    </div>
                  </div>
                )}
              </div>
              <div className="dashboard-surface p-4">
                <h2 className="text-sm font-medium text-white mb-3">
                  Artifacts
                </h2>
                <div className="space-y-2 max-h-72 overflow-y-auto">
                  {(workspace?.artifacts ?? []).length === 0 ? (
                    <div className="text-xs text-slate-500">
                      No artifacts registered for this project.
                    </div>
                  ) : (
                    workspace!.artifacts.map((artifact) => (
                      <div
                        key={`${artifact.id}-${artifact.path}`}
                        className="rounded-lg bg-slate-950/55 border border-slate-800 p-3 text-xs"
                      >
                        <div className="text-slate-200 break-all">
                          {artifact.path}
                        </div>
                        <div className="text-slate-500 mt-1">
                          {artifact.agent_id || "unknown agent"}
                          {artifact.size_bytes
                            ? ` · ${(artifact.size_bytes / 1024).toFixed(1)} KB`
                            : ""}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="dashboard-surface p-4">
                <h2 className="text-sm font-medium text-white mb-3">
                  Worker Activity
                </h2>
                <div className="space-y-2 max-h-72 overflow-y-auto">
                  {(workspace?.worker_activity ?? []).length === 0 ? (
                    <div className="text-xs text-slate-500">
                      No project-scoped worker activity yet.
                    </div>
                  ) : (
                    workspace!.worker_activity.map((task, index) => (
                      <div
                        key={`${task.task_id}-${index}`}
                        className="rounded-lg bg-slate-950/55 border border-slate-800 p-3 text-xs"
                      >
                        <div className="text-slate-200">
                          {task.agent_id || "agent"}
                        </div>
                        <div className="text-slate-500 mt-1">
                          {task.team_id || "team"} · {task.status || "unknown"}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="dashboard-surface p-4 md:col-span-2">
                <h2 className="text-sm font-medium text-white mb-3">
                  Project Logs
                </h2>
                <div className="space-y-2 max-h-72 overflow-y-auto">
                  {(workspace?.logs ?? []).length === 0 ? (
                    <div className="text-xs text-slate-500">
                      Project-scoped log filtering is not available from the
                      current container log source.
                    </div>
                  ) : (
                    workspace!.logs.map((log, index) => (
                      <div
                        key={`${log.id ?? log.created_at ?? "log"}-${index}`}
                        className="rounded-lg bg-slate-950/55 border border-slate-800 p-3 text-xs"
                      >
                        <div className="text-slate-200">
                          {log.message ?? "log entry"}
                        </div>
                        <div className="text-slate-500 mt-1">
                          {log.level ?? "info"}
                          {log.created_at
                            ? ` · ${formatInTz(log.created_at, "MMM d HH:mm")}`
                            : ""}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}

          {workspaceSubTab === "cost" && (
            <div className="dashboard-surface p-4">
              <h2 className="text-sm font-medium text-white mb-3">
                Cost And Usage
              </h2>
              {workspace?.cost_usage?.available ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
                  <KpiCard
                    label="Total cost"
                    value={`$${workspace.cost_usage.total_cost_usd ?? 0}`}
                    hint="USD since project start"
                    icon="wallet"
                    tone="warning"
                  />
                  <KpiCard
                    label="LLM calls"
                    value={workspace.cost_usage.llm_calls ?? 0}
                    hint="model invocations"
                    icon="brain"
                    tone="info"
                  />
                  <KpiCard
                    label="Tool calls"
                    value={workspace.cost_usage.tool_calls ?? 0}
                    hint="external tool invocations"
                    icon="wrench"
                    tone="info"
                  />
                  <KpiCard
                    label="Tokens"
                    value={workspace.cost_usage.total_tokens ?? 0}
                    hint="prompt and completion"
                    icon="activity"
                    tone="info"
                  />
                  <KpiCard
                    label="Failed calls"
                    value={workspace.cost_usage.failed_calls ?? 0}
                    hint="LLM or tool failures"
                    icon="shield"
                    tone={
                      (workspace.cost_usage.failed_calls ?? 0) > 0
                        ? "negative"
                        : "positive"
                    }
                  />
                </div>
              ) : (
                <EmptyState
                  icon="activity"
                  title="Usage telemetry unavailable"
                  description={
                    workspace?.cost_usage?.reason ??
                    "Cost and usage data is not available from the current backend."
                  }
                />
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === "evidence" && (
        <div
          id="project-panel-evidence"
          role="tabpanel"
          aria-labelledby="project-tab-evidence"
          className="space-y-4"
        >
          {evidenceLoading && (
            <div className="dashboard-surface p-6 text-center text-sm text-slate-500">
              Loading immutable evidence policy checks…
            </div>
          )}
          {!evidenceLoading && evidence === null && (
            <ErrorBanner tone="error" title="Evidence data unavailable">
              The evidence service did not return a project-scoped policy result.
            </ErrorBanner>
          )}
          {evidence && (
            <>
              <div className="dashboard-surface p-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-medium text-white">Completion Evidence</h2>
                  <p className="mt-1 text-xs text-slate-500">
                    Policy {evidence.policy_id} v{evidence.policy_version} · {Math.round(evidence.completeness_score * 100)}% complete
                  </p>
                </div>
                <span className={clsx(
                  "rounded-full border px-3 py-1 text-xs font-medium",
                  evidence.status === "complete"
                    ? "border-emerald-700/70 bg-emerald-950/40 text-emerald-300"
                    : "border-amber-700/70 bg-amber-950/40 text-amber-200",
                )}>
                  {evidence.status}
                </span>
              </div>
              <div className="space-y-2">
                {evidence.checks.map((check) => (
                  <div key={check.name} className={clsx(
                    "dashboard-surface flex items-start gap-3 p-4",
                    check.required && !check.passed && "border-amber-800/70",
                  )}>
                    {check.passed ? (
                      <CheckCircle size={18} className="mt-0.5 shrink-0 text-emerald-400" />
                    ) : (
                      <XCircle size={18} className="mt-0.5 shrink-0 text-amber-400" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-100">
                        {check.name.replace(/_/g, " ")}
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xxs font-normal text-slate-400">
                          {check.required ? "required" : "optional"}
                        </span>
                      </div>
                      {check.reason && <p className="mt-1 text-xs text-amber-200">{check.reason}</p>}
                      {check.evidence_refs.length > 0 && (
                        <p className="mt-2 break-all text-xxs text-slate-500">
                          Evidence: {check.evidence_refs.join(", ")}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
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
                <button
                  onClick={() => handleAction("retry")}
                  disabled={!!actionLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-600/20 text-amber-400 border border-amber-700 rounded-lg disabled:opacity-50"
                >
                  <RotateCcw size={12} />{" "}
                  {actionLoading === "retry" ? "..." : "Retry"}
                </button>
                <button
                  onClick={() => handleAction("archive")}
                  disabled={!!actionLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-slate-600/20 text-slate-400 border border-slate-700 rounded-lg disabled:opacity-50"
                >
                  <Archive size={12} />{" "}
                  {actionLoading === "archive" ? "..." : "Archive"}
                </button>
              </>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <h2 className="text-sm font-medium text-slate-300 mb-4">
                State History
              </h2>
              <div className="space-y-0">
                {WORKFLOW_STATES.map((state, i) => {
                  const entry = history.find((h) => h.to_state === state);
                  const isCurrent = project.state === state;
                  const isPast = entry !== undefined;
                  return (
                    <div key={state} className="flex gap-3 group">
                      <div className="flex flex-col items-center">
                        <div
                          className={clsx(
                            "w-3 h-3 rounded-full flex-shrink-0 mt-0.5",
                            isCurrent
                              ? STATE_COLORS[state]
                              : isPast
                                ? "bg-slate-600"
                                : "bg-slate-800 border border-slate-700",
                          )}
                        />
                        {i < WORKFLOW_STATES.length - 1 && (
                          <div
                            className={clsx(
                              "w-px flex-1 my-0.5",
                              isPast ? "bg-slate-700" : "bg-slate-800",
                            )}
                          />
                        )}
                      </div>
                      <div className="pb-3">
                        <div
                          className={clsx(
                            "text-xs font-medium",
                            isCurrent
                              ? "text-white"
                              : isPast
                                ? "text-slate-400"
                                : "text-slate-700",
                          )}
                        >
                          {state.replace(/_/g, " ")}
                        </div>
                        {entry && (
                          <div className="text-xxs text-slate-600">
                            {formatInTz(
                              entry.transitioned_at,
                              "MMM d HH:mm:ss",
                            )}
                            {entry.triggered_by && ` · ${entry.triggered_by}`}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="space-y-4">
              {decisions.length > 0 && (
                <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-amber-300 mb-3">
                    Pending Decisions ({decisions.length})
                  </h2>
                  <div className="space-y-3">
                    {decisions.map((d) => (
                      <div
                        key={d.id}
                        className="bg-slate-900 rounded-lg p-3 border border-slate-800"
                      >
                        <div className="text-xs font-medium text-slate-200 mb-1">
                          {d.gate_type ?? d.decision_type ?? "approval"}
                        </div>
                        <p className="text-xs text-slate-400 mb-2">
                          {d.prompt ?? "Operator decision required."}
                        </p>
                        {Boolean(d.context) && (
                          <button
                            onClick={() =>
                              setExpandedDecision(
                                expandedDecision === d.id ? null : d.id,
                              )
                            }
                            className="text-xxs text-blue-400 mb-2"
                          >
                            {expandedDecision === d.id
                              ? "Hide context"
                              : "Show context"}
                          </button>
                        )}
                        {expandedDecision === d.id && (
                          <pre className="text-xxs text-slate-500 bg-slate-950 rounded p-2 overflow-x-auto mb-2">
                            {JSON.stringify(d.context, null, 2)}
                          </pre>
                        )}
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleDecision(d.id, "APPROVED")}
                            disabled={!!actionLoading}
                            className="flex items-center gap-1 px-2 py-1 bg-green-600/20 text-green-400 border border-green-800 text-xs rounded disabled:opacity-50"
                          >
                            <CheckCircle size={11} />
                            {actionLoading === `${d.id}-APPROVED`
                              ? "..."
                              : "Approve"}
                          </button>
                          <button
                            onClick={() => handleDecision(d.id, "REJECTED")}
                            disabled={!!actionLoading}
                            className="flex items-center gap-1 px-2 py-1 bg-red-600/20 text-red-400 border border-red-800 text-xs rounded disabled:opacity-50"
                          >
                            <XCircle size={11} />
                            Reject
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-slate-300 mb-3">
                  Project History
                </h2>
                <div
                  className="space-y-2 max-h-64 overflow-y-auto"
                  data-testid="project-history-list"
                >
                  {history.length === 0 ? (
                    <div className="text-xs text-slate-500">
                      No history yet.
                    </div>
                  ) : (
                    history.map((entry, index) => (
                      <div
                        key={`${entry.transitioned_at}-${index}`}
                        className="rounded-lg bg-slate-950 border border-slate-800 p-3"
                      >
                        <div className="flex items-center justify-between gap-3 text-xs">
                          <div className="text-slate-200">
                            {(entry.event || "transition").replace(/_/g, " ")}
                          </div>
                          <div className="text-slate-500">
                            {formatInTz(
                              entry.transitioned_at,
                              "MMM d HH:mm:ss",
                            )}
                          </div>
                        </div>
                        <div className="text-xxs text-slate-500 mt-1">
                          {(entry.from_state || entry.to_state).replace(
                            /_/g,
                            " ",
                          )}{" "}
                          → {entry.to_state.replace(/_/g, " ")}
                          {entry.triggered_by && ` · ${entry.triggered_by}`}
                        </div>
                        {entry.payload && (
                          <div className="text-xxs text-slate-400 mt-1">
                            {entry.payload.to_node_id
                              ? `Override to ${String(entry.payload.to_node_id)}${entry.payload.reason ? `: ${String(entry.payload.reason)}` : ""}`
                              : JSON.stringify(entry.payload)}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-slate-300 mb-3">
                  Monitor
                </h2>
                <div className="space-y-2">
                  <Link
                    href="/ceo"
                    className="flex items-center gap-2 text-xs text-blue-400"
                  >
                    → CEO Live Feed
                  </Link>
                  <Link
                    href="/streams"
                    className="flex items-center gap-2 text-xs text-blue-400"
                  >
                    → All Agent Streams
                  </Link>
                  <Link
                    href="/logs"
                    className="flex items-center gap-2 text-xs text-blue-400"
                  >
                    → Container Logs
                  </Link>
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
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
              <div className="text-center">
                <GitBranch size={32} className="mx-auto text-slate-600 mb-3" />
                <p className="text-slate-400 text-sm">
                  No flow attached to this project.
                </p>
                <p className="text-slate-500 text-xs mt-1 mb-4">
                  Select a flow below to start orchestrating this project.
                </p>
              </div>
              {actionLoading === "load-flows" ? (
                <div className="text-sm text-slate-500 py-4 text-center">
                  Loading flows...
                </div>
              ) : (
                <div className="space-y-2 mt-4">
                  {availableFlows.length > 0 ? (
                    availableFlows.map((flow) => (
                      <button
                        key={flow.id}
                        onClick={() => handleAssignFlow(flow.id)}
                        disabled={actionLoading === "assign-flow"}
                        data-testid={`assign-flow-${flow.id}`}
                        className="w-full text-left px-4 py-3 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors"
                      >
                        <div className="text-slate-100 font-medium text-sm">
                          {flow.name}
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">
                          v{flow.version} ·{" "}
                          {flow.description || "No description"}
                        </div>
                      </button>
                    ))
                  ) : (
                    <div className="text-sm text-slate-500 py-4 text-center">
                      No active flows available.{" "}
                      <Link href="/flows" className="text-blue-400">
                        Create one first →
                      </Link>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span
                    className={clsx(
                      "px-2 py-1 rounded text-xs font-medium",
                      FLOW_STATUS_COLORS[
                        flowInstance.status as FlowInstanceStatus
                      ],
                    )}
                  >
                    {flowInstance.status}
                  </span>
                  <span className="text-xs text-slate-500">
                    v{flowInstance.flow_version}
                  </span>
                </div>
                <div className="flex gap-2">
                  {flowInstance.status === "NOT_STARTED" && (
                    <button
                      onClick={() => handleFlowAction("start")}
                      disabled={!!actionLoading}
                      data-testid="flow-start-button"
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg"
                    >
                      <Play size={12} /> Start
                    </button>
                  )}
                  {flowInstance.status === "RUNNING" && (
                    <>
                      <button
                        onClick={() => handleFlowAction("pause")}
                        disabled={!!actionLoading}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-yellow-600/20 text-yellow-400 border border-yellow-800 rounded-lg"
                      >
                        <Pause size={12} /> Pause
                      </button>
                      <button
                        onClick={() => handleFlowAction("cancel")}
                        disabled={!!actionLoading}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-red-600/20 text-red-400 border border-red-800 rounded-lg"
                      >
                        <StopCircle size={12} /> Cancel
                      </button>
                    </>
                  )}
                  {flowInstance.status === "PAUSED" && (
                    <>
                      <button
                        onClick={() => handleFlowAction("resume")}
                        disabled={!!actionLoading}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg"
                      >
                        <Play size={12} /> Resume
                      </button>
                      <button
                        onClick={() => handleFlowAction("cancel")}
                        disabled={!!actionLoading}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-red-600/20 text-red-400 border border-red-800 rounded-lg"
                      >
                        <StopCircle size={12} /> Cancel
                      </button>
                    </>
                  )}
                  {flowInstance.status === "WAITING_APPROVAL" && (
                    <button
                      onClick={() => handleFlowAction("resume")}
                      disabled={!!actionLoading}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg"
                    >
                      <Play size={12} /> Resume
                    </button>
                  )}
                  {(flowInstance.status === "FAILED" ||
                    flowInstance.status === "CANCELLED") && (
                    <button
                      onClick={handleRetry}
                      disabled={!!actionLoading}
                      data-testid="flow-retry-button"
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-600/20 text-amber-400 border border-amber-800 rounded-lg"
                    >
                      <RotateCcw size={12} /> Retry
                    </button>
                  )}
                  <button
                    onClick={openFlowSwitchModal}
                    disabled={!!actionLoading}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-purple-600/20 text-purple-400 border border-purple-800 rounded-lg"
                  >
                    <ArrowRightCircle size={12} /> Switch Flow
                  </button>
                </div>
              </div>

              <div
                className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden"
                style={{ height: "400px" }}
              >
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={flowNodeTypes}
                  fitView
                  className="bg-slate-950"
                >
                  <Background color="#374151" gap={16} />
                  <Controls className="!bg-slate-800 !border-slate-700" />
                </ReactFlow>
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-slate-300 mb-3">
                    Current Node
                  </h2>
                  <div className="space-y-2">
                    {(flowInstance.active_node_ids || []).length === 0 ? (
                      <div className="text-xs text-slate-500">
                        No active node yet.
                      </div>
                    ) : (
                      flowInstance.active_node_ids.map((nodeId) => {
                        const node = flowDefinition?.nodes.find(
                          (item) => item.id === nodeId,
                        );
                        return (
                          <div
                            key={nodeId}
                            className="rounded-lg bg-slate-950 border border-slate-800 p-3"
                            data-testid="current-node-card"
                          >
                            <div
                              className="text-sm text-white"
                              data-testid="current-node-label"
                            >
                              {node?.label || nodeId}
                            </div>
                            <div className="text-xxs text-slate-500 mt-1">
                              {node?.type || "task"}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-slate-300 mb-3">
                    Past Transitions
                  </h2>
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {nodeExecutions.filter((exec) => exec.status !== "RUNNING")
                      .length === 0 ? (
                      <div className="text-xs text-slate-500">
                        No completed steps yet.
                      </div>
                    ) : (
                      nodeExecutions
                        .filter((exec) => exec.status !== "RUNNING")
                        .map((exec, index) => (
                          <div
                            key={`${exec.id || exec.node_id}-${index}`}
                            className="rounded-lg bg-slate-950 border border-slate-800 p-3 text-xs"
                          >
                            <div className="text-slate-200">
                              {exec.node_label || exec.node_id}
                            </div>
                            <div className="text-slate-500 mt-1">
                              {exec.status}
                              {exec.completed_at
                                ? ` · ${formatInTz(exec.completed_at, "HH:mm:ss")}`
                                : ""}
                            </div>
                          </div>
                        ))
                    )}
                  </div>
                </div>
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-slate-300 mb-3">
                    Next Possible Transitions
                  </h2>
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {nextPossibleTransitions.length === 0 ? (
                      <div className="text-xs text-slate-500">
                        No outgoing transitions available.
                      </div>
                    ) : (
                      nextPossibleTransitions.map((transition) => (
                        <div
                          key={transition.edgeId}
                          className="rounded-lg bg-slate-950 border border-slate-800 p-3 text-xs"
                        >
                          <div className="text-slate-200">
                            {transition.targetLabel}
                          </div>
                          <div className="text-slate-500 mt-1">
                            From {transition.sourceId}
                            {transition.condition
                              ? ` · ${transition.condition}`
                              : ""}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>

              {(flowInstance.escalated_to ||
                flowInstance.escalation_reason) && (
                <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-amber-300 mb-2">
                    Escalation
                  </h2>
                  <div className="text-xs text-amber-100">
                    {flowInstance.escalated_to
                      ? `Escalated to ${flowInstance.escalated_to}`
                      : "Escalation recorded"}
                  </div>
                  {flowInstance.escalation_reason && (
                    <div className="text-xxs text-amber-200/80 mt-1">
                      {flowInstance.escalation_reason}
                    </div>
                  )}
                </div>
              )}

              {(flowInstance.status === "RUNNING" ||
                flowInstance.status === "WAITING_APPROVAL") &&
                flowInstance.active_node_ids?.length > 0 && (
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                    <h2 className="text-sm font-medium text-slate-300 mb-3">
                      Active Nodes
                    </h2>
                    <div className="space-y-2">
                      {flowInstance.active_node_ids.map((nodeId) => {
                        const activeNode = flowDefinition?.nodes.find(
                          (node) => node.id === nodeId,
                        );
                        const nodeExec = nodeExecutions.find(
                          (e) => e.node_id === nodeId && e.status === "RUNNING",
                        );
                        const governedTask =
                          activeNode?.type === "task" &&
                          typeof activeNode.config?.worker_id === "string";
                        return (
                          <div
                            key={nodeId}
                            className="flex items-center justify-between bg-slate-800 rounded-lg p-3"
                          >
                            <div>
                              <div className="text-sm font-medium text-white">
                                {activeNode?.label || nodeId}
                              </div>
                              <div className="text-xxs text-slate-500 mt-0.5">
                                {activeNode?.type || "task"}
                              </div>
                              {nodeExec && (
                                <div className="text-xs text-slate-500 mt-0.5">
                                  Running...
                                </div>
                              )}
                            </div>
                            <div className="flex gap-2">
                              {activeNode?.type === "approval" ? (
                                <>
                                  <button
                                    onClick={() =>
                                      handleNodeAction(nodeId, "complete", {
                                        decision: "approved",
                                        approved: true,
                                      })
                                    }
                                    disabled={!!actionLoading}
                                    data-testid="approval-approve-button"
                                    className="px-2 py-1 text-xs bg-green-600/20 text-green-400 border border-green-800 rounded hover:bg-green-600/40"
                                  >
                                    Approve
                                  </button>
                                  <button
                                    onClick={() =>
                                      handleNodeAction(nodeId, "complete", {
                                        decision: "edit_requested",
                                      })
                                    }
                                    disabled={!!actionLoading}
                                    data-testid="approval-edit-requested-button"
                                    className="px-2 py-1 text-xs bg-blue-600/20 text-blue-400 border border-blue-800 rounded hover:bg-blue-600/40"
                                  >
                                    Request Edit
                                  </button>
                                  <button
                                    onClick={() =>
                                      handleNodeAction(nodeId, "complete", {
                                        decision: "rejected",
                                        approved: false,
                                      })
                                    }
                                    disabled={!!actionLoading}
                                    data-testid="approval-reject-button"
                                    className="px-2 py-1 text-xs bg-red-600/20 text-red-400 border border-red-800 rounded hover:bg-red-600/40"
                                  >
                                    Reject
                                  </button>
                                </>
                              ) : governedTask ? (
                                <>
                                  <button
                                    onClick={() =>
                                      handleNodeAction(nodeId, "advance")
                                    }
                                    disabled={!!actionLoading}
                                    data-testid="dispatch-worker-run-button"
                                    className="px-2 py-1 text-xs bg-blue-600/20 text-blue-300 border border-blue-800 rounded hover:bg-blue-600/40"
                                  >
                                    Dispatch Worker Run
                                  </button>
                                  <span className="self-center text-xxs text-slate-500">
                                    Result evidence advances this node
                                  </span>
                                </>
                              ) : (
                                <>
                                  <button
                                    onClick={() =>
                                      handleNodeAction(nodeId, "complete")
                                    }
                                    disabled={!!actionLoading}
                                    data-testid="complete-node-button"
                                    className="px-2 py-1 text-xs bg-green-600/20 text-green-400 border border-green-800 rounded hover:bg-green-600/40"
                                  >
                                    Complete
                                  </button>
                                  <button
                                    onClick={() =>
                                      handleNodeAction(nodeId, "timeout", {
                                        error: "Timed out waiting for analysis",
                                      })
                                    }
                                    disabled={!!actionLoading}
                                    data-testid="timeout-node-button"
                                    className="px-2 py-1 text-xs bg-amber-600/20 text-amber-400 border border-amber-800 rounded hover:bg-amber-600/40"
                                  >
                                    Timeout
                                  </button>
                                  <button
                                    onClick={() =>
                                      handleNodeAction(nodeId, "fail")
                                    }
                                    disabled={!!actionLoading}
                                    data-testid="fail-node-button"
                                    className="px-2 py-1 text-xs bg-red-600/20 text-red-400 border border-red-800 rounded hover:bg-red-600/40"
                                  >
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

              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <h2 className="text-sm font-medium text-slate-300 mb-3">
                  Manual Override
                </h2>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <select
                    value={overrideNodeId}
                    onChange={(e) => setOverrideNodeId(e.target.value)}
                    data-testid="override-node-select"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white"
                  >
                    {overrideableNodes.map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.label}
                      </option>
                    ))}
                  </select>
                  <input
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    placeholder="Reason for override"
                    data-testid="override-reason-input"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white"
                  />
                  <button
                    onClick={handleOverrideFlowNode}
                    disabled={
                      !overrideNodeId || actionLoading === "override-flow-node"
                    }
                    data-testid="override-node-button"
                    className="px-3 py-2 text-xs font-medium bg-amber-600/20 text-amber-400 border border-amber-800 rounded-lg hover:bg-amber-600/40 disabled:opacity-50"
                  >
                    {actionLoading === "override-flow-node"
                      ? "Overriding..."
                      : "Override Node"}
                  </button>
                </div>
                <p className="text-xxs text-slate-500 mt-2">
                  Overrides are logged into project history for audit
                  visibility.
                </p>
              </div>

              {nodeExecutions.length > 0 && (
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                  <h2 className="text-sm font-medium text-slate-300 mb-3">
                    Execution History
                  </h2>
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {nodeExecutions.map((exec, i) => (
                      <div
                        key={exec.id || i}
                        className="flex items-center gap-3 text-xs"
                      >
                        <div
                          className={clsx(
                            "w-2 h-2 rounded-full",
                            exec.status === "COMPLETED"
                              ? "bg-green-500"
                              : exec.status === "FAILED"
                                ? "bg-red-500"
                                : "bg-slate-600",
                          )}
                        />
                        <div className="flex-1 text-slate-300">
                          {exec.node_label || exec.node_id}
                        </div>
                        <div
                          className={clsx(
                            "px-1.5 py-0.5 rounded",
                            exec.status === "COMPLETED"
                              ? "bg-green-900/50 text-green-400"
                              : exec.status === "FAILED"
                                ? "bg-red-900/50 text-red-400"
                                : "bg-slate-800 text-slate-400",
                          )}
                        >
                          {exec.status}
                        </div>
                        <div className="text-slate-500">
                          {exec.completed_at
                            ? formatInTz(exec.completed_at, "HH:mm:ss")
                            : formatInTz(exec.started_at, "HH:mm:ss")}
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
            <div className="flex items-center gap-3">
              <div className="text-sm text-slate-400">
                {contextItems.length} context item
                {contextItems.length === 1 ? "" : "s"} attached to this project
                {generatedDocumentCount > 0 && (
                  <span className="text-xs text-slate-500">
                    · {generatedDocumentCount} generated document
                    {generatedDocumentCount === 1 ? "" : "s"}
                  </span>
                )}
              </div>
              {contextItemIds.length > 0 && (
                <button
                  type="button"
                  onClick={contextSelection.toggleAll}
                  className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                >
                  {contextSelection.isAllSelected
                    ? "Clear selection"
                    : "Select all"}
                </button>
              )}
            </div>
            <button
              onClick={() => setShowContextUpload(!showContextUpload)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600/20 text-blue-400 border border-blue-700 rounded-lg hover:bg-blue-600/40"
            >
              <Plus size={12} />
              Add Item
            </button>
          </div>

          {bulkContextError && (
            <div className="rounded-lg border border-amber-800 bg-amber-950/30 px-4 py-2 text-sm text-amber-200">
              {bulkContextError}
            </div>
          )}

          {contextSelection.selectedCount > 0 && (
            <BulkActionBar
              selectedCount={contextSelection.selectedCount}
              totalCount={contextItemIds.length}
              loading={bulkContextDeleting}
              action="delete"
              onAction={handleBulkDeleteContextItems}
              onClear={contextSelection.clear}
            />
          )}

          {showContextUpload && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-slate-500 mb-1">
                    Type
                  </label>
                  <select
                    value={newContextType}
                    onChange={(e) =>
                      setNewContextType(
                        e.target.value as "FILE" | "URL" | "TEXT",
                      )
                    }
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                  >
                    <option value="FILE">File Attachment</option>
                    <option value="URL">URL Link</option>
                    <option value="TEXT">Text Note</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">
                    Name
                  </label>
                  <input
                    value={newContextName}
                    onChange={(e) => setNewContextName(e.target.value)}
                    placeholder="Item name..."
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                  />
                </div>
              </div>

              {newContextType === "URL" && (
                <div>
                  <label className="block text-xs text-slate-500 mb-1">
                    URL
                  </label>
                  <input
                    value={newContextUrl}
                    onChange={(e) => setNewContextUrl(e.target.value)}
                    placeholder="https://..."
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                  />
                </div>
              )}

              {newContextType === "TEXT" && (
                <div>
                  <label className="block text-xs text-slate-500 mb-1">
                    Content
                  </label>
                  <textarea
                    value={newContextText}
                    onChange={(e) => setNewContextText(e.target.value)}
                    placeholder="Enter text content..."
                    rows={4}
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                  />
                </div>
              )}

              {newContextType === "FILE" && (
                <div className="border border-dashed border-slate-700 rounded-lg p-4 text-center">
                  <Upload size={24} className="mx-auto text-slate-500 mb-2" />
                  <p className="text-xs text-slate-500">
                    File upload UI would go here
                  </p>
                  <p className="text-xxs text-slate-600 mt-1">
                    For now, enter file details manually
                  </p>
                </div>
              )}

              <div>
                <label className="block text-xs text-slate-500 mb-1">
                  Tags (comma-separated)
                </label>
                <input
                  value={newContextTags}
                  onChange={(e) => setNewContextTags(e.target.value)}
                  placeholder="requirements, architecture, notes"
                  className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                />
              </div>

              <div className="flex gap-2">
                <button
                  onClick={handleAddContextItem}
                  disabled={
                    actionLoading === "add-context" || !newContextName.trim()
                  }
                  className="px-3 py-1.5 text-xs font-medium bg-green-600/20 text-green-400 border border-green-800 rounded-lg hover:bg-green-600/40 disabled:opacity-50"
                >
                  {actionLoading === "add-context" ? "Adding..." : "Add Item"}
                </button>
                <button
                  onClick={() => setShowContextUpload(false)}
                  className="px-3 py-1.5 text-xs font-medium bg-slate-600/20 text-slate-400 border border-slate-700 rounded-lg"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {contextLoading ? (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 text-center text-slate-500 text-sm">
              Loading context items...
            </div>
          ) : contextItems.length === 0 ? (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 text-center">
              <FileText size={32} className="mx-auto text-slate-600 mb-3" />
              <p className="text-slate-400 text-sm">No context items yet</p>
              <p className="text-slate-500 text-xs mt-1">
                Add files, URLs, or notes to build project context
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {contextItems.map((item) => {
                const isSelected = contextSelection.selected.has(item.id);
                const itemType = String(item.item_type || "TEXT").toUpperCase();
                const isGeneratedDocument = Boolean(item.read_only || item.source === "document");
                return (
                  <div
                    key={item.id}
                    className={clsx(
                      "bg-slate-900 border rounded-xl p-4 transition-colors",
                      isGeneratedDocument && "border-indigo-800/80 bg-indigo-950/10",
                      isSelected
                        ? "border-blue-500/60 bg-blue-950/30"
                        : !isGeneratedDocument && "border-slate-800",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div className="pt-1">
                        {isGeneratedDocument ? (
                          <span
                            className="inline-flex h-4 w-4 items-center justify-center text-indigo-400"
                            title="Generated documents are read-only here"
                            aria-label="Generated document"
                          >
                            <FileText size={14} />
                          </span>
                        ) : (
                          <RowCheckbox
                            checked={isSelected}
                            onChange={() => contextSelection.toggle(item.id)}
                            ariaLabel={`Select ${item.name}`}
                          />
                        )}
                      </div>
                      <div
                        className={clsx(
                          "p-2 rounded-lg",
                          itemType === "DOCUMENT"
                            ? "bg-indigo-900/30 text-indigo-300"
                            : itemType === "FILE"
                            ? "bg-blue-900/30 text-blue-400"
                            : itemType === "URL"
                              ? "bg-purple-900/30 text-purple-400"
                              : "bg-amber-900/30 text-amber-400",
                        )}
                      >
                        {itemType === "URL" ? (
                          <LinkIcon size={16} />
                        ) : (
                          <FileText size={16} />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-white truncate">
                          {item.name}
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">
                          {isGeneratedDocument
                            ? `${item.doc_type || itemType} · v${item.version || 1}`
                            : itemType}
                          {item.size_bytes &&
                            ` · ${(item.size_bytes / 1024).toFixed(1)} KB`}
                          {isGeneratedDocument && item.status && (
                            <span className="ml-2 rounded bg-indigo-900/40 px-1.5 py-0.5 text-indigo-300">
                              {item.status.replace(/_/g, " ")}
                            </span>
                          )}
                        </div>
                        {item.description && (
                          <div className="text-xs text-slate-400 mt-1 line-clamp-2">
                            {item.description}
                          </div>
                        )}
                        {item.tags && item.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {item.tags.map((tag) => (
                              <span
                                key={tag}
                                className="px-1.5 py-0.5 bg-slate-800 text-slate-400 text-xxs rounded"
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                        {isGeneratedDocument && item.blob_key && (
                          <div className="mt-2 truncate text-xxs text-indigo-300/70" title={item.blob_key}>
                            {item.blob_key}
                          </div>
                        )}
                      </div>
                      {!isGeneratedDocument && (
                        <button
                          onClick={() => handleDeleteContextItem(item.id)}
                          disabled={actionLoading === `delete-${item.id}`}
                          title="Delete"
                          className="p-1 text-slate-500 hover:text-red-400"
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {showFlowSwitch && flowInstance && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-semibold text-white mb-4">
              Switch Flow
            </h2>
            <p className="text-sm text-slate-400 mb-4">
              Select a new flow to replace the current one.
            </p>
            {actionLoading === "load-flows" ? (
              <div className="text-sm text-slate-500 py-4 text-center">
                Loading flows...
              </div>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {availableFlows
                  .filter((f) => f.id !== flowInstance.flow_id)
                  .map((flow) => (
                    <button
                      key={flow.id}
                      onClick={() => handleSwitchFlow(flow.id)}
                      disabled={actionLoading === "switch-flow"}
                      className="w-full text-left px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-sm"
                    >
                      <div className="text-slate-100 font-medium">
                        {flow.name}
                      </div>
                      <div className="text-xs text-slate-500">
                        v{flow.version}
                      </div>
                    </button>
                  ))}
                {availableFlows.filter((f) => f.id !== flowInstance.flow_id)
                  .length === 0 && (
                  <div className="text-sm text-slate-500 py-4 text-center">
                    No other active flows available
                  </div>
                )}
              </div>
            )}
            <button
              onClick={() => setShowFlowSwitch(false)}
              className="mt-4 w-full px-3 py-2 border border-slate-700 rounded-lg text-sm text-slate-400 hover:text-slate-100"
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
  nodes: {
    id: string;
    type: string;
    label: string;
    config: Record<string, unknown>;
    position?: { x: number; y: number };
  }[],
  edges: { id: string; source: string; target: string; condition?: string }[],
  activeNodeIds: string[],
  executions: FlowNodeExecution[],
): { nodes: Node[]; edges: Edge[] } {
  const nodeStatus: Record<string, string> = {};
  executions.forEach((e) => {
    nodeStatus[e.node_id] = e.status;
  });

  const flowNodes: Node[] = nodes.map((n) => ({
    id: n.id,
    type: "flowNode",
    position: n.position || { x: Math.random() * 400, y: Math.random() * 400 },
    data: {
      label: n.label,
      type: n.type,
      status: activeNodeIds.includes(n.id) ? "RUNNING" : nodeStatus[n.id],
    },
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
