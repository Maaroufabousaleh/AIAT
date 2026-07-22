"use client";

import { useState, useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import {
  ArrowLeft,
  Save,
  CheckCircle2,
  GitBranch,
  Sparkles,
  ShieldCheck,
  GitFork,
  ArrowRightLeft,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";
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
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

/**
 * A reusable flow scaffolding users can drop in to get started quickly.
 * Templates are position-only blueprints — labels get generated at apply time
 * so multiple templates never collide on `Date.now()` IDs in the same canvas.
 */
interface FlowTemplate {
  id: string;
  name: string;
  description: string;
  icon: LucideIcon;
  nodes: Array<{ type: FlowNodeType; label: string; position: { x: number; y: number } }>;
  edges: Array<{ sourceIndex: number; targetIndex: number; label?: string }>;
}

interface FlowDryRunResult {
  valid: boolean;
  errors: Array<{ code: string; message: string; node_id?: string }>;
  warnings: Array<{ code: string; message: string; node_id?: string }>;
}

interface GovernedWorkerOption {
  id: string;
  name: string;
  status?: string;
  model_mode?: string;
  model_profile_id?: string | null;
  adapter_type?: string;
  active_adapter_id?: string | null;
  active_skill_bundle_id?: string | null;
}

interface GovernedModelProfile {
  profile_id: string;
  status?: string;
  purpose?: string;
  versions?: Array<{ version?: string; status?: string }>;
}

function csvValues(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : "";
}

function parseCsv(value: string): string[] {
  return value.split(",").map((entry) => entry.trim()).filter(Boolean);
}

const FLOW_TEMPLATES: FlowTemplate[] = [
  {
    id: "blank",
    name: "Blank canvas",
    description: "Start from scratch with just a Start and End node.",
    icon: GitBranch,
    nodes: [
      { type: "start", label: "Start", position: { x: 80, y: 80 } },
      { type: "end", label: "End", position: { x: 80, y: 220 } },
    ],
    edges: [{ sourceIndex: 0, targetIndex: 1 }],
  },
  {
    id: "approval",
    name: "Approval workflow",
    description: "Task → human approval → end. Pauses until a user signs off.",
    icon: ShieldCheck,
    nodes: [
      { type: "start", label: "Start", position: { x: 80, y: 80 } },
      { type: "task", label: "Do work", position: { x: 80, y: 200 } },
      { type: "approval", label: "Manager review", position: { x: 80, y: 320 } },
      { type: "end", label: "End", position: { x: 80, y: 440 } },
    ],
    edges: [
      { sourceIndex: 0, targetIndex: 1 },
      { sourceIndex: 1, targetIndex: 2 },
      { sourceIndex: 2, targetIndex: 3 },
    ],
  },
  {
    id: "branch",
    name: "Branch on condition",
    description: "Fan out a task into parallel branches that re-join.",
    icon: GitFork,
    nodes: [
      { type: "start", label: "Start", position: { x: 200, y: 60 } },
      { type: "task", label: "Prepare", position: { x: 200, y: 180 } },
      { type: "parallel", label: "Run in parallel", position: { x: 200, y: 300 } },
      { type: "task", label: "Branch A", position: { x: 60, y: 420 } },
      { type: "task", label: "Branch B", position: { x: 340, y: 420 } },
      { type: "join", label: "Re-join", position: { x: 200, y: 540 } },
      { type: "end", label: "End", position: { x: 200, y: 660 } },
    ],
    edges: [
      { sourceIndex: 0, targetIndex: 1 },
      { sourceIndex: 1, targetIndex: 2 },
      { sourceIndex: 2, targetIndex: 3, label: "a" },
      { sourceIndex: 2, targetIndex: 4, label: "b" },
      { sourceIndex: 3, targetIndex: 5 },
      { sourceIndex: 4, targetIndex: 5 },
      { sourceIndex: 5, targetIndex: 6 },
    ],
  },
  {
    id: "switch",
    name: "Switch routing",
    description: "Route outcomes to different downstream nodes based on a key.",
    icon: ArrowRightLeft,
    nodes: [
      { type: "start", label: "Start", position: { x: 200, y: 60 } },
      { type: "switch", label: "Decide", position: { x: 200, y: 200 } },
      { type: "task", label: "Path: approved", position: { x: 40, y: 360 } },
      { type: "task", label: "Path: rejected", position: { x: 360, y: 360 } },
      { type: "end", label: "End", position: { x: 200, y: 520 } },
    ],
    edges: [
      { sourceIndex: 0, targetIndex: 1 },
      { sourceIndex: 1, targetIndex: 2, label: "approved" },
      { sourceIndex: 1, targetIndex: 3, label: "rejected" },
      { sourceIndex: 2, targetIndex: 4 },
      { sourceIndex: 3, targetIndex: 4 },
    ],
  },
  {
    id: "escalate",
    name: "Escalation ladder",
    description: "Try a task first, then escalate to a senior team if it fails.",
    icon: Sparkles,
    nodes: [
      { type: "start", label: "Start", position: { x: 80, y: 80 } },
      { type: "task", label: "First attempt", position: { x: 80, y: 200 } },
      { type: "escalate", label: "Escalate to lead", position: { x: 80, y: 340 } },
      { type: "end", label: "End", position: { x: 80, y: 480 } },
    ],
    edges: [
      { sourceIndex: 0, targetIndex: 1 },
      { sourceIndex: 1, targetIndex: 2 },
      { sourceIndex: 2, targetIndex: 3 },
    ],
  },
];

const CUSTOM_NODE_TYPES: NodeTypes = {};
let pendingConnectionSource: string | null = null;

function FlowNodeComponent({ id, data, selected }: { id: string; data: { label: string; type: FlowNodeType }; selected?: boolean }) {
  return (
    <div className={clsx(
      "px-3 py-2 rounded-lg border-2 min-w-[100px] text-center",
      selected ? "border-blue-500" : "border-slate-600",
      FLOW_NODE_COLORS[data.type] || "bg-slate-600"
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
        className="react-flow__handle-target !bg-slate-400"
      />
      <div className="text-sm font-medium text-white">{data.label}</div>
      <div className="text-xs text-white/70">{NODE_TYPE_LABELS[data.type]}</div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="react-flow__handle-source !bg-slate-400"
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
  const [dryRunning, setDryRunning] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] = useState<FlowDryRunResult | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [rawSwitchCases, setRawSwitchCases] = useState("");
  const [showTemplates, setShowTemplates] = useState(true);
  const [appliedTemplateId, setAppliedTemplateId] = useState<string | null>(null);
  const [governedWorkers, setGovernedWorkers] = useState<GovernedWorkerOption[]>([]);
  const [modelProfiles, setModelProfiles] = useState<GovernedModelProfile[]>([]);
  const [governanceLoading, setGovernanceLoading] = useState(true);
  const [governanceLoadError, setGovernanceLoadError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function loadGovernanceOptions() {
      setGovernanceLoading(true);
      try {
        const [workersResponse, profilesResponse] = await Promise.all([
          fetch("/api/workers", { signal: controller.signal }),
          fetch("/api/governance/model-profiles", { signal: controller.signal }),
        ]);
        if (!workersResponse.ok || !profilesResponse.ok) {
          throw new Error("The worker registry or Model Profile catalog is unavailable");
        }
        const workers = await workersResponse.json();
        const profiles = await profilesResponse.json();
        setGovernedWorkers(Array.isArray(workers) ? workers : []);
        setModelProfiles(Array.isArray(profiles) ? profiles : []);
        setGovernanceLoadError(null);
      } catch (error) {
        if (!controller.signal.aborted) {
          setGovernanceLoadError(error instanceof Error ? error.message : "Could not load governed worker options");
        }
      } finally {
        if (!controller.signal.aborted) setGovernanceLoading(false);
      }
    }
    void loadGovernanceOptions();
    return () => controller.abort();
  }, []);

  /**
   * Apply a template by replacing the current canvas with the template's
   * scaffold. IDs are unique-ified at apply time so re-applying or switching
   * templates never collides with existing nodes.
   */
  const applyTemplate = useCallback((template: FlowTemplate) => {
    const stamp = Date.now();
    const idMap = template.nodes.map((n) => `${n.type}_${stamp}_${Math.random().toString(36).slice(2, 7)}`);
    const newNodes: Node[] = template.nodes.map((n, i) => ({
      id: idMap[i],
      type: "flowNode",
      position: n.position,
      data: { label: n.label, type: n.type },
    }));
    const newEdges: Edge[] = template.edges.map((e, i) => ({
      id: `e_${stamp}_${i}`,
      source: idMap[e.sourceIndex],
      target: idMap[e.targetIndex],
      label: e.label,
      markerEnd: { type: MarkerType.ArrowClosed },
    }));
    setNodes(newNodes);
    setEdges(newEdges);
    setSelectedNode(null);
    setShowConfig(false);
    setAppliedTemplateId(template.id);
    setShowTemplates(false);
  }, [setNodes, setEdges]);

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

  const handleDryRun = async () => {
    if (nodes.length === 0) {
      setValidationError("Add at least one node before validating the flow");
      return;
    }
    setValidationError(null);
    setDryRunResult(null);
    setDryRunning(true);
    try {
      const definition = convertReactFlowToFlow(nodes, edges);
      const response = await fetch("/api/flows/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ definition_json: definition }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !payload) {
        setValidationError(payload?.error || payload?.detail || "Could not validate the flow");
        return;
      }
      const result = payload as FlowDryRunResult;
      setDryRunResult(result);
      if (!result.valid) {
        setValidationError(
          result.errors.map((error) => error.message).join("; ") ||
            "The flow is not ready to run",
        );
      }
    } catch {
      setValidationError("Network error while validating the flow");
    } finally {
      setDryRunning(false);
    }
  };

  return (
    <div className="h-screen flex flex-col dashboard-page">
      <div className="px-4 pt-4 pb-3">
        <PageHeader
          icon="git-branch"
          title="New orchestration flow"
          description={
            <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-slate-400">
              <span>Compose nodes, then activate when ready.</span>
              <span aria-hidden="true" className="text-slate-600">·</span>
              <span className="inline-flex items-center gap-1">
                <span
                  className={clsx(
                    "inline-block w-1.5 h-1.5 rounded-full",
                    nodes.length === 0 ? "bg-slate-500" : "bg-emerald-400"
                  )}
                  aria-hidden="true"
                />
                {nodes.length} {nodes.length === 1 ? "node" : "nodes"} on canvas
              </span>
              {appliedTemplateId && (
                <>
                  <span aria-hidden="true" className="text-slate-600">·</span>
                  <span className="inline-flex items-center gap-1 text-slate-500">
                    <Sparkles size={11} className="text-blue-400" aria-hidden="true" />
                    From template
                  </span>
                </>
              )}
            </span>
          }
          actions={
            <>
              <Link
                href="/flows"
                prefetch={false}
                aria-label="Back to flows list"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-slate-300 hover:text-white text-sm font-medium rounded-lg border border-slate-700 hover:border-slate-500 hover:bg-slate-800 transition-colors"
              >
                <ArrowLeft size={14} />
                Back
              </Link>
              <label className="inline-flex items-center gap-2 text-sm text-slate-300 px-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                  aria-label="Activate flow on save"
                  className="rounded border-slate-600 bg-slate-900 text-blue-500 focus:ring-2 focus:ring-blue-500/40 focus:ring-offset-0 transition-colors"
                />
                Active
              </label>
              <button
                onClick={handleDryRun}
                disabled={dryRunning}
                aria-busy={dryRunning}
                data-testid="flow-dry-run-button"
                className={clsx(
                  "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border transition-colors",
                  dryRunning
                    ? "border-slate-700 text-slate-500 cursor-wait"
                    : "border-slate-600 text-slate-200 hover:text-white hover:border-slate-400 hover:bg-slate-800"
                )}
              >
                <ShieldCheck size={14} aria-hidden="true" />
                {dryRunning ? "Validating…" : "Validate readiness"}
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                aria-busy={saving}
                data-testid="flow-save-button"
                className={clsx(
                  "inline-flex items-center gap-1.5 px-3.5 py-1.5 text-sm font-medium rounded-lg transition-colors shadow-sm",
                  "focus:outline-none focus:ring-2 focus:ring-blue-500/50",
                  saving
                    ? "bg-blue-700/60 text-blue-100 cursor-wait"
                    : "bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white shadow-blue-500/10"
                )}
              >
                {saving ? (
                  <>
                    <span
                      className="inline-block w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"
                      aria-hidden="true"
                    />
                    Creating…
                  </>
                ) : (
                  <>
                    <Save size={14} aria-hidden="true" />
                    Create Flow
                  </>
                )}
              </button>
            </>
          }
        />

        {/* Name + description strip — sits inside the page header band */}
        <div className="mt-3 rounded-xl border border-slate-800/80 bg-slate-950/35 px-4 py-3 shadow-sm shadow-black/10">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_2fr] gap-3">
            <div>
              <label
                htmlFor="flow-name"
                className="block text-xxs font-semibold uppercase tracking-wider text-slate-500 mb-1"
              >
                Flow name
              </label>
              <input
                id="flow-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Customer onboarding v2"
                data-testid="flow-name-input"
                aria-label="Flow name"
                className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors"
              />
            </div>
            <div>
              <label
                htmlFor="flow-description"
                className="block text-xxs font-semibold uppercase tracking-wider text-slate-500 mb-1"
              >
                Description <span className="text-slate-600 normal-case font-normal">(optional)</span>
              </label>
              <input
                id="flow-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What does this flow do and who owns it?"
                aria-label="Flow description"
                className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors"
              />
            </div>
          </div>
        </div>

        {/* Template suggestions — collapsible, hidden once a template is applied */}
        {showTemplates && (
          <div
            className="mt-3 rounded-xl border border-slate-800/80 bg-slate-950/35 px-4 py-3 shadow-sm shadow-black/10"
            aria-label="Flow template suggestions"
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Sparkles size={14} className="text-blue-400" aria-hidden="true" />
                <h2 className="text-sm font-medium text-slate-200">Start from a template</h2>
                <span className="text-xxs text-slate-500">Optional — pick a starter or build from scratch.</span>
              </div>
              <button
                onClick={() => setShowTemplates(false)}
                aria-label="Hide template suggestions"
                className="p-1 rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500/40"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>
            <div
              className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2"
              role="list"
            >
              {FLOW_TEMPLATES.map((template) => {
                const Icon = template.icon;
                const isApplied = appliedTemplateId === template.id;
                return (
                  <button
                    key={template.id}
                    onClick={() => applyTemplate(template)}
                    role="listitem"
                    aria-label={`Use ${template.name} template`}
                    data-testid={`template-${template.id}`}
                    className={clsx(
                      "group text-left rounded-lg border p-3 transition-all",
                      "focus:outline-none focus:ring-2 focus:ring-blue-500/50",
                      isApplied
                        ? "border-blue-500/60 bg-blue-500/10"
                        : "border-slate-800 bg-slate-900/40 hover:bg-slate-900/80 hover:border-slate-600"
                    )}
                  >
                    <div className="flex items-start gap-2">
                      <div
                        className={clsx(
                          "flex-shrink-0 w-8 h-8 rounded-md border flex items-center justify-center transition-colors",
                          isApplied
                            ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
                            : "bg-slate-800/80 text-slate-400 border-slate-700 group-hover:text-slate-200 group-hover:border-slate-600"
                        )}
                      >
                        <Icon size={14} aria-hidden="true" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="text-sm font-medium text-slate-100 truncate">
                            {template.name}
                          </span>
                          {isApplied && (
                            <CheckCircle2
                              size={12}
                              className="text-blue-400 flex-shrink-0"
                              aria-label="Currently applied"
                            />
                          )}
                        </div>
                        <p className="text-xs text-slate-500 mt-0.5 leading-relaxed line-clamp-2">
                          {template.description}
                        </p>
                        <div className="text-xxs text-slate-600 mt-1.5">
                          {template.nodes.length} nodes · {template.edges.length} edges
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {!showTemplates && (
          <button
            onClick={() => setShowTemplates(true)}
            className="mt-3 inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500/40 rounded px-1 py-0.5"
            aria-label="Show template suggestions"
          >
            <Sparkles size={12} className="text-blue-400" aria-hidden="true" />
            Show templates
          </button>
        )}
      </div>

      {validationError && (
        <div className="px-4 pb-3">
          <ErrorBanner tone="error" title={dryRunResult ? "Flow is not ready" : "Cannot create flow"}>
            {validationError}
          </ErrorBanner>
        </div>
      )}

      {dryRunResult && !validationError && (
        <div className="px-4 pb-3" data-testid="flow-dry-run-result">
          <ErrorBanner tone="info" icon={ShieldCheck} title="Flow is ready to run">
            {dryRunResult.warnings.length > 0
              ? `${dryRunResult.warnings.length} compatibility warning${dryRunResult.warnings.length === 1 ? "" : "s"} recorded.`
              : "Topology, assignments, certified adapters, capabilities, checkpoints, and model policy passed the dry run."}
          </ErrorBanner>
        </div>
      )}

      <div className="flex-1 flex">
        {/* Left panel: node palette */}
        <div className="w-48 border-r border-slate-800 bg-slate-900 p-3 space-y-2">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-3">Node Types</p>
          {NODE_TYPES_OPTIONS.map((type) => (
            <button
              key={type}
              onClick={() => addNode(type)}
              data-testid={`add-node-${type}`}
              className={clsx(
                "w-full px-2 py-1.5 text-xs text-left rounded border border-slate-700 hover:opacity-90 transition-opacity",
                FLOW_NODE_COLORS[type]
              )}
            >
              + {NODE_TYPE_LABELS[type]}
            </button>
          ))}
          <div className="pt-4 border-t border-slate-800">
            <p className="text-xs text-slate-600 leading-relaxed">
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
            className="bg-slate-950"
          >
            <Background color="#374151" gap={16} />
            <Controls className="!bg-slate-800 !border-slate-700" />
            <MiniMap className="!bg-slate-900 !border-slate-700 !pointer-events-none" />
          </ReactFlow>
        </div>

        {/* Right panel: node config */}
        {showConfig && selectedNode && (
          <div className="w-72 border-l border-slate-800 bg-slate-900 p-4 space-y-4 overflow-y-auto">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xxs font-semibold uppercase tracking-wider text-slate-500">
                  Selected node
                </div>
                <h3 className="text-sm font-medium text-white truncate" title={selectedNode.data.label as string}>
                  {selectedNode.data.label as string}
                </h3>
              </div>
              <button
                onClick={() => setShowConfig(false)}
                aria-label="Close node configuration"
                className="p-1 rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500/40"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>

            <div>
              <label className="block text-xs text-slate-500 mb-1">Label</label>
              <input
                value={selectedNode.data.label as string}
                onChange={(e) => updateNodeLabel(selectedNode.id, e.target.value)}
                data-testid="node-label-input"
                className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors"
              />
            </div>

            <div>
              <label className="block text-xs text-slate-500 mb-1">Type</label>
              <div className={clsx("px-2 py-1 rounded text-xs", FLOW_NODE_COLORS[selectedNode.data.type as FlowNodeType])}>
                {NODE_TYPE_LABELS[selectedNode.data.type as FlowNodeType]}
              </div>
            </div>

            {selectedNode.data.type === "task" && (
              <>
                <div className="rounded-md border border-blue-900/70 bg-blue-950/20 p-2.5 text-xs text-blue-100">
                  <div className="font-medium">Governed worker assignment</div>
                  <p className="mt-1 text-blue-200/70 leading-relaxed">
                    Select a certified worker and Model Profile. Readiness validation checks the selected immutable runtime records before this flow can run.
                  </p>
                </div>
                {governanceLoadError && (
                  <div className="rounded-md border border-amber-800/70 bg-amber-950/20 p-2 text-xs text-amber-200">
                    {governanceLoadError}. You can still enter a legacy team/action assignment, but it cannot pass governed readiness.
                  </div>
                )}
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Assigned Worker</label>
                  <select
                    value={(nodeConfig.worker_id as string) || ""}
                    onChange={(event) => {
                      const selected = governedWorkers.find((worker) => worker.id === event.target.value);
                      const next: Record<string, unknown> = {
                        ...nodeConfig,
                        worker_id: event.target.value || undefined,
                        team_id: undefined,
                      };
                      if (selected?.model_mode) next.model_mode = selected.model_mode;
                      if (selected?.model_profile_id) next.model_profile_id = selected.model_profile_id;
                      updateNodeConfig(next);
                    }}
                    disabled={governanceLoading}
                    data-testid="task-worker-select"
                    className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white transition-colors"
                  >
                    <option value="">{governanceLoading ? "Loading workers…" : "Select a governed worker"}</option>
                    {governedWorkers.map((worker) => (
                      <option key={worker.id} value={worker.id}>
                        {worker.name} · {worker.status || "UNKNOWN"} · {worker.adapter_type || "runtime"}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Model Mode</label>
                  <select
                    value={(nodeConfig.model_mode as string) || "aiat_gateway"}
                    onChange={(event) => updateNodeConfig({ ...nodeConfig, model_mode: event.target.value, ...(event.target.value === "none" ? { model_profile_id: undefined } : {}) })}
                    data-testid="task-model-mode-select"
                    className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white transition-colors"
                  >
                    <option value="aiat_gateway">AIAT gateway</option>
                    <option value="certified_external_runtime">Certified external runtime</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="none">No model</option>
                  </select>
                </div>
                {(nodeConfig.model_mode as string || "aiat_gateway") !== "none" && (
                  <div>
                    <label className="block text-xs text-slate-500 mb-1">Model Profile</label>
                    <select
                      value={(nodeConfig.model_profile_id as string) || ""}
                      onChange={(event) => updateNodeConfig({ ...nodeConfig, model_profile_id: event.target.value || undefined })}
                      disabled={governanceLoading}
                      data-testid="task-model-profile-select"
                      className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white transition-colors"
                    >
                      <option value="">Select approved Model Profile</option>
                      {modelProfiles.filter((profile) => profile.status === "approved").map((profile) => (
                        <option key={profile.profile_id} value={profile.profile_id}>
                          {profile.profile_id}{profile.purpose ? ` · ${profile.purpose}` : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Task Type</label>
                  <input value={(nodeConfig.task_type as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, task_type: e.target.value })} placeholder="code_review" data-testid="task-type-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Required Capabilities</label>
                  <input value={csvValues(nodeConfig.required_capabilities)} onChange={(e) => updateNodeConfig({ ...nodeConfig, required_capabilities: parseCsv(e.target.value) })} placeholder="qa.test_execute, artifact.capture" data-testid="task-capabilities-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Workspace Mode</label>
                  <select value={(nodeConfig.project_workspace_mode as string) || "isolated"} onChange={(e) => updateNodeConfig({ ...nodeConfig, project_workspace_mode: e.target.value })} data-testid="task-workspace-mode-select" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white transition-colors">
                    <option value="isolated">Isolated</option>
                    <option value="shared_readonly">Shared read-only</option>
                    <option value="approved_write">Approved write</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Permission Requirements</label>
                  <input value={csvValues(nodeConfig.permission_requirements)} onChange={(e) => updateNodeConfig({ ...nodeConfig, permission_requirements: parseCsv(e.target.value) })} placeholder="repository.read" data-testid="task-permissions-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Tool Grants</label>
                  <input value={csvValues(nodeConfig.tool_grants)} onChange={(e) => updateNodeConfig({ ...nodeConfig, tool_grants: parseCsv(e.target.value) })} placeholder="opencode.workspace_read" data-testid="task-tool-grants-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Team ID</label>
                  <input value={(nodeConfig.team_id as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, team_id: e.target.value })} placeholder="office_cto" data-testid="task-team-id-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Action</label>
                  <input value={(nodeConfig.action as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, action: e.target.value })} placeholder="execute_task" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Timeout (seconds)</label>
                  <input type="number" value={Number(nodeConfig.timeout_seconds) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, timeout_seconds: parseInt(e.target.value) || 0 })} placeholder="300" data-testid="task-timeout-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Retries</label>
                  <input type="number" min="0" value={Number(nodeConfig.retries) || 0} onChange={(e) => {
                    const retries = Math.max(0, parseInt(e.target.value, 10) || 0);
                    const current = (nodeConfig.retry_policy as Record<string, unknown> | undefined) || {};
                    updateNodeConfig({ ...nodeConfig, retries, retry_policy: { ...current, max_attempts: retries + 1 } });
                  }} placeholder="3" data-testid="task-retries-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                  <p className="mt-1 text-xxs text-slate-500">Retries are persisted as a governed retry policy with the same immutable run idempotency scope.</p>
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Retry Strategies</label>
                  <input value={csvValues((nodeConfig.retry_policy as { strategies?: string[] } | undefined)?.strategies)} onChange={(e) => {
                    const current = (nodeConfig.retry_policy as Record<string, unknown> | undefined) || {};
                    updateNodeConfig({ ...nodeConfig, retry_policy: { ...current, strategies: parseCsv(e.target.value) } });
                  }} placeholder="same_version, checkpoint" data-testid="task-retry-strategies-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Retry Backoff (seconds)</label>
                  <input type="number" min="0" value={Number((nodeConfig.retry_policy as { backoff_seconds?: number } | undefined)?.backoff_seconds) || ""} onChange={(e) => {
                    const current = (nodeConfig.retry_policy as Record<string, unknown> | undefined) || {};
                    updateNodeConfig({ ...nodeConfig, retry_policy: { ...current, backoff_seconds: Math.max(0, Number(e.target.value) || 0) } });
                  }} placeholder="15" data-testid="task-retry-backoff-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Budget Limit (USD)</label>
                  <input type="number" min="0" step="0.01" value={Number((nodeConfig.budget as { max_cost_usd?: number } | undefined)?.max_cost_usd) || ""} onChange={(e) => {
                    const current = (nodeConfig.budget as Record<string, number> | undefined) || {};
                    updateNodeConfig({ ...nodeConfig, budget: { ...current, max_cost_usd: Math.max(0, Number(e.target.value) || 0) } });
                  }} placeholder="5.00" data-testid="task-budget-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Checkpoint Mode</label>
                  <select value={(nodeConfig.checkpoint_policy as { mode?: string } | undefined)?.mode || "unsupported"} onChange={(e) => {
                    const current = (nodeConfig.checkpoint_policy as Record<string, unknown> | undefined) || {};
                    updateNodeConfig({ ...nodeConfig, checkpoint_policy: { ...current, mode: e.target.value } });
                  }} data-testid="task-checkpoint-mode-select" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white transition-colors">
                    <option value="unsupported">Unsupported</option>
                    <option value="restart_only">Restart only</option>
                    <option value="wrapper">Adapter wrapper</option>
                    <option value="native">Native checkpoint</option>
                  </select>
                </div>
                <label className="flex items-center gap-2 text-xs text-slate-300">
                  <input type="checkbox" checked={Boolean((nodeConfig.checkpoint_policy as { required?: boolean } | undefined)?.required)} onChange={(e) => {
                    const current = (nodeConfig.checkpoint_policy as Record<string, unknown> | undefined) || {};
                    updateNodeConfig({ ...nodeConfig, checkpoint_policy: { ...current, required: e.target.checked } });
                  }} data-testid="task-checkpoint-required" />
                  Require a checkpoint before completion
                </label>
                <label className="flex items-center gap-2 text-xs text-slate-300">
                  <input type="checkbox" checked={(nodeConfig.cancellation_policy as { cooperative?: boolean } | undefined)?.cooperative !== false} onChange={(e) => {
                    const current = (nodeConfig.cancellation_policy as Record<string, unknown> | undefined) || {};
                    updateNodeConfig({ ...nodeConfig, cancellation_policy: { ...current, cooperative: e.target.checked } });
                  }} data-testid="task-cooperative-cancel" />
                  Require cooperative cancellation
                </label>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Force Cancellation After (seconds)</label>
                  <input type="number" min="1" value={Number((nodeConfig.cancellation_policy as { force_after_seconds?: number } | undefined)?.force_after_seconds) || ""} onChange={(e) => {
                    const current = (nodeConfig.cancellation_policy as Record<string, unknown> | undefined) || {};
                    const value = parseInt(e.target.value, 10);
                    updateNodeConfig({ ...nodeConfig, cancellation_policy: { ...current, force_after_seconds: Number.isFinite(value) && value > 0 ? value : undefined } });
                  }} placeholder="60" data-testid="task-force-cancel-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Required Artifacts</label>
                  <input value={csvValues(((nodeConfig.artifact_expectations as Array<{ name?: string }> | undefined) || []).map((artifact) => artifact.name || ""))} onChange={(e) => updateNodeConfig({ ...nodeConfig, artifact_expectations: parseCsv(e.target.value).map((name) => ({ name, required: true })) })} placeholder="test-report.xml, coverage.json" data-testid="task-artifacts-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Completion Criterion</label>
                  <input value={String((nodeConfig.completion_criteria as { required_output?: string } | undefined)?.required_output || "")} onChange={(e) => updateNodeConfig({ ...nodeConfig, completion_criteria: { required_output: e.target.value } })} placeholder="all required tests pass" data-testid="task-completion-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Escalate To Team</label>
                  <input value={(nodeConfig.escalate_to_team as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_team: e.target.value })} placeholder="exec_ceo" data-testid="task-escalate-team-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Or Escalate To Agent</label>
                  <input value={(nodeConfig.escalate_to_agent as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_agent: e.target.value })} placeholder="agent_id" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
              </>
            )}

            {selectedNode.data.type === "approval" && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Approver Role</label>
                  <input value={(nodeConfig.approver_role as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_role: e.target.value })} placeholder="exec_ceo" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Or Approver User</label>
                  <input value={(nodeConfig.approver_user as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_user: e.target.value })} placeholder="human" data-testid="approval-user-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
              </>
            )}

            {selectedNode.data.type === "condition" && (
              <div>
                <label className="block text-xs text-slate-500 mb-1">Expression</label>
                <input value={(nodeConfig.expression as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, expression: e.target.value })} placeholder="node_X completed" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
              </div>
            )}

            {selectedNode.data.type === "switch" && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Switch Key</label>
                  <input value={(nodeConfig.switch_key as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, switch_key: e.target.value })} placeholder="decision_result" data-testid="switch-key-input" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Switch Cases (JSON)</label>
                  <div className="space-y-2 mb-2">
                    {switchCaseEntries.map(([caseKey, target], index) => (
                      <div key={`${caseKey}-${index}`} className="grid grid-cols-2 gap-2">
                        <input
                          value={caseKey}
                          onChange={(e) => updateSwitchCase(index, "key", e.target.value)}
                          placeholder="approved"
                          data-testid={`switch-case-key-${index}`}
                          className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors"
                        />
                        <input
                          value={target}
                          onChange={(e) => updateSwitchCase(index, "target", e.target.value)}
                          placeholder="target_node_id"
                          data-testid={`switch-case-target-${index}`}
                          className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors"
                        />
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={addSwitchCase}
                      data-testid="switch-case-add-button"
                      className="w-full px-2 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs rounded"
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
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white h-20 font-mono"
                  />
                  {jsonError && <div className="text-xs text-red-400 mt-1">{jsonError}</div>}
                </div>
              </>
            )}

            {selectedNode.data.type === "escalate" && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Escalate To Team</label>
                  <input value={(nodeConfig.escalate_to_team as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_team: e.target.value })} placeholder="exec_ceo" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Or Escalate To Agent</label>
                  <input value={(nodeConfig.escalate_to_agent as string) || ""} onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_agent: e.target.value })} placeholder="agent_id" className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors" />
                </div>
              </>
            )}

            {selectedNode.data.type === "parallel" && (
              <div>
                <label className="block text-xs text-slate-500 mb-1">Branches (comma-separated node IDs)</label>
                <input
                  value={((nodeConfig.branches as string[]) || []).join(", ")}
                  onChange={(e) => updateNodeConfig({ ...nodeConfig, branches: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })}
                  placeholder="branch_1, branch_2"
                  className="w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors"
                />
              </div>
            )}

            <div>
              <label className="block text-xs text-slate-500 mb-1">Description (optional)</label>
              <textarea
                value={(nodeConfig.description as string) || ""}
                onChange={(e) => updateNodeConfig({ ...nodeConfig, description: e.target.value })}
                placeholder="What does this node do?"
                className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white h-16"
              />
            </div>

            <button
              onClick={deleteSelectedNode}
              aria-label="Delete this node from the flow"
              className="w-full inline-flex items-center justify-center gap-1.5 px-2.5 py-1.5 bg-red-950/30 text-red-300 border border-red-800/70 text-xs font-medium rounded-md hover:bg-red-900/40 hover:border-red-700 active:bg-red-900/60 transition-colors focus:outline-none focus:ring-2 focus:ring-red-500/40"
            >
              <Trash2 size={12} aria-hidden="true" />
              Delete Node
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
