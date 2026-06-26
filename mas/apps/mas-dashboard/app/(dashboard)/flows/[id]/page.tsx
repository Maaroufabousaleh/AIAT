"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import { ArrowLeft, Save, Copy, X, AlertTriangle, Undo2, Redo2, GitBranch, CheckCircle2, Circle, History, Info } from "lucide-react";
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
  type FlowNodeType,
} from "@/lib/flow-types";
import { convertFlowToReactFlow, convertReactFlowToFlow } from "@/lib/flow-editor";
import { KpiCard } from "@/components/ui/KpiCard";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

const CUSTOM_NODE_TYPES: NodeTypes = {};
let pendingConnectionSource: string | null = null;

function FlowNodeComponent({ id, data, selected }: { id: string; data: { label: string; type: FlowNodeType }; selected?: boolean }) {
  return (
    <div className={clsx(
      "px-3 py-2 rounded-lg border-2 min-w-[100px] text-center shadow-md shadow-black/30",
      selected ? "border-blue-400 ring-2 ring-blue-400/40" : "border-slate-700",
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
        className="react-flow__handle-target !bg-slate-300"
      />
      <div className="text-sm font-medium text-white">{data.label}</div>
      <div className="text-xs text-white/80">{NODE_TYPE_LABELS[data.type]}</div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="react-flow__handle-source !bg-slate-300"
      />
    </div>
  );
}

CUSTOM_NODE_TYPES.flowNode = FlowNodeComponent;

const NODE_TYPES_OPTIONS: FlowNodeType[] = ["start", "task", "approval", "condition", "parallel", "join", "switch", "escalate", "end"];

export default function FlowEditorPage() {
  const router = useRouter();
  const { currentFlow, fetchFlow, createFlow, updateFlow, loading } = useFlowStore();
  
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isActive, setIsActive] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([] as Edge[]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [nodeConfig, setNodeConfig] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  // Save status indicator: 'idle' (no changes), 'dirty' (unsaved), 'saving', 'saved', 'error'
  const [saveStatus, setSaveStatus] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [rawSwitchCases, setRawSwitchCases] = useState("");
  // Undo/redo history stacks — store serializable snapshots of the canvas.
  const [history, setHistory] = useState<{ nodes: Node[]; edges: Edge[] }[]>([]);
  const [redoStack, setRedoStack] = useState<{ nodes: Node[]; edges: Edge[] }[]>([]);
  const [historyVersion, setHistoryVersion] = useState(0);

  const isNew = !currentFlow?.id;

  useEffect(() => {
    const flowId = window.location.pathname.split("/").pop();
    if (flowId && flowId !== "new") {
      fetchFlow(flowId).then((flow) => {
        if (flow) {
          setName(flow.name);
          setDescription(flow.description || "");
          setIsActive(flow.is_active);
          const { nodes: rawNodes, edges: rawEdges } = convertFlowToReactFlow(
            flow.definition_json?.nodes || [],
            flow.definition_json?.edges || []
          );
          const flowNodes = rawNodes.map((node) => ({
            ...node,
            markerEnd: { type: MarkerType.ArrowClosed },
          }));
          const flowEdges = rawEdges.map((edge) => ({
            ...edge,
            markerEnd: { type: MarkerType.ArrowClosed },
            style: { stroke: "#6b7280" },
          }));
          setNodes(flowNodes);
          setEdges(flowEdges);
          setSaveStatus("saved");
          setLastSavedAt(new Date(flow.updated_at));
        }
      });
    }
  }, [fetchFlow, setNodes, setEdges]);

  // Track canvas edits in the undo stack. We sample on a debounce so quick
  // drags (which fire many change events) don't fill the history. The version
  // counter is the trigger for this effect.
  useEffect(() => {
    if (historyVersion === 0) return;
    setHistory((prev) => [...prev.slice(-49), { nodes, edges }]);
    setRedoStack([]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyVersion]);

  const undo = useCallback(() => {
    setHistory((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      setRedoStack((r) => [...r, { nodes, edges }]);
      setNodes(last.nodes);
      setEdges(last.edges);
      setSaveStatus("dirty");
      return prev.slice(0, -1);
    });
  }, [nodes, edges, setNodes, setEdges]);

  const redo = useCallback(() => {
    setRedoStack((prev) => {
      if (prev.length === 0) return prev;
      const next = prev[prev.length - 1];
      setHistory((h) => [...h, { nodes, edges }]);
      setNodes(next.nodes);
      setEdges(next.edges);
      setSaveStatus("dirty");
      return prev.slice(0, -1);
    });
  }, [nodes, edges, setNodes, setEdges]);

  // Keyboard shortcuts: Ctrl/Cmd+Z = undo, Ctrl/Cmd+Shift+Z or Ctrl+Y = redo.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const isMeta = event.metaKey || event.ctrlKey;
      if (!isMeta) return;
      const target = event.target as HTMLElement | null;
      // Don't capture undo/redo while the user is typing in an input/textarea.
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      if (event.key === "z" && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if ((event.key === "z" && event.shiftKey) || event.key === "y") {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [undo, redo]);

  // Node-count summary used in the header strip. Memoized so the toolbar
  // doesn't re-render on every drag tick.
  const nodeSummary = useMemo(() => {
    const counts: Record<FlowNodeType, number> = {
      start: 0, end: 0, task: 0, approval: 0, condition: 0,
      parallel: 0, join: 0, switch: 0, escalate: 0,
    };
    for (const n of nodes) {
      const t = n.data?.type as FlowNodeType | undefined;
      if (t && counts[t] !== undefined) counts[t] += 1;
    }
    return { total: nodes.length, edges: edges.length, counts };
  }, [nodes, edges]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, id: `e${Date.now()}`, markerEnd: { type: MarkerType.ArrowClosed } }, eds));
      setSaveStatus("dirty");
      setHistoryVersion((v) => v + 1);
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
      setSaveStatus("dirty");
      setHistoryVersion((v) => v + 1);
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
    setSaveStatus("dirty");
    setHistoryVersion((v) => v + 1);
  }, [nodes.length, setNodes]);

  const updateNodeLabel = useCallback((nodeId: string, label: string) => {
    setNodes((nds) =>
      nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, label } } : n))
    );
    setSelectedNode((node) => (node?.id === nodeId ? { ...node, data: { ...node.data, label } } : node));
    setSaveStatus("dirty");
  }, [setNodes]);

  const updateNodeConfig = useCallback((config: Record<string, unknown>) => {
    setNodeConfig(config);
    if (selectedNode) {
      setNodes((nds) =>
        nds.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, config } } : n))
      );
      setSelectedNode((node) => (node ? { ...node, data: { ...node.data, config } } : node));
      setSaveStatus("dirty");
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
      setSaveStatus("dirty");
      setHistoryVersion((v) => v + 1);
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
    setSaveStatus("saving");

    const definition = convertReactFlowToFlow(nodes, edges);

    try {
      if (isNew) {
        const created = await createFlow({
          name,
          description,
          definition_json: definition,
          is_active: isActive,
        });
        if (created) {
          setSaveStatus("saved");
          setLastSavedAt(new Date());
          router.push(`/flows/${created.id}`);
        } else {
          setSaveStatus("error");
        }
      } else {
        const updated = await updateFlow(currentFlow!.id, {
          name,
          description,
          definition_json: definition,
          is_active: isActive,
        });
        if (updated) {
          setSaveStatus("saved");
          setLastSavedAt(new Date());
        } else {
          setSaveStatus("error");
        }
      }
    } catch (err) {
      setSaveStatus("error");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAsNewVersion = async () => {
    const validation = validateFlow();
    if (validation || !currentFlow) {
      setValidationError(validation);
      return;
    }
    setValidationError(null);
    setSaving(true);
    setSaveStatus("saving");

    try {
      const created = await createFlow({
        name,
        description,
        definition_json: convertReactFlowToFlow(nodes, edges),
        is_active: isActive,
        version_from_flow_id: currentFlow.id,
      });
      if (created) {
        setSaveStatus("saved");
        setLastSavedAt(new Date());
        router.push(`/flows/${created.id}`);
      } else {
        setSaveStatus("error");
      }
    } catch (err) {
      setSaveStatus("error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-screen flex flex-col bg-slate-950 text-slate-100">
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 border-b border-slate-800 bg-slate-900/80 backdrop-blur">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            href="/flows"
            aria-label="Back to flows"
            className="p-1.5 rounded-md text-slate-500 hover:text-slate-200 hover:bg-slate-800 transition-colors"
          >
            <ArrowLeft size={18} />
          </Link>
          <div className="min-w-0">
            <input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setSaveStatus("dirty");
              }}
              placeholder="Flow name..."
              aria-label="Flow name"
              data-testid="flow-name-input"
              className="text-lg font-semibold bg-transparent text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-400/60 rounded px-1 -mx-1"
            />
            {currentFlow && (
              <p className="text-xs text-slate-500 mt-0.5">
                v{currentFlow.version} · Updated {formatDistanceToNow(new Date(currentFlow.updated_at), { addSuffix: true })}
              </p>
            )}
          </div>
          {/* Save status indicator — single source of truth for the user about
              whether their work is persisted. */}
          <SaveStatusBadge status={saveStatus} lastSavedAt={lastSavedAt} />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Undo / Redo buttons + keyboard shortcut hints */}
          <div className="flex items-center gap-1 mr-1">
            <button
              type="button"
              onClick={undo}
              disabled={history.length === 0}
              aria-label="Undo last change"
              title="Undo (Ctrl+Z)"
              data-testid="flow-undo-button"
              className="p-1.5 rounded-md text-slate-300 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed border border-slate-700 transition-colors"
            >
              <Undo2 size={14} />
            </button>
            <button
              type="button"
              onClick={redo}
              disabled={redoStack.length === 0}
              aria-label="Redo last undone change"
              title="Redo (Ctrl+Shift+Z)"
              data-testid="flow-redo-button"
              className="p-1.5 rounded-md text-slate-300 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed border border-slate-700 transition-colors"
            >
              <Redo2 size={14} />
            </button>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-300 mr-2 select-none cursor-pointer">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(e) => {
                setIsActive(e.target.checked);
                setSaveStatus("dirty");
              }}
              aria-label="Mark flow as active"
              className="rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-400/60"
            />
            Active
          </label>
          <button
            onClick={handleSave}
            disabled={saving || loading}
            data-testid="flow-save-button"
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 active:bg-blue-700 disabled:bg-slate-800 disabled:text-slate-500 text-white text-sm rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400/60"
          >
            <Save size={14} />
            {saving ? "Saving..." : "Save"}
          </button>
          {currentFlow && (
            <button
              onClick={handleSaveAsNewVersion}
              disabled={saving || loading}
              data-testid="flow-save-version-button"
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 active:bg-slate-600 disabled:bg-slate-900 disabled:text-slate-500 text-white text-sm rounded-lg border border-slate-700 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400/60"
            >
              <Copy size={14} />
              Save As New Version
            </button>
          )}
        </div>
      </div>

      {/* Node count summary strip — total / edges / undo depth, with KPI tiles
          for each node type so the user can see composition at a glance. */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-slate-800 bg-slate-900/45">
        <KpiCard
          label="Total nodes"
          value={nodeSummary.total}
          icon="git-branch"
          tone="info"
          className="min-w-[10rem] py-2.5"
        />
        <KpiCard
          label="Edges"
          value={nodeSummary.edges}
          icon="network"
          tone="neutral"
          className="min-w-[8rem] py-2.5"
        />
        <KpiCard
          label="History depth"
          value={history.length}
          hint={redoStack.length > 0 ? `${redoStack.length} pending redo` : "no redo available"}
          icon="clock"
          tone="neutral"
          className="min-w-[12rem] py-2.5"
        />
        <div className="flex flex-wrap items-center gap-1.5 ml-1" aria-label="Node composition">
          {NODE_TYPES_OPTIONS.map((type) => {
            const count = nodeSummary.counts[type] ?? 0;
            if (count === 0) return null;
            return (
              <span
                key={type}
                data-testid={`node-count-${type}`}
                className={clsx(
                  "inline-flex items-center gap-1.5 rounded-md border border-slate-700/80 px-2 py-0.5 text-xxs font-medium text-white/90 shadow-sm shadow-black/20",
                  FLOW_NODE_COLORS[type]
                )}
              >
                <span>{NODE_TYPE_LABELS[type]}</span>
                <span className="inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded bg-slate-950/40 text-xxs font-semibold text-white">
                  {count}
                </span>
              </span>
            );
          })}
        </div>
        <div className="ml-auto hidden sm:flex items-center gap-1 text-xxs text-slate-500" title="Keyboard shortcuts">
          <History size={12} />
          <span>Ctrl+Z undo</span>
          <span aria-hidden="true">·</span>
          <span>Ctrl+Shift+Z redo</span>
        </div>
      </div>

      {validationError && (
        <div className="px-4 py-2 border-b border-slate-800">
          <ErrorBanner tone="error" title="Cannot save flow" icon={AlertTriangle}>
            {validationError}
          </ErrorBanner>
        </div>
      )}

      <div className="flex-1 flex">
        <div className="w-48 border-r border-slate-800 bg-slate-900/60 p-3 space-y-2">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">Add Node</p>
          {NODE_TYPES_OPTIONS.map((type) => (
            <button
              key={type}
              onClick={() => addNode(type)}
              data-testid={`add-node-${type}`}
              aria-label={`Add ${NODE_TYPE_LABELS[type]} node`}
              className={clsx(
                "w-full px-2 py-1.5 text-xs text-left rounded border border-slate-700/80 hover:brightness-110 hover:border-slate-600 active:scale-[0.98] transition focus:outline-none focus:ring-2 focus:ring-blue-400/60 text-white",
                FLOW_NODE_COLORS[type]
              )}
            >
              {NODE_TYPE_LABELS[type]}
            </button>
          ))}
          <div className="pt-2 mt-2 border-t border-slate-800/80 text-xxs text-slate-500 leading-snug flex items-start gap-1.5">
            <Info size={12} className="flex-shrink-0 mt-0.5 text-slate-600" />
            <span>Drag from a node&apos;s bottom handle to another node to connect them.</span>
          </div>
        </div>

        <div className="flex-1 relative">
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
            <Background color="#334155" gap={16} />
            <Controls className="!bg-slate-800 !border-slate-700 [&>button]:!bg-slate-800 [&>button]:!border-slate-700 [&>button]:!text-slate-200 [&>button:hover]:!bg-slate-700" />
            <MiniMap className="!bg-slate-900 !border-slate-700 !pointer-events-none" />
          </ReactFlow>
          {/* Dirty canvas indicator — corner badge that surfaces the unsaved
              state directly on the canvas. */}
          {saveStatus === "dirty" && (
            <div
              role="status"
              aria-live="polite"
              className="absolute top-3 right-3 inline-flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-950/70 px-2.5 py-1 text-xxs font-medium text-amber-200 shadow-md shadow-black/30"
            >
              <Circle size={8} className="fill-amber-400 text-amber-400" />
              Unsaved changes
            </div>
          )}
        </div>

        {showConfig && selectedNode && (
          <div className="w-72 border-l border-slate-800 bg-slate-900/60 p-4 space-y-4 overflow-y-auto">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-white">Node Config</h3>
              <button
                onClick={() => setShowConfig(false)}
                aria-label="Close node config panel"
                className="p-1 rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800 transition-colors"
              >
                <X size={14} />
              </button>
            </div>

            <div>
              <label className="block text-xs text-slate-500 mb-1">Label</label>
              <input
                value={selectedNode.data.label as string}
                onChange={(e) => updateNodeLabel(selectedNode.id, e.target.value)}
                data-testid="node-label-input"
                aria-label="Node label"
                className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
              />
            </div>

            <div>
              <label className="block text-xs text-slate-500 mb-1">Type</label>
              <div className={clsx(
                "inline-block px-2 py-1 rounded text-xs text-white border border-slate-700/80",
                FLOW_NODE_COLORS[selectedNode.data.type as FlowNodeType]
              )}>
                {NODE_TYPE_LABELS[selectedNode.data.type as FlowNodeType]}
              </div>
            </div>

            {selectedNode.data.type === "task" && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Team ID</label>
                  <input
                    value={(nodeConfig.team_id as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, team_id: e.target.value })}
                    placeholder="office_cto"
                    data-testid="task-team-id-input"
                    aria-label="Team ID"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Action</label>
                  <input
                    value={(nodeConfig.action as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, action: e.target.value })}
                    placeholder="execute_task"
                    aria-label="Action"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Timeout (seconds)</label>
                  <input
                    type="number"
                    value={Number(nodeConfig.timeout_seconds) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, timeout_seconds: parseInt(e.target.value) || 0 })}
                    placeholder="300"
                    data-testid="task-timeout-input"
                    aria-label="Timeout in seconds"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Retries</label>
                  <input
                    type="number"
                    value={Number(nodeConfig.retries) || 0}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, retries: parseInt(e.target.value) || 0 })}
                    placeholder="3"
                    data-testid="task-retries-input"
                    aria-label="Retries"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Escalate To Team</label>
                  <input
                    value={(nodeConfig.escalate_to_team as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_team: e.target.value })}
                    placeholder="exec_ceo"
                    data-testid="task-escalate-team-input"
                    aria-label="Escalate to team"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Or Escalate To Agent</label>
                  <input
                    value={(nodeConfig.escalate_to_agent as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_agent: e.target.value })}
                    placeholder="agent_id"
                    aria-label="Escalate to agent"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
              </>
            )}

            {selectedNode.data.type === "approval" && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Approver Role</label>
                  <input
                    value={(nodeConfig.approver_role as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_role: e.target.value })}
                    placeholder="exec_ceo"
                    aria-label="Approver role"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Or Approver User</label>
                  <input
                    value={(nodeConfig.approver_user as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, approver_user: e.target.value })}
                    placeholder="human"
                    data-testid="approval-user-input"
                    aria-label="Approver user"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
              </>
            )}

            {selectedNode.data.type === "condition" && (
              <div>
                <label className="block text-xs text-slate-500 mb-1">Expression</label>
                <input
                  value={(nodeConfig.expression as string) || ""}
                  onChange={(e) => updateNodeConfig({ ...nodeConfig, expression: e.target.value })}
                  placeholder="node_X completed"
                  aria-label="Condition expression"
                  className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                />
              </div>
            )}

            {selectedNode.data.type === "switch" && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Switch Key (context field)</label>
                  <input
                    value={(nodeConfig.switch_key as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, switch_key: e.target.value })}
                    placeholder="decision_result"
                    data-testid="switch-key-input"
                    aria-label="Switch key"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
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
                          aria-label="Switch case key"
                          className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                        />
                        <input
                          value={target}
                          onChange={(e) => updateSwitchCase(index, "target", e.target.value)}
                          placeholder="target_node_id"
                          data-testid={`switch-case-target-${index}`}
                          aria-label="Switch case target"
                          className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                        />
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={addSwitchCase}
                      data-testid="switch-case-add-button"
                      className="w-full px-2 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs rounded transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                    >
                      Add Case
                    </button>
                  </div>
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
                    aria-label="Switch cases raw JSON"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white h-20 font-mono focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                  {jsonError && <div className="text-xs text-rose-400 mt-1">{jsonError}</div>}
                </div>
              </>
            )}

            {selectedNode.data.type === "escalate" && (
              <>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Escalate To Team</label>
                  <input
                    value={(nodeConfig.escalate_to_team as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_team: e.target.value })}
                    placeholder="exec_ceo"
                    aria-label="Escalate to team"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Or Escalate To Agent</label>
                  <input
                    value={(nodeConfig.escalate_to_agent as string) || ""}
                    onChange={(e) => updateNodeConfig({ ...nodeConfig, escalate_to_agent: e.target.value })}
                    placeholder="agent_id"
                    aria-label="Escalate to agent"
                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                  />
                </div>
              </>
            )}

            {selectedNode.data.type === "parallel" && (
              <div>
                <label className="block text-xs text-slate-500 mb-1">Branches (comma-separated node IDs)</label>
                <input
                  value={((nodeConfig.branches as string[]) || []).join(", ")}
                  onChange={(e) => updateNodeConfig({
                    ...nodeConfig,
                    branches: e.target.value.split(",").map(s => s.trim()).filter(Boolean)
                  })}
                  placeholder="branch_1, branch_2"
                  aria-label="Parallel branches"
                  className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-400/60"
                />
              </div>
            )}

            <button
              onClick={deleteSelectedNode}
              className="w-full px-2 py-1.5 bg-rose-600/20 text-rose-300 border border-rose-800/60 text-xs rounded hover:bg-rose-600/30 active:bg-rose-600/40 transition-colors focus:outline-none focus:ring-2 focus:ring-rose-400/60"
            >
              Delete Node
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Small badge that reflects whether the canvas is saved, dirty, or saving.
 * Lives next to the flow name so the user always sees it.
 */
function SaveStatusBadge({
  status,
  lastSavedAt,
}: {
  status: "idle" | "dirty" | "saving" | "saved" | "error";
  lastSavedAt: Date | null;
}) {
  const cfg: Record<typeof status, { wrap: string; dot: string; label: string }> = {
    idle: {
      wrap: "border-slate-700/70 bg-slate-800/60 text-slate-400",
      dot: "bg-slate-500",
      label: "Not yet edited",
    },
    dirty: {
      wrap: "border-amber-500/30 bg-amber-950/40 text-amber-200",
      dot: "bg-amber-400",
      label: "Unsaved changes",
    },
    saving: {
      wrap: "border-blue-500/30 bg-blue-950/40 text-blue-200",
      dot: "bg-blue-400 animate-pulse",
      label: "Saving…",
    },
    saved: {
      wrap: "border-emerald-500/30 bg-emerald-950/40 text-emerald-200",
      dot: "bg-emerald-400",
      label: lastSavedAt
        ? `Saved ${formatDistanceToNow(lastSavedAt, { addSuffix: true })}`
        : "Saved",
    },
    error: {
      wrap: "border-rose-500/30 bg-rose-950/40 text-rose-200",
      dot: "bg-rose-400",
      label: "Save failed",
    },
  };
  const c = cfg[status];
  return (
    <span
      role="status"
      aria-live="polite"
      data-testid="flow-save-status"
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xxs font-medium transition-colors",
        c.wrap
      )}
    >
      {status === "saved" ? (
        <CheckCircle2 size={12} className="text-emerald-400" />
      ) : (
        <span className={clsx("inline-block w-1.5 h-1.5 rounded-full", c.dot)} />
      )}
      {c.label}
    </span>
  );
}
