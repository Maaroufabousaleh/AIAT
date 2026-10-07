"use client";

import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type FormEvent as ReactFormEvent,
} from "react";
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
  MessageSquare,
  Save,
  X,
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
  CanonicalIssueCommentRequest,
  CanonicalIssueCreateRequest,
  CanonicalIssueUpdateRequest,
} from "@/lib/generated/orchestrator-api";
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

type HttpError = Error & { status?: number };

type WorkspaceSubTab = "activity" | "resources" | "cost";

const WORKSPACE_SUB_TABS: readonly WorkspaceSubTab[] = [
  "activity",
  "resources",
  "cost",
];

const MAX_INLINE_TEXT_FILE_BYTES = 100_000;
const SUPPORTED_TEXT_FILE_EXTENSIONS = new Set([
  ".txt",
  ".md",
  ".rst",
  ".csv",
  ".json",
  ".yaml",
  ".yml",
  ".xml",
  ".html",
  ".css",
  ".js",
  ".jsx",
  ".ts",
  ".tsx",
  ".py",
  ".toml",
  ".ini",
  ".log",
  ".sql",
  ".sh",
]);

const PROJECT_ISSUE_STATUS_OPTIONS = [
  "backlog", "in_progress", "in_review", "done", "blocked", "cancelled",
];
const PROJECT_ISSUE_PRIORITY_OPTIONS = ["critical", "high", "medium", "low"];
const PROJECT_ISSUE_TYPE_OPTIONS = ["feature", "test", "qa", "docs", "infra", "bugfix", "bug", "rework"];

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

interface EvidencePackageCategory {
  category: string;
  status: string;
  required: boolean;
  item_count: number;
  evidence_refs: string[];
  reason?: string | null;
}

interface ProjectEvidencePackage {
  schema_version: string;
  status: string;
  completeness_score: number;
  categories: EvidencePackageCategory[];
  notices: Array<{ artifact_id: string; field: string; value: string }>;
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

interface ProjectWorkerRun {
  id: string;
  project_id: string;
  worker_id?: string;
  task_type?: string;
  state: string;
  model_resolution_snapshot_id?: string | null;
  created_at?: string;
}

interface ProjectIssue {
  issue_id: string;
  project_id: string;
  title: string;
  status: string;
  issue_type?: string;
  priority?: string;
  assigned_team?: string | null;
  updated_at?: string;
  revision?: number;
}

interface CanonicalProjectIssue extends ProjectIssue {
  description?: string | null;
  assigned_agent?: string | null;
  estimated_hours?: number | null;
  actual_hours?: number | null;
  story_points?: number | null;
}

interface ProjectIssueComment {
  comment_id?: string;
  body: string;
  actor_id?: string;
  created_at?: string;
}

interface ProjectIssueDetail {
  issue: CanonicalProjectIssue;
  comments: ProjectIssueComment[];
  links: Array<Record<string, unknown>>;
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
  const [projectStale, setProjectStale] = useState(false);
  const [projectRefreshError, setProjectRefreshError] = useState<string | null>(
    null,
  );
  const [projectLoadError, setProjectLoadError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const hasProjectRef = useRef(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const accessDeniedRef = useRef(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [expandedDecision, setExpandedDecision] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<
    "workspace" | "workflow" | "flow" | "context" | "evidence"
  >("workspace");
  // Sub-tabs within the workspace view let operators jump between the most
  // important slices (next action, project activity, live resources, spend)
  // without scrolling through every card on a tall screen.
  const [workspaceSubTab, setWorkspaceSubTab] =
    useState<WorkspaceSubTab>("activity");

  const handleWorkspaceSubTabKeyDown = useCallback(
    (
      event: ReactKeyboardEvent<HTMLButtonElement>,
      current: WorkspaceSubTab,
    ) => {
      const currentIndex = WORKSPACE_SUB_TABS.indexOf(current);
      let nextIndex: number | null = null;

      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        nextIndex = (currentIndex + 1) % WORKSPACE_SUB_TABS.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        nextIndex =
          (currentIndex - 1 + WORKSPACE_SUB_TABS.length) %
          WORKSPACE_SUB_TABS.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = WORKSPACE_SUB_TABS.length - 1;
      }

      if (nextIndex === null) return;

      event.preventDefault();
      const nextTab = WORKSPACE_SUB_TABS[nextIndex];
      setWorkspaceSubTab(nextTab);
      window.requestAnimationFrame(() => {
        document.getElementById(`workspace-tab-${nextTab}`)?.focus();
      });
    },
    [],
  );
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [projectWorkerRuns, setProjectWorkerRuns] = useState<ProjectWorkerRun[]>([]);
  const [projectWorkerRunsError, setProjectWorkerRunsError] = useState<string | null>(null);
  const [projectIssues, setProjectIssues] = useState<ProjectIssue[]>([]);
  const [projectIssuesError, setProjectIssuesError] = useState<string | null>(null);
  const [issueCreateOpen, setIssueCreateOpen] = useState(false);
  const [issueCreateBusy, setIssueCreateBusy] = useState(false);
  const [issueCreateTitle, setIssueCreateTitle] = useState("");
  const [issueCreateDescription, setIssueCreateDescription] = useState("");
  const [issueCreateType, setIssueCreateType] = useState("feature");
  const [issueCreatePriority, setIssueCreatePriority] = useState("medium");
  const [selectedIssue, setSelectedIssue] = useState<ProjectIssueDetail | null>(null);
  const [issueDetailLoading, setIssueDetailLoading] = useState(false);
  const [issueUpdateBusy, setIssueUpdateBusy] = useState(false);
  const [issueCommentBusy, setIssueCommentBusy] = useState(false);
  const [issueEditTitle, setIssueEditTitle] = useState("");
  const [issueEditDescription, setIssueEditDescription] = useState("");
  const [issueEditStatus, setIssueEditStatus] = useState("");
  const [issueEditPriority, setIssueEditPriority] = useState("");
  const [issueEditTeam, setIssueEditTeam] = useState("");
  const [issueEditAgent, setIssueEditAgent] = useState("");
  const [issueNewComment, setIssueNewComment] = useState("");
  const [issueActionError, setIssueActionError] = useState<string | null>(null);
  const [issueActionNotice, setIssueActionNotice] = useState<string | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceStale, setWorkspaceStale] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const workspaceRef = useRef<WorkspaceSummary | null>(null);
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
  const [evidencePackage, setEvidencePackage] =
    useState<ProjectEvidencePackage | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [showContextUpload, setShowContextUpload] = useState(false);
  const [newContextName, setNewContextName] = useState("");
  const [newContextType, setNewContextType] = useState<"FILE" | "URL" | "TEXT">(
    "FILE",
  );
  const [newContextUrl, setNewContextUrl] = useState("");
  const [newContextText, setNewContextText] = useState("");
  const [newContextFile, setNewContextFile] = useState<File | null>(null);
  const [contextUploadError, setContextUploadError] = useState<string | null>(
    null,
  );
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
  const clearContextSelection = contextSelection.clear;
  const handleAccessDenied = useCallback(() => {
    accessDeniedRef.current = true;
    setAccessDenied(true);
    setProjectStale(false);
    setProjectRefreshError(null);
    setProjectLoadError(null);
    setActionLoading(null);
    setShowFlowSwitch(false);
    setShowFlowActionsMenu(false);
    setShowContextUpload(false);
    setIssueCreateOpen(false);
    setSelectedIssue(null);
    setBulkContextError("");
    clearContextSelection();
  }, [clearContextSelection]);
  const generatedDocumentCount = useMemo(
    () => contextItems.filter((item) => item.read_only).length,
    [contextItems],
  );
  useEffect(() => {
    contextSelection.prune();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextItemIds.join(",")]);

  const load = useCallback(async () => {
    if (accessDeniedRef.current) return;
    setLoading(true);
    setProjectRefreshError(null);
    try {
      const [proj, hist, dec, trans] = await Promise.allSettled([
        fetch(`/api/projects/${id}`).then(async (r) => {
          const payload = await r.json().catch(() => null);
          if (!r.ok) {
            const detail =
              payload && typeof payload === "object"
                ? ((payload as { detail?: unknown; error?: unknown }).detail ??
                  (payload as { detail?: unknown; error?: unknown }).error)
                : null;
            if (r.status === 404) {
              throw new Error(
                "Project was not found. It may have been deleted, archived, or this link may be stale.",
              );
            }
            const error = new Error(
              typeof detail === "string" && detail.trim()
                ? `Project request failed: ${detail}`
                : `Project request failed (HTTP ${r.status})`,
            ) as HttpError;
            error.status = r.status;
            throw error;
          }
          return payload;
        }),
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
      if (proj.status === "fulfilled" && proj.value) {
        setProject(proj.value);
        hasProjectRef.current = true;
        setProjectStale(false);
        setProjectRefreshError(null);
        setProjectLoadError(null);
      } else {
        if (
          proj.status === "rejected" &&
          proj.reason instanceof Error &&
          ((proj.reason as HttpError).status === 401 ||
            (proj.reason as HttpError).status === 403)
        ) {
          handleAccessDenied();
          return;
        }
        const failure =
          proj.status === "rejected" && proj.reason instanceof Error
            ? proj.reason.message
            : "The project could not be loaded from the control plane.";
        if (hasProjectRef.current) {
          setProjectStale(true);
          setProjectRefreshError(
            failure ||
              "The latest project refresh failed; this page may be out of date.",
          );
        } else {
          setProjectLoadError(failure);
        }
      }
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
    } catch (cause) {
      if (hasProjectRef.current) {
        setProjectStale(true);
        setProjectRefreshError(
          cause instanceof Error
            ? cause.message
            : "The latest project refresh failed; this page may be out of date.",
        );
      }
    } finally {
      setLoading(false);
    }
  }, [handleAccessDenied, id]);

  const loadFlowData = useCallback(async () => {
    if (accessDeniedRef.current) return;
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
    if (accessDeniedRef.current) return;
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
    if (accessDeniedRef.current) return;
    setEvidenceLoading(true);
    try {
      const [response, packageResponse] = await Promise.all([
        fetch(`/api/projects/${id}/evidence`),
        fetch(`/api/projects/${id}/evidence/package`),
      ]);
      setEvidence(response.ok ? await response.json() : null);
      setEvidencePackage(
        packageResponse.ok ? await packageResponse.json() : null,
      );
    } catch {
      setEvidence(null);
      setEvidencePackage(null);
    } finally {
      setEvidenceLoading(false);
    }
  }, [id]);

  const loadWorkspace = useCallback(async () => {
    if (accessDeniedRef.current) return;
    setWorkspaceLoading(true);
    try {
      const [res, repositoryRes, workerRunsRes, issuesRes] = await Promise.all([
        fetch(`/api/projects/${id}/workspace`),
        fetch(`/api/projects/${id}/repository`),
        fetch(`/api/governance/runs?project_id=${encodeURIComponent(id)}&limit=50`),
        fetch(`/api/projects/${id}/issues?limit=100`),
      ]);
      if (
        [res, repositoryRes, workerRunsRes, issuesRes].some(
          (response) => response.status === 401 || response.status === 403,
        )
      ) {
        handleAccessDenied();
        return;
      }

      const [workspacePayload, repositoryPayload, workerRunsPayload, issuesPayload] =
        await Promise.all([
          res.json().catch(() => null),
          repositoryRes.ok ? repositoryRes.json().catch(() => null) : null,
          workerRunsRes.ok ? workerRunsRes.json().catch(() => null) : null,
          issuesRes.ok ? issuesRes.json().catch(() => null) : null,
        ]);
      const failures: string[] = [];

      const isProjectWorkerRun = (value: unknown): value is ProjectWorkerRun => {
        if (!value || typeof value !== "object") return false;
        const run = value as Partial<ProjectWorkerRun>;
        return (
          typeof run.id === "string" &&
          typeof run.project_id === "string" &&
          typeof run.state === "string"
        );
      };
      if (!workerRunsRes.ok) {
        setProjectWorkerRuns([]);
        setProjectWorkerRunsError(
          `Worker runs could not be loaded (HTTP ${workerRunsRes.status}).`,
        );
      } else if (!Array.isArray(workerRunsPayload)) {
        setProjectWorkerRuns([]);
        setProjectWorkerRunsError("Worker runs response was not a list.");
      } else {
        const runs = workerRunsPayload.filter(isProjectWorkerRun);
        setProjectWorkerRuns(
          runs.filter((run) => run.project_id.toLowerCase() === id.toLowerCase()),
        );
        setProjectWorkerRunsError(null);
      }

      const isProjectIssue = (value: unknown): value is ProjectIssue => {
        if (!value || typeof value !== "object") return false;
        const issue = value as Partial<ProjectIssue>;
        return (
          typeof issue.issue_id === "string" &&
          typeof issue.project_id === "string" &&
          typeof issue.title === "string" &&
          typeof issue.status === "string"
        );
      };
      if (!issuesRes.ok) {
        setProjectIssues([]);
        setProjectIssuesError(`Project issues could not be loaded (HTTP ${issuesRes.status}).`);
      } else if (!Array.isArray(issuesPayload)) {
        setProjectIssues([]);
        setProjectIssuesError("Project issues response was not a list.");
      } else {
        const issues = issuesPayload.filter(isProjectIssue);
        setProjectIssues(
          issues.filter((issue) => issue.project_id.toLowerCase() === id.toLowerCase()),
        );
        setProjectIssuesError(null);
      }

      if (!res.ok) {
        const detail =
          workspacePayload && typeof workspacePayload === "object"
            ? ((workspacePayload as { detail?: unknown; error?: unknown })
                .detail ??
              (workspacePayload as { detail?: unknown; error?: unknown }).error)
            : null;
        failures.push(
          typeof detail === "string" && detail.trim()
            ? `Workspace request failed: ${detail}`
            : `Workspace request failed (HTTP ${res.status})`,
        );
      }
      if (!repositoryRes.ok) {
        const detail =
          repositoryPayload && typeof repositoryPayload === "object"
            ? ((repositoryPayload as { detail?: unknown; error?: unknown })
                .detail ??
              (repositoryPayload as { detail?: unknown; error?: unknown })
                .error)
            : null;
        failures.push(
          typeof detail === "string" && detail.trim()
            ? `Repository request failed: ${detail}`
            : `Repository request failed (HTTP ${repositoryRes.status})`,
        );
      }

      if (
        res.ok &&
        workspacePayload &&
        typeof workspacePayload === "object" &&
        "recent_activity" in workspacePayload
      ) {
        const data = { ...(workspacePayload as WorkspaceSummary) };
        if (repositoryRes.ok) {
          data.repository =
            repositoryPayload && typeof repositoryPayload === "object"
              ? ((repositoryPayload as { workspace?: RepositorySummary | null })
                  .workspace ?? null)
              : null;
        } else if (workspaceRef.current) {
          data.repository = workspaceRef.current.repository;
        }
        setWorkspace(data);
        workspaceRef.current = data;
        setWorkspaceStale(failures.length > 0);
        setWorkspaceError(failures.length > 0 ? failures.join(" ") : null);
      } else {
        const failure =
          failures[0] ??
          "Workspace response was missing the required activity data.";
        if (workspaceRef.current) {
          setWorkspaceStale(true);
          setWorkspaceError(failure);
        } else {
          setWorkspace(null);
          setWorkspaceStale(false);
          setWorkspaceError(failure);
        }
      }
    } catch (err) {
      console.error("Failed to load workspace:", err);
      const failure =
        err instanceof Error
          ? err.message
          : "The project workspace could not be loaded from the control plane.";
      if (workspaceRef.current) {
        setWorkspaceStale(true);
        setWorkspaceError(failure);
      } else {
        setWorkspace(null);
        setWorkspaceStale(false);
        setWorkspaceError(failure);
      }
    } finally {
      setWorkspaceLoading(false);
    }
  }, [handleAccessDenied, id]);

  function handleDeniedResponse(response: Response) {
    if (response.status === 401 || response.status === 403) {
      handleAccessDenied();
      return true;
    }
    return false;
  }

  async function issueErrorMessage(response: Response, fallback: string): Promise<string> {
    const payload = await response.json().catch(() => null);
    const detail = payload && typeof payload === "object"
      ? (payload as { detail?: unknown; error?: unknown }).detail ??
        (payload as { detail?: unknown; error?: unknown }).error
      : null;
    return typeof detail === "string" && detail.trim()
      ? detail.slice(0, 400)
      : `${fallback} (HTTP ${response.status})`;
  }

  async function openIssue(issueId: string) {
    if (accessDeniedRef.current) return;
    setIssueDetailLoading(true);
    setIssueActionError(null);
    setIssueActionNotice(null);
    try {
      const response = await fetch(`/api/projects/${id}/issues/${encodeURIComponent(issueId)}`, { cache: "no-store" });
      if (handleDeniedResponse(response)) return;
      if (!response.ok) throw new Error(await issueErrorMessage(response, "Issue details could not be loaded"));
      const payload = await response.json() as ProjectIssueDetail;
      if (!payload?.issue || payload.issue.project_id.toLowerCase() !== id.toLowerCase() || payload.issue.issue_id !== issueId) {
        throw new Error("Issue details did not match this project.");
      }
      const detail: ProjectIssueDetail = {
        issue: payload.issue,
        comments: Array.isArray(payload.comments) ? payload.comments.filter((comment) => typeof comment?.body === "string") : [],
        links: Array.isArray(payload.links) ? payload.links : [],
      };
      setSelectedIssue(detail);
      setIssueEditTitle(detail.issue.title);
      setIssueEditDescription(detail.issue.description ?? "");
      setIssueEditStatus(detail.issue.status.toLowerCase());
      setIssueEditPriority((detail.issue.priority ?? "medium").toLowerCase());
      setIssueEditTeam(detail.issue.assigned_team ?? "");
      setIssueEditAgent(detail.issue.assigned_agent ?? "");
      setIssueNewComment("");
    } catch (cause) {
      setIssueActionError(cause instanceof Error ? cause.message : "Issue details could not be loaded.");
    } finally {
      setIssueDetailLoading(false);
    }
  }

  async function handleCreateIssue(event: ReactFormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (accessDeniedRef.current || !issueCreateTitle.trim()) return;
    setIssueCreateBusy(true);
    setIssueActionError(null);
    setIssueActionNotice(null);
    const body: CanonicalIssueCreateRequest = {
      title: issueCreateTitle.trim(),
      description: issueCreateDescription.trim() || null,
      issue_type: issueCreateType,
      priority: issueCreatePriority,
    };
    try {
      const response = await fetch(`/api/projects/${id}/issues`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (handleDeniedResponse(response)) return;
      if (!response.ok) throw new Error(await issueErrorMessage(response, "Issue could not be created"));
      setIssueCreateOpen(false);
      setIssueCreateTitle("");
      setIssueCreateDescription("");
      setIssueCreateType("feature");
      setIssueCreatePriority("medium");
      setIssueActionNotice("Issue created.");
      await loadWorkspace();
    } catch (cause) {
      setIssueActionError(cause instanceof Error ? cause.message : "Issue could not be created.");
    } finally {
      setIssueCreateBusy(false);
    }
  }

  async function handleUpdateIssue(event: ReactFormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (accessDeniedRef.current || !selectedIssue) return;
    const revision = selectedIssue.issue.revision;
    if (!Number.isSafeInteger(revision) || (revision ?? 0) < 1) {
      setIssueActionError("This issue has no valid revision. Refresh the issue details before editing.");
      return;
    }
    setIssueUpdateBusy(true);
    setIssueActionError(null);
    setIssueActionNotice(null);
    const body: CanonicalIssueUpdateRequest = {
      title: issueEditTitle.trim(),
      description: issueEditDescription.trim() || null,
      status: issueEditStatus,
      priority: issueEditPriority,
      assigned_team: issueEditTeam.trim() || null,
      assigned_agent: issueEditAgent.trim() || null,
      expected_revision: revision,
    };
    try {
      const response = await fetch(`/api/projects/${id}/issues/${encodeURIComponent(selectedIssue.issue.issue_id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (handleDeniedResponse(response)) return;
      if (response.status === 409) {
        await openIssue(selectedIssue.issue.issue_id);
        setIssueActionNotice("This issue changed since you opened it. The latest revision is loaded; review it before saving again.");
        return;
      }
      if (!response.ok) throw new Error(await issueErrorMessage(response, "Issue could not be updated"));
      await Promise.all([openIssue(selectedIssue.issue.issue_id), loadWorkspace()]);
      setIssueActionNotice("Issue updated.");
    } catch (cause) {
      setIssueActionError(cause instanceof Error ? cause.message : "Issue could not be updated.");
    } finally {
      setIssueUpdateBusy(false);
    }
  }

  async function handleAddIssueComment(event: ReactFormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (accessDeniedRef.current || !selectedIssue || !issueNewComment.trim()) return;
    setIssueCommentBusy(true);
    setIssueActionError(null);
    setIssueActionNotice(null);
    const body: Pick<CanonicalIssueCommentRequest, "body"> = { body: issueNewComment.trim() };
    try {
      const response = await fetch(`/api/projects/${id}/issues/${encodeURIComponent(selectedIssue.issue.issue_id)}/comments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (handleDeniedResponse(response)) return;
      if (!response.ok) throw new Error(await issueErrorMessage(response, "Comment could not be added"));
      setIssueNewComment("");
      await openIssue(selectedIssue.issue.issue_id);
      setIssueActionNotice("Comment added.");
    } catch (cause) {
      setIssueActionError(cause instanceof Error ? cause.message : "Comment could not be added.");
    } finally {
      setIssueCommentBusy(false);
    }
  }

  async function handleRepositoryAction(operation: "sync" | "status") {
    if (accessDeniedRef.current) return;
    setActionLoading(`repository-${operation}`);
    setRepositoryError(null);
    try {
      const res = await fetch(`/api/projects/${id}/repository`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation }),
      });
      if (!res.ok) {
        if (handleDeniedResponse(res)) return;
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
    if (accessDeniedRef.current) return;
    const loadingKey = `${decisionId}-${decision}`;
    setActionLoading(loadingKey);
    try {
      const res = await fetch(`/api/projects/${id}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision,
          decided_by: "operator",
          comments: `Submitted from project workspace: ${decision.toLowerCase()}`,
        }),
      });
      if (handleDeniedResponse(res)) return;
      await Promise.all([load(), loadWorkspace()]);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleAction(action: "retry" | "archive") {
    if (accessDeniedRef.current) return;
    setActionLoading(action);
    try {
      const res = await fetch(`/api/projects/${id}/${action}`, { method: "POST" });
      if (handleDeniedResponse(res)) return;
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleStartProject() {
    if (accessDeniedRef.current) return;
    setActionLoading("start");
    setStartError(null);
    try {
      const res = await fetch(`/api/projects/${id}/start`, { method: "POST" });
      if (handleDeniedResponse(res)) return;
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const detail =
          body && typeof body === "object"
            ? (body as { detail?: unknown; error?: unknown }).detail ??
              (body as { detail?: unknown; error?: unknown }).error
            : null;
        throw new Error(
          typeof detail === "string" && detail.trim()
            ? detail
            : `Project could not be started (HTTP ${res.status})`,
        );
      }
      await load();
    } catch (cause) {
      setStartError(
        cause instanceof Error ? cause.message : "Project could not be started",
      );
    } finally {
      setActionLoading(null);
    }
  }

  async function handleTransition(event: string) {
    if (accessDeniedRef.current) return;
    setActionLoading(event);
    try {
      const res = await fetch(`/api/projects/${id}/transition`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event }),
      });
      if (handleDeniedResponse(res)) return;
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleFlowAction(action: string) {
    if (!flowInstance || accessDeniedRef.current) return;
    setActionLoading(action);
    try {
      const res = await fetch(`/api/flows/instances/${flowInstance.id}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) {
        if (handleDeniedResponse(res)) return;
        const data = await res.json().catch(() => ({}));
        setFlowError(
          data.error || data.detail || `Could not ${action} this flow.`,
        );
        return;
      }
      setFlowError(null);
      await loadFlowData();
    } catch {
      setFlowError(`Could not ${action} this flow. Try again.`);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleNodeAction(
    nodeId: string,
    action: string,
    options?: { approved?: boolean; decision?: string; error?: string },
  ) {
    if (!flowInstance || accessDeniedRef.current) return;
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
        setFlowError(null);
        await Promise.all([loadFlowData(), load()]);
      } else {
        if (handleDeniedResponse(res)) return;
        const data = await res.json().catch(() => ({}));
        setFlowError(
          data.error || data.detail || `Failed to ${action} node`,
        );
      }
    } catch {
      setFlowError(`Could not ${action} this flow node. Try again.`);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleSwitchFlow(newFlowId: string) {
    if (!flowInstance || accessDeniedRef.current) return;
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
        setFlowError(null);
        await loadFlowData();
        setShowFlowSwitch(false);
      } else {
        if (handleDeniedResponse(res)) return;
        const data = await res.json().catch(() => ({}));
        setFlowError(data.error || data.detail || "Failed to switch flow");
      }
    } catch {
      setFlowError("Could not switch flows. Try again.");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleRetry() {
    if (!flowInstance || accessDeniedRef.current) return;
    setActionLoading("retry");
    try {
      const res = await fetch(`/api/flows/instances/${flowInstance.id}/retry`, {
        method: "POST",
      });
      if (res.ok) {
        setFlowError(null);
        await loadFlowData();
      } else {
        if (handleDeniedResponse(res)) return;
        const data = await res.json().catch(() => ({}));
        setFlowError(data.error || data.detail || "Failed to retry flow");
      }
    } catch {
      setFlowError("Could not retry this flow. Try again.");
    } finally {
      setActionLoading(null);
    }
  }

  async function openFlowSwitchModal() {
    if (accessDeniedRef.current) return;
    setActionLoading("load-flows");
    try {
      const res = await fetch("/api/flows?is_active=true");
      if (res.ok) {
        const flows = await res.json();
        setAvailableFlows(flows || []);
      } else if (handleDeniedResponse(res)) {
        return;
      }
    } catch {
      setAvailableFlows([]);
    } finally {
      setActionLoading(null);
    }
    setShowFlowSwitch(true);
  }

  async function handleAssignFlow(flowId: string) {
    if (accessDeniedRef.current) return;
    setActionLoading("assign-flow");
    try {
      const res = await fetch(`/api/projects/${id}/flow-instance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flow_id: flowId }),
      });
      if (res.ok) {
        setFlowError(null);
        await loadFlowData();
      } else {
        if (handleDeniedResponse(res)) return;
        const data = await res.json().catch(() => ({}));
        setFlowError(data.error || data.detail || "Failed to assign flow");
      }
    } catch {
      setFlowError("Could not assign this flow. Try again.");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleOverrideFlowNode() {
    if (!flowInstance || !overrideNodeId || accessDeniedRef.current) return;
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
        if (handleDeniedResponse(res)) return;
        const data = await res.json().catch(() => ({}));
        setFlowError(data.error || "Failed to override flow node");
      }
    } finally {
      setActionLoading(null);
    }
  }

  async function handleAddContextItem() {
    if (!newContextName.trim() || accessDeniedRef.current) return;
    setActionLoading("add-context");
    setContextUploadError(null);
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
      } else if (newContextType === "FILE") {
        const file = newContextFile;
        if (!file) {
          setContextUploadError("Choose a text file to attach.");
          return;
        }
        if (file.size === 0) {
          setContextUploadError("The selected file is empty.");
          return;
        }
        if (file.size > MAX_INLINE_TEXT_FILE_BYTES) {
          setContextUploadError("Text files must be 100 KB or smaller.");
          return;
        }
        const extension = file.name.match(/\.[^.]+$/)?.[0]?.toLowerCase();
        const hasTextMimeType =
          file.type.startsWith("text/") ||
          ["application/json", "application/yaml", "application/x-yaml"].includes(
            file.type,
          );
        if (
          !hasTextMimeType &&
          (!extension || !SUPPORTED_TEXT_FILE_EXTENSIONS.has(extension))
        ) {
          setContextUploadError(
            "Choose a UTF-8 text file such as .txt, .md, .json, .yaml, or source code.",
          );
          return;
        }

        try {
          const contents = await file.arrayBuffer();
          const contentText = new TextDecoder("utf-8", { fatal: true }).decode(
            contents,
          );
          if (contentText.includes("\u0000")) {
            throw new Error("The selected file contains binary data.");
          }
          const digest = await crypto.subtle.digest("SHA-256", contents);
          const sha256 = Array.from(new Uint8Array(digest))
            .map((byte) => byte.toString(16).padStart(2, "0"))
            .join("");
          body.mime_type = file.type || "text/plain";
          body.size_bytes = file.size;
          body.content_text = contentText;
          body.metadata = {
            upload_mode: "inline_text_context",
            source_name: file.name,
            sha256,
          };
        } catch (cause) {
          setContextUploadError(
            cause instanceof Error && cause.message.includes("binary data")
              ? cause.message
              : "The selected file must contain valid UTF-8 text.",
          );
          return;
        }
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
        setNewContextFile(null);
        setNewContextTags("");
        setShowContextUpload(false);
      } else {
        handleDeniedResponse(res);
        if (!accessDeniedRef.current) {
          const data = await res.json().catch(() => ({}));
          setContextUploadError(
            data.error || data.detail || "Could not add this project context item.",
          );
        }
      }
    } catch {
      setContextUploadError("Could not add this project context item. Try again.");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDeleteContextItem(itemId: string) {
    if (accessDeniedRef.current) return;
    if (!confirm("Delete this context item?")) return;
    setActionLoading(`delete-${itemId}`);
    try {
      const res = await fetch(`/api/projects/${id}/context/${itemId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        await loadContextData();
      } else {
        handleDeniedResponse(res);
      }
    } finally {
      setActionLoading(null);
    }
  }

  async function handleBulkDeleteContextItems() {
    if (accessDeniedRef.current || contextSelection.selectedCount === 0) return;
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
          if (!res.ok) {
            if (res.status === 401 || res.status === 403) {
              throw Object.assign(new Error("Authorization denied"), {
                status: res.status,
              });
            }
            throw new Error(`HTTP ${res.status}`);
          }
        }),
      );
      const denied = results.some(
        (result) =>
          result.status === "rejected" &&
          (result.reason as HttpError)?.status !== undefined &&
          ((result.reason as HttpError).status === 401 ||
            (result.reason as HttpError).status === 403),
      );
      if (denied) {
        handleAccessDenied();
        return;
      }
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

  if (loading && !project) {
    return (
      <main
        aria-label="Project detail"
        className="dashboard-page flex items-center justify-center h-full"
      >
        <div
          role="status"
          aria-live="polite"
          aria-label="Loading project"
          className="text-slate-500 text-sm"
        >
          Loading project…
        </div>
      </main>
    );
  }

  if (accessDenied && !project) {
    return (
      <main aria-label="Project detail" className="dashboard-page">
        <section
          role="region"
          aria-label="Project access status"
          className="mx-4 mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-4 text-sm text-amber-100"
        >
          <h1 className="text-lg font-medium text-amber-50">
            Project access denied
          </h1>
          <p className="mt-1 text-amber-100/80">
            The project definition cannot be read while authorization is
            unavailable. Refresh and project controls remain hidden until
            access is restored.
          </p>
        </section>
        <Link
          href="/projects"
          className="mt-4 inline-flex min-h-11 items-center gap-1 text-sm text-blue-400 hover:text-blue-300"
        >
          <ArrowLeft size={14} aria-hidden="true" /> Back to projects
        </Link>
      </main>
    );
  }

  if (!project) {
    return (
      <main aria-label="Project detail" className="dashboard-page">
        <ErrorBanner
          tone="error"
          title="Project unavailable"
          action={
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              aria-busy={loading}
              className="inline-flex min-h-11 items-center gap-2 rounded-md border border-red-700/70 bg-red-950/40 px-3 py-2 text-xs font-medium text-red-100 transition-colors hover:bg-red-900/50 disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw
                size={14}
                className={loading ? "animate-spin" : ""}
                aria-hidden="true"
              />
              Retry
            </button>
          }
        >
          {projectLoadError ??
            "The project could not be loaded from the control plane. Retry to try again."}
        </ErrorBanner>
        <Link
          href="/projects"
          className="inline-flex min-h-11 items-center gap-1 text-sm text-blue-400 hover:text-blue-300"
        >
          <ArrowLeft size={14} /> Back to projects
        </Link>
      </main>
    );
  }

  const isFailed = project.state === "FAILED";
  const isTerminal = TERMINAL_STATES.includes(project.state);
  const scopedWorkerRuns = projectWorkerRuns.filter(
    (run) => run.project_id.toLowerCase() === id.toLowerCase(),
  );
  const scopedProjectIssues = projectIssues.filter(
    (issue) => issue.project_id.toLowerCase() === id.toLowerCase(),
  );

  // Refresh handler respects the active tab so operators get fresh data
  // for whichever surface they're currently inspecting.
  const handleRefresh = () => {
    if (accessDeniedRef.current) return Promise.resolve();
    const surfaceRefresh =
      activeTab === "flow"
        ? loadFlowData()
        : activeTab === "workspace"
          ? loadWorkspace()
          : activeTab === "context"
            ? loadContextData()
            : activeTab === "evidence"
              ? loadEvidence()
              : Promise.resolve();
    return Promise.all([load(), surfaceRefresh]);
  };
  const isRefreshing =
    loading ||
    flowLoading ||
    workspaceLoading ||
    contextLoading ||
    evidenceLoading;

  return (
    <main aria-label="Project detail" className="dashboard-page">
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
        actions={!accessDenied ? (
          <>
            <span
              className={clsx(
                "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium text-white border border-white/10",
                STATE_COLORS[project.state] ?? "bg-slate-600",
              )}
              role="status"
              aria-label={`Project state: ${project.state.replace(/_/g, " ")}`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-white/70" />
              {project.state?.replace(/_/g, " ")}
            </span>
            {project.state === "INIT" && (
              <button
                type="button"
                onClick={() => void handleStartProject()}
                disabled={!!actionLoading || projectStale}
                aria-busy={actionLoading === "start"}
                className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-blue-600/60 bg-blue-600/20 px-3 py-2 text-xs font-medium text-blue-100 transition-colors hover:bg-blue-600/30 disabled:cursor-wait disabled:opacity-50"
              >
                <Play size={14} aria-hidden="true" />
                {actionLoading === "start" ? "Starting…" : "Start CEO work"}
              </button>
            )}
            <button
              type="button"
              onClick={handleRefresh}
              title="Refresh"
              aria-label="Refresh project data"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-slate-800 bg-slate-950/40 text-slate-400 hover:text-slate-100 hover:border-slate-600 hover:bg-slate-900 transition-colors disabled:opacity-50"
            >
              <RefreshCw
                size={15}
                className={isRefreshing ? "animate-spin" : ""}
                aria-hidden="true"
              />
            </button>
          </>
        ) : undefined}
      />

      {startError && !accessDenied && (
        <div className="mx-4 mt-3">
          <ErrorBanner
            tone="error"
            title="Project could not be started"
            action={
              <button
                type="button"
                onClick={() => void handleStartProject()}
                disabled={!!actionLoading || projectStale}
                className="inline-flex min-h-11 items-center rounded-md border border-red-700/70 bg-red-950/40 px-3 py-2 text-xs font-medium text-red-100 hover:bg-red-900/50 disabled:opacity-50"
              >
                Retry start
              </button>
            }
          >
            {startError}
          </ErrorBanner>
        </div>
      )}

      {accessDenied && (
        <section
          role="region"
          aria-label="Project access status"
          className="mx-4 mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"
        >
          <h2 className="font-medium text-amber-50">Project access denied</h2>
          <p className="mt-1 text-amber-100/80">
            This last-known project header is retained for reference only.
            Refresh, retry, workflow, flow, context, evidence, repository,
            approval, transition, and other mutation controls are hidden until
            authorization is restored.
          </p>
        </section>
      )}

      {projectStale && projectRefreshError && !accessDenied && (
        <ErrorBanner
          tone="warning"
          title="Showing last known project state"
          action={
            <button
              type="button"
              onClick={() => void handleRefresh()}
              disabled={isRefreshing}
              aria-busy={isRefreshing}
              className="inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs font-medium text-slate-100 transition-colors hover:bg-slate-800 disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw
                size={14}
                className={isRefreshing ? "animate-spin" : ""}
                aria-hidden="true"
              />
              Retry
            </button>
          }
        >
          {projectRefreshError} The project controls remain visible, but the
          canonical state may have changed.
        </ErrorBanner>
      )}

      {!accessDenied && <div
        className="flex flex-wrap gap-1 border-b border-slate-800"
        role="tablist"
        aria-label="Project views"
        aria-orientation="horizontal"
      >
        <button
          type="button"
          onClick={() => setActiveTab("workspace")}
          data-testid="project-tab-workspace"
          role="tab"
          aria-selected={activeTab === "workspace"}
          aria-controls="project-panel-workspace"
          className={clsx(
            "min-h-11 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "workspace"
              ? "border-blue-400 text-blue-300"
              : "border-transparent text-slate-400 hover:text-slate-200",
          )}
        >
          Workspace
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("workflow")}
          data-testid="project-tab-workflow"
          role="tab"
          aria-selected={activeTab === "workflow"}
          aria-controls="project-panel-workflow"
          className={clsx(
            "min-h-11 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
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
          type="button"
          onClick={() => setActiveTab("flow")}
          data-testid="project-tab-flow"
          role="tab"
          aria-selected={activeTab === "flow"}
          aria-controls="project-panel-flow"
          className={clsx(
            "min-h-11 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
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
          type="button"
          onClick={() => setActiveTab("context")}
          data-testid="project-tab-context"
          role="tab"
          aria-selected={activeTab === "context"}
          aria-controls="project-panel-context"
          className={clsx(
            "min-h-11 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
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
          type="button"
          onClick={() => setActiveTab("evidence")}
          data-testid="project-tab-evidence"
          role="tab"
          aria-selected={activeTab === "evidence"}
          aria-controls="project-panel-evidence"
          className={clsx(
            "min-h-11 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            activeTab === "evidence"
              ? "border-blue-400 text-blue-300"
              : "border-transparent text-slate-400 hover:text-slate-200",
          )}
        >
          Evidence
          {evidence && (
            <span
              className={clsx(
                "px-1.5 py-0.5 rounded text-xxs",
                evidence.status === "complete"
                  ? "bg-emerald-950/60 text-emerald-300"
                  : "bg-amber-950/60 text-amber-300",
              )}
            >
              {Math.round(evidence.completeness_score * 100)}%
            </span>
          )}
        </button>
      </div>}

      {!accessDenied && activeTab === "workspace" && (
        <div
          id="project-panel-workspace"
          role="tabpanel"
          aria-labelledby="project-tab-workspace"
          aria-busy={workspaceLoading}
          className="space-y-4"
        >
          {workspaceStale && workspaceError && (
            <div data-testid="project-workspace-stale">
              <ErrorBanner
                tone="warning"
                title="Showing last known workspace"
                action={
                  <button
                    type="button"
                    onClick={() => void loadWorkspace()}
                    disabled={workspaceLoading}
                    aria-busy={workspaceLoading}
                    className="inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs font-medium text-slate-100 transition-colors hover:bg-slate-800 disabled:cursor-wait disabled:opacity-60"
                  >
                    <RefreshCw
                      size={14}
                      className={workspaceLoading ? "animate-spin" : ""}
                      aria-hidden="true"
                    />
                    Retry
                  </button>
                }
              >
                {workspaceError} Retained workspace data remains visible while
                the latest refresh is retried.
              </ErrorBanner>
            </div>
          )}
          {workspace === null && !workspaceLoading && (
            <ErrorBanner
              tone="error"
              title="Workspace data unavailable"
              action={
                <button
                  type="button"
                  onClick={() => void loadWorkspace()}
                  disabled={workspaceLoading}
                  aria-busy={workspaceLoading}
                  className="inline-flex min-h-11 items-center gap-2 rounded-md border border-red-700/70 bg-red-950/40 px-3 py-2 text-xs font-medium text-red-100 transition-colors hover:bg-red-900/50 disabled:cursor-wait disabled:opacity-60"
                >
                  <RefreshCw
                    size={14}
                    className={workspaceLoading ? "animate-spin" : ""}
                    aria-hidden="true"
                  />
                  Retry
                </button>
              }
            >
              {workspaceError ??
                "The project workspace API may be unavailable or the project may not exist. Retry to try again."}
            </ErrorBanner>
          )}

          {/* Sub-tabs help operators focus on one slice of the workspace at a time
              (operator actions, project resources, or cost) without scrolling. */}
          <div
            className="flex flex-wrap gap-1 dashboard-toolbar"
            role="tablist"
            aria-label="Workspace sections"
            aria-orientation="horizontal"
          >
            <button
              type="button"
              id="workspace-tab-activity"
              onClick={() => setWorkspaceSubTab("activity")}
              onKeyDown={(event) =>
                handleWorkspaceSubTabKeyDown(event, "activity")
              }
              role="tab"
              aria-selected={workspaceSubTab === "activity"}
              aria-controls="workspace-panel-activity"
              tabIndex={workspaceSubTab === "activity" ? 0 : -1}
              className={clsx(
                "min-h-11 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors flex items-center gap-1.5",
                workspaceSubTab === "activity"
                  ? "bg-blue-500/15 text-blue-200 border-blue-400/40"
                  : "bg-slate-900/40 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700",
              )}
            >
              <Activity size={12} /> Activity
            </button>
            <button
              type="button"
              id="workspace-tab-resources"
              onClick={() => setWorkspaceSubTab("resources")}
              onKeyDown={(event) =>
                handleWorkspaceSubTabKeyDown(event, "resources")
              }
              role="tab"
              aria-selected={workspaceSubTab === "resources"}
              aria-controls="workspace-panel-resources"
              tabIndex={workspaceSubTab === "resources" ? 0 : -1}
              className={clsx(
                "min-h-11 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors flex items-center gap-1.5",
                workspaceSubTab === "resources"
                  ? "bg-blue-500/15 text-blue-200 border-blue-400/40"
                  : "bg-slate-900/40 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700",
              )}
            >
              <ListChecks size={12} /> Resources
            </button>
            <button
              type="button"
              id="workspace-tab-cost"
              onClick={() => setWorkspaceSubTab("cost")}
              onKeyDown={(event) => handleWorkspaceSubTabKeyDown(event, "cost")}
              role="tab"
              aria-selected={workspaceSubTab === "cost"}
              aria-controls="workspace-panel-cost"
              tabIndex={workspaceSubTab === "cost" ? 0 : -1}
              className={clsx(
                "min-h-11 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors flex items-center gap-1.5",
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
            <div
              id="workspace-panel-activity"
              role="tabpanel"
              aria-labelledby="workspace-tab-activity"
              tabIndex={0}
              className="space-y-4"
            >
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

              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <section className="dashboard-surface p-4" aria-labelledby="project-worker-runs-heading">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <h2 id="project-worker-runs-heading" className="text-sm font-medium text-white">Worker Runs</h2>
                    <span className="text-xs text-slate-500">canonical runtime state</span>
                  </div>
                  {projectWorkerRunsError ? (
                    <div role="status" className="space-y-2 text-xs text-amber-200">
                      <p>{projectWorkerRunsError}</p>
                      <button type="button" onClick={() => void loadWorkspace()} disabled={workspaceLoading} className="min-h-11 rounded-md border border-slate-700 px-3 py-2 text-slate-200 hover:bg-slate-800 disabled:opacity-50">Retry project activity</button>
                    </div>
                  ) : workspaceLoading && scopedWorkerRuns.length === 0 ? (
                    <p className="text-xs text-slate-500">Loading worker runs…</p>
                  ) : scopedWorkerRuns.length === 0 ? (
                    <p className="text-xs text-slate-500">No worker runs are recorded for this project yet.</p>
                  ) : (
                    <ul className="space-y-2" aria-label="Project worker runs">
                      {scopedWorkerRuns.map((run) => (
                        <li key={run.id} className="rounded-lg border border-slate-800 bg-slate-950/55 p-3 text-xs">
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <Link href={`/workers/runs/${encodeURIComponent(run.id)}`} aria-label={`Open WorkerRun ${run.id}`} className="font-mono text-blue-300 underline decoration-blue-500/50 underline-offset-2 hover:text-blue-200">{run.id}</Link>
                            <span className="rounded border border-slate-700 px-2 py-1 text-slate-200">{run.state.replace(/_/g, " ")}</span>
                          </div>
                          <div className="mt-2 text-slate-400">{run.task_type || "Worker task"}{run.worker_id ? ` · worker ${run.worker_id}` : ""}</div>
                          <div className="mt-1 text-slate-500">
                            {run.created_at ? formatInTz(run.created_at, "MMM d HH:mm") : "Creation time unknown"}
                            {run.model_resolution_snapshot_id ? " · governed model snapshot pinned" : " · no model snapshot recorded"}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section className="dashboard-surface p-4" aria-labelledby="project-issues-heading">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <h2 id="project-issues-heading" className="text-sm font-medium text-white">Project Issues</h2>
                    <div className="flex items-center gap-2">
                      <span className="hidden text-xs text-slate-500 sm:inline">project-scoped work items</span>
                      <button
                        type="button"
                        onClick={() => { setIssueCreateOpen((open) => !open); setIssueActionError(null); setIssueActionNotice(null); }}
                        disabled={accessDenied}
                        aria-expanded={issueCreateOpen}
                        className="inline-flex min-h-11 items-center gap-1.5 rounded-md border border-blue-700/60 bg-blue-950/30 px-3 py-2 text-xs font-medium text-blue-100 hover:bg-blue-900/40 disabled:opacity-50"
                      >
                        <Plus size={14} aria-hidden="true" /> New issue
                      </button>
                    </div>
                  </div>
                  {issueActionError && !selectedIssue && <p role="alert" className="mb-3 rounded-md border border-red-700/50 bg-red-950/30 px-3 py-2 text-xs text-red-200">{issueActionError}</p>}
                  {issueActionNotice && !selectedIssue && <p role="status" className="mb-3 rounded-md border border-emerald-700/50 bg-emerald-950/25 px-3 py-2 text-xs text-emerald-200">{issueActionNotice}</p>}
                  {issueCreateOpen && (
                    <form onSubmit={handleCreateIssue} className="mb-4 grid gap-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3 md:grid-cols-2" aria-label="Create project issue">
                      <label className="text-xs text-slate-300 md:col-span-2">Title
                        <input required maxLength={500} value={issueCreateTitle} onChange={(event) => setIssueCreateTitle(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white" />
                      </label>
                      <label className="text-xs text-slate-300 md:col-span-2">Description
                        <textarea maxLength={100000} rows={3} value={issueCreateDescription} onChange={(event) => setIssueCreateDescription(event.target.value)} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white" />
                      </label>
                      <label className="text-xs text-slate-300">Type
                        <select value={issueCreateType} onChange={(event) => setIssueCreateType(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white">
                          {PROJECT_ISSUE_TYPE_OPTIONS.map((value) => <option key={value} value={value}>{value.replace(/_/g, " ")}</option>)}
                        </select>
                      </label>
                      <label className="text-xs text-slate-300">Priority
                        <select value={issueCreatePriority} onChange={(event) => setIssueCreatePriority(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white">
                          {PROJECT_ISSUE_PRIORITY_OPTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
                        </select>
                      </label>
                      <div className="flex gap-2 md:col-span-2">
                        <button type="submit" disabled={issueCreateBusy || !issueCreateTitle.trim()} className="inline-flex min-h-11 items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50">{issueCreateBusy ? "Creating…" : "Create issue"}</button>
                        <button type="button" onClick={() => setIssueCreateOpen(false)} disabled={issueCreateBusy} className="min-h-11 rounded-md border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-50">Cancel</button>
                      </div>
                    </form>
                  )}
                  {projectIssuesError ? (
                    <div role="status" className="space-y-2 text-xs text-amber-200">
                      <p>{projectIssuesError}</p>
                      <button type="button" onClick={() => void loadWorkspace()} disabled={workspaceLoading} className="min-h-11 rounded-md border border-slate-700 px-3 py-2 text-slate-200 hover:bg-slate-800 disabled:opacity-50">Retry project activity</button>
                    </div>
                  ) : workspaceLoading && scopedProjectIssues.length === 0 ? (
                    <p className="text-xs text-slate-500">Loading project issues…</p>
                  ) : scopedProjectIssues.length === 0 ? (
                    <p className="text-xs text-slate-500">No issues are recorded for this project yet.</p>
                  ) : (
                    <ul className="space-y-2" aria-label="Project issues">
                      {scopedProjectIssues.map((issue) => (
                        <li key={issue.issue_id} className="rounded-lg border border-slate-800 bg-slate-950/55 p-3 text-xs">
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <span className="font-medium text-slate-100">{issue.title}</span>
                            <span className="rounded border border-slate-700 px-2 py-1 text-slate-200">{issue.status.replace(/_/g, " ")}</span>
                          </div>
                          <div className="mt-2 text-slate-400">{issue.issue_type || "Issue"} · {issue.priority || "priority not set"}{issue.assigned_team ? ` · ${issue.assigned_team}` : ""}</div>
                          <button type="button" onClick={() => void openIssue(issue.issue_id)} disabled={issueDetailLoading} className="mt-2 inline-flex min-h-11 items-center gap-1.5 rounded border border-slate-700 px-3 py-2 text-xs text-blue-200 hover:bg-slate-800 disabled:opacity-50">
                            <MessageSquare size={13} aria-hidden="true" /> {issueDetailLoading ? "Loading…" : "Manage issue"}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>

              {selectedIssue && (
                <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-black/70 p-4 sm:items-center" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedIssue(null); }}>
                  <section role="dialog" aria-modal="true" aria-labelledby="project-issue-dialog-title" className="my-4 w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-950 p-5 shadow-2xl">
                    <div className="mb-4 flex items-start justify-between gap-3">
                      <div>
                        <h2 id="project-issue-dialog-title" className="text-base font-semibold text-white">Manage project issue</h2>
                        <p className="mt-1 break-all font-mono text-xs text-slate-500">{selectedIssue.issue.issue_id} · revision {selectedIssue.issue.revision ?? "unknown"}</p>
                      </div>
                      <button type="button" aria-label="Close issue details" onClick={() => setSelectedIssue(null)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md border border-slate-700 text-slate-300 hover:bg-slate-800"><X size={16} aria-hidden="true" /></button>
                    </div>
                    {issueActionError && <p role="alert" className="mb-3 rounded-md border border-red-700/50 bg-red-950/30 px-3 py-2 text-xs text-red-200">{issueActionError}</p>}
                    {issueActionNotice && <p role="status" className="mb-3 rounded-md border border-emerald-700/50 bg-emerald-950/25 px-3 py-2 text-xs text-emerald-200">{issueActionNotice}</p>}
                    {issueDetailLoading && <p role="status" className="mb-3 text-xs text-slate-400">Refreshing issue details…</p>}
                    <form onSubmit={handleUpdateIssue} className="grid gap-3 md:grid-cols-2" aria-label="Update project issue">
                      <label className="text-xs text-slate-300 md:col-span-2">Title
                        <input required maxLength={500} value={issueEditTitle} onChange={(event) => setIssueEditTitle(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white" />
                      </label>
                      <label className="text-xs text-slate-300 md:col-span-2">Description
                        <textarea maxLength={100000} rows={3} value={issueEditDescription} onChange={(event) => setIssueEditDescription(event.target.value)} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white" />
                      </label>
                      <label className="text-xs text-slate-300">Status
                        <select value={issueEditStatus} onChange={(event) => setIssueEditStatus(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white">
                          {!PROJECT_ISSUE_STATUS_OPTIONS.includes(issueEditStatus) && <option value={issueEditStatus}>{issueEditStatus.replace(/_/g, " ")} (current)</option>}
                          {PROJECT_ISSUE_STATUS_OPTIONS.map((value) => <option key={value} value={value}>{value.replace(/_/g, " ")}</option>)}
                        </select>
                      </label>
                      <label className="text-xs text-slate-300">Priority
                        <select value={issueEditPriority} onChange={(event) => setIssueEditPriority(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white">
                          {!PROJECT_ISSUE_PRIORITY_OPTIONS.includes(issueEditPriority) && <option value={issueEditPriority}>{issueEditPriority} (current)</option>}
                          {PROJECT_ISSUE_PRIORITY_OPTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
                        </select>
                      </label>
                      <label className="text-xs text-slate-300">Assigned team
                        <input maxLength={200} value={issueEditTeam} onChange={(event) => setIssueEditTeam(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white" />
                      </label>
                      <label className="text-xs text-slate-300">Assigned worker
                        <input maxLength={200} value={issueEditAgent} onChange={(event) => setIssueEditAgent(event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-slate-700 bg-slate-900 px-3 text-sm text-white" />
                      </label>
                      <div className="flex flex-wrap gap-2 md:col-span-2">
                        <button type="submit" disabled={issueUpdateBusy || issueDetailLoading || !issueEditTitle.trim()} className="inline-flex min-h-11 items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"><Save size={14} aria-hidden="true" />{issueUpdateBusy ? "Saving…" : "Save issue"}</button>
                        <button type="button" onClick={() => void openIssue(selectedIssue.issue.issue_id)} disabled={issueDetailLoading || issueUpdateBusy} className="min-h-11 rounded-md border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-50">Refresh latest</button>
                      </div>
                    </form>
                    <section className="mt-5 border-t border-slate-800 pt-4" aria-labelledby="issue-comments-heading">
                      <h3 id="issue-comments-heading" className="text-sm font-medium text-white">Comments</h3>
                      {selectedIssue.comments.length === 0 ? <p className="mt-2 text-xs text-slate-500">No comments yet.</p> : (
                        <ul className="mt-2 max-h-48 space-y-2 overflow-y-auto" aria-label="Issue comments">
                          {selectedIssue.comments.map((comment, index) => <li key={comment.comment_id ?? `${comment.created_at ?? "comment"}-${index}`} className="rounded-md border border-slate-800 bg-slate-900/60 p-3 text-xs">
                            <p className="whitespace-pre-wrap text-slate-200">{comment.body}</p>
                            <p className="mt-2 text-slate-500">{comment.actor_id ?? "Operator"}{comment.created_at ? ` · ${formatInTz(comment.created_at, "MMM d HH:mm")}` : ""}</p>
                          </li>)}
                        </ul>
                      )}
                      <form onSubmit={handleAddIssueComment} className="mt-3 space-y-2" aria-label="Add issue comment">
                        <label htmlFor="project-issue-comment" className="text-xs text-slate-300">Add a comment</label>
                        <textarea id="project-issue-comment" required maxLength={100000} rows={3} value={issueNewComment} onChange={(event) => setIssueNewComment(event.target.value)} className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white" />
                        <button type="submit" disabled={issueCommentBusy || !issueNewComment.trim()} className="inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-xs text-slate-100 hover:bg-slate-800 disabled:opacity-50"><MessageSquare size={14} aria-hidden="true" />{issueCommentBusy ? "Adding…" : "Add comment"}</button>
                      </form>
                    </section>
                  </section>
                </div>
              )}

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
            <div
              id="workspace-panel-resources"
              role="tabpanel"
              aria-labelledby="workspace-tab-resources"
              tabIndex={0}
              className="grid grid-cols-1 gap-4 md:grid-cols-2"
            >
              <div className="dashboard-surface p-4 md:col-span-2">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div>
                    <h2 className="text-sm font-medium text-white">
                      Git Workspace
                    </h2>
                    <p className="text-xs text-slate-500 mt-1">
                      Source code lives in the project-scoped tool workspace and
                      is managed through the Git adapter.
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
                      {actionLoading === "repository-sync"
                        ? "Syncing…"
                        : "Sync"}
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
                        {workspace.repository.workspace_path ||
                          workspace.repository.workspace_relative_path ||
                          "pending"}
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500">Status</div>
                      <div className="text-slate-200 mt-1">
                        {workspace.repository.status || "unknown"}
                        {workspace.repository.clean === false &&
                          " · uncommitted changes"}
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500">Remote</div>
                      <div className="text-slate-200 mt-1 break-all">
                        {workspace.repository.remote ||
                          workspace.repository.repository_url ||
                          "local repository"}
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500">Branch / commit</div>
                      <div className="text-slate-200 mt-1 break-all">
                        {workspace.repository.branch || "unknown"}
                        {workspace.repository.head
                          ? ` · ${workspace.repository.head.slice(0, 12)}`
                          : ""}
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
            <div
              id="workspace-panel-cost"
              role="tabpanel"
              aria-labelledby="workspace-tab-cost"
              tabIndex={0}
              className="dashboard-surface p-4"
            >
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

      {!accessDenied && activeTab === "evidence" && (
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
              The evidence service did not return a project-scoped policy
              result.
            </ErrorBanner>
          )}
          {evidence && (
            <>
              <div className="dashboard-surface p-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-medium text-white">
                    Completion Evidence
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">
                    Policy {evidence.policy_id} v{evidence.policy_version} ·{" "}
                    {Math.round(evidence.completeness_score * 100)}% complete
                  </p>
                </div>
                <span
                  className={clsx(
                    "rounded-full border px-3 py-1 text-xs font-medium",
                    evidence.status === "complete"
                      ? "border-emerald-700/70 bg-emerald-950/40 text-emerald-300"
                      : "border-amber-700/70 bg-amber-950/40 text-amber-200",
                  )}
                >
                  {evidence.status}
                </span>
              </div>
              <div className="space-y-2">
                {evidence.checks.map((check) => (
                  <div
                    key={check.name}
                    className={clsx(
                      "dashboard-surface flex items-start gap-3 p-4",
                      check.required && !check.passed && "border-amber-800/70",
                    )}
                  >
                    {check.passed ? (
                      <CheckCircle
                        size={18}
                        className="mt-0.5 shrink-0 text-emerald-400"
                      />
                    ) : (
                      <XCircle
                        size={18}
                        className="mt-0.5 shrink-0 text-amber-400"
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-100">
                        {check.name.replace(/_/g, " ")}
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xxs font-normal text-slate-400">
                          {check.required ? "required" : "optional"}
                        </span>
                      </div>
                      {check.reason && (
                        <p className="mt-1 text-xs text-amber-200">
                          {check.reason}
                        </p>
                      )}
                      {check.evidence_refs.length > 0 && (
                        <p className="mt-2 break-all text-xxs text-slate-500">
                          Evidence: {check.evidence_refs.join(", ")}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              {evidencePackage && (
                <section
                  className="dashboard-surface p-4"
                  aria-labelledby="evidence-package-heading"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3
                        id="evidence-package-heading"
                        className="text-sm font-medium text-white"
                      >
                        Evidence package coverage
                      </h3>
                      <p className="mt-1 text-xs text-slate-500">
                        {evidencePackage.schema_version} · grouped read-only
                        views over canonical project records
                      </p>
                    </div>
                    <span className="text-xs text-slate-400">
                      {
                        evidencePackage.categories.filter(
                          (category) => category.status === "present",
                        ).length
                      }
                      /{evidencePackage.categories.length} categories present
                    </span>
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {evidencePackage.categories.map((category) => (
                      <div
                        key={category.category}
                        className="rounded-lg border border-slate-800 bg-slate-950/40 p-3"
                      >
                        <div className="flex items-center justify-between gap-2 text-xs">
                          <span className="font-medium capitalize text-slate-200">
                            {category.category}
                          </span>
                          <span
                            className={clsx(
                              "rounded-full px-2 py-0.5",
                              category.status === "present"
                                ? "bg-emerald-950/50 text-emerald-300"
                                : category.status === "missing"
                                  ? "bg-amber-950/50 text-amber-200"
                                  : "bg-slate-800 text-slate-400",
                            )}
                          >
                            {category.status}
                          </span>
                        </div>
                        <p className="mt-1 text-xxs text-slate-500">
                          {category.item_count} item
                          {category.item_count === 1 ? "" : "s"}
                          {category.required ? " · required" : " · optional"}
                        </p>
                        {category.reason && (
                          <p className="mt-1 text-xxs text-amber-200">
                            {category.reason}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                  {evidencePackage.notices.length > 0 && (
                    <p className="mt-3 text-xxs text-slate-500">
                      Resource licence/restriction values are retained as
                      metadata notices only and do not change package status.
                    </p>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      )}

      {!accessDenied && activeTab === "workflow" && (
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

      {!accessDenied && activeTab === "flow" && (
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

      {!accessDenied && activeTab === "context" && (
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
                    onChange={(e) => {
                      setNewContextType(
                        e.target.value as "FILE" | "URL" | "TEXT",
                      );
                      setNewContextFile(null);
                      setContextUploadError(null);
                    }}
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
                <div className="space-y-2 rounded-lg border border-dashed border-slate-700 p-4">
                  <label className="flex cursor-pointer flex-col items-center gap-2 text-center">
                    <Upload size={24} className="text-slate-500" aria-hidden="true" />
                    <span className="text-sm font-medium text-slate-200">
                      Choose a text file
                    </span>
                    <span className="text-xs text-slate-500">
                      UTF-8 text, up to 100 KB. Contents are saved as project context.
                    </span>
                    <input
                      type="file"
                      aria-label="Choose a project text file"
                      accept=".txt,.md,.rst,.csv,.json,.yaml,.yml,.xml,.html,.css,.js,.jsx,.ts,.tsx,.py,.toml,.ini,.log,.sql,.sh,text/*,application/json,application/yaml"
                      className="sr-only"
                      onChange={(event) => {
                        const file = event.currentTarget.files?.[0] ?? null;
                        setNewContextFile(file);
                        setNewContextName(file?.name ?? "");
                        setContextUploadError(null);
                      }}
                    />
                  </label>
                  {newContextFile && (
                    <p className="text-center text-xs text-slate-300" role="status">
                      Selected {newContextFile.name} ({newContextFile.size.toLocaleString()} bytes)
                    </p>
                  )}
                </div>
              )}

              {contextUploadError && (
                <p className="text-sm text-red-300" role="alert">
                  {contextUploadError}
                </p>
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
                const isGeneratedDocument = Boolean(
                  item.read_only || item.source === "document",
                );
                return (
                  <div
                    key={item.id}
                    className={clsx(
                      "bg-slate-900 border rounded-xl p-4 transition-colors",
                      isGeneratedDocument &&
                        "border-indigo-800/80 bg-indigo-950/10",
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
                          <div
                            className="mt-2 truncate text-xxs text-indigo-300/70"
                            title={item.blob_key}
                          >
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
    </main>
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
