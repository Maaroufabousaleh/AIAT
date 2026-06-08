"use client";

import { useEffect, useCallback, useMemo, useState } from "react";
import { clsx } from "clsx";
import {
  Network,
  Users,
  GitBranch,
  ChevronRight,
  ChevronLeft,
  RefreshCw,
  ArrowRight,
  Search,
  Copy,
  Clock,
  X,
  Home,
  AlertCircle,
} from "lucide-react";

import { useSystemVizStore } from "@/lib/system-viz-store";
import { HierarchyViz } from "@/components/system-viz/HierarchyViz";
import { PermissionsViz } from "@/components/system-viz/PermissionsViz";
import { OrchestrationViz } from "@/components/system-viz/OrchestrationViz";
import type { ViewMode, TeamInfo, WorkflowState, OrchestrationFlow, SystemData, PermissionData, OrchestrationData } from "@/lib/system-viz-types";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { KpiCard } from "@/components/ui/KpiCard";

const VIEW_MODES: { id: ViewMode; label: string; icon: React.ElementType }[] = [
  { id: "hierarchy", label: "Team Hierarchy", icon: Network },
  { id: "permissions", label: "Permissions", icon: Users },
  { id: "orchestration", label: "Orchestration", icon: GitBranch },
];

// Visual swatch for the tier pill in the team details sidebar.
const TIER_DOT: Record<string, string> = {
  orchestrator: "#f59e0b",
  executive: "#3b82f6",
  c_suite: "#8b5cf6",
  admin: "#10b981",
};

// Human-friendly label for a relative "X seconds ago" string.
function formatRelativeTime(date: Date | null): string {
  if (!date) return "Never";
  const diffMs = Date.now() - date.getTime();
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 5) return "Just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return date.toLocaleString();
}

// Lightweight skeleton row used while initial data is being fetched.
function SkeletonRow({ width = "w-full" }: { width?: string }) {
  return (
    <div
      role="presentation"
      className={clsx(
        "h-3 rounded bg-slate-800/70 animate-pulse",
        width
      )}
    />
  );
}

type OrgGraphSummary = {
  nodes?: Array<unknown>;
  edges?: Array<unknown>;
  capability_edges?: Array<unknown>;
  mermaid?: string;
};

export default function SystemVisualizationPage() {
  const {
    viewMode,
    setViewMode,
    selectedTeam,
    setSelectedTeam,
    selectedFlow,
    setSelectedFlow,
    systemData,
    setSystemData,
    permissionData,
    setPermissionData,
    orchestrationData,
    setOrchestrationData,
    loading,
    setLoading,
    error,
    setError,
    highlightedPath,
    setHighlightedPath,
  } = useSystemVizStore();

  const [traceMode, setTraceMode] = useState(false);
  const [traceStart, setTraceStart] = useState<string | null>(null);
  const [traceEnd, setTraceEnd] = useState<string | null>(null);
  const [orgGraph, setOrgGraph] = useState<OrgGraphSummary | null>(null);
  const [mermaidCopied, setMermaidCopied] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  // Local refetch key increments on every refresh; used to force child
  // visualization components to re-mount and pick up fresh data.
  const [refetchKey, setRefetchKey] = useState(0);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);

    // Fetch all data in parallel, handling each independently
    const [sysRes, permRes, orchRes, orgRes] = await Promise.all([
      fetch("/api/system/hierarchy"),
      fetch("/api/system/permissions"),
      fetch("/api/system/orchestration"),
      fetch("/api/system/org-graph"),
    ]);

    // Parse all responses - handle each independently to allow partial content
    let sysData: SystemData | null = null;
    let permData: PermissionData | null = null;
    let orchData: OrchestrationData | null = null;
    let orgGraphData: OrgGraphSummary | null = null;
    const errors: string[] = [];

    // Helper to safely parse JSON - handles both HTTP errors and JSON parsing errors
    const parseJson = async (res: Response, name: string): Promise<unknown> => {
      if (!res.ok) {
        errors.push(name);
        return null;
      }
      try {
        return await res.json();
      } catch {
        errors.push(name);
        return null;
      }
    };

    // Parse all in parallel
    const [sysRaw, permRaw, orchRaw, orgRaw] = await Promise.all([
      parseJson(sysRes, "hierarchy"),
      parseJson(permRes, "permissions"),
      parseJson(orchRes, "orchestration"),
      parseJson(orgRes, "org-graph"),
    ]);

    sysData = sysRaw as SystemData | null;
    permData = permRaw as PermissionData | null;
    orchData = orchRaw as OrchestrationData | null;
    orgGraphData = orgRaw as OrgGraphSummary | null;

    // Only set error if system hierarchy failed - that's critical. Other data is optional.
    if (!sysData && errors.length > 0) {
      setError(`Failed to load ${errors.join(", ")}`);
    }

    // Set data even if some APIs failed (allows partial content to show)
    if (sysData) setSystemData(sysData);
    if (permData) setPermissionData(permData);
    if (orchData) setOrchestrationData(orchData);
    if (orgGraphData) setOrgGraph(orgGraphData);

    setLastRefreshed(new Date());
    setLoading(false);
  }, [setSystemData, setPermissionData, setOrchestrationData, setLoading, setError]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Tick the relative-time label every 30s so "Last refreshed" stays accurate
  // without us having to refetch.
  const [, setTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 30_000);
    return () => clearInterval(iv);
  }, []);

  const handleRefresh = useCallback(() => {
    setRefetchKey(k => k + 1);
    fetchData();
  }, [fetchData]);

  const teams: TeamInfo[] = useMemo(() => systemData?.teams || [], [systemData]);
  const hierarchy = useMemo(() => systemData?.hierarchy || [], [systemData]);
  const states: WorkflowState[] = useMemo(() => orchestrationData?.states || [], [orchestrationData]);
  const flows: OrchestrationFlow[] = useMemo(() => orchestrationData?.flows || [], [orchestrationData]);

  const findPath = useCallback((start: string, end: string): string[] => {
    const adjacency: Record<string, string[]> = {};
    
    flows.forEach(flow => {
      flow.edges.forEach(edge => {
        if (!adjacency[edge.source]) adjacency[edge.source] = [];
        adjacency[edge.source].push(edge.target);
      });
    });

    const visited = new Set<string>();
    const path: string[] = [];

    function dfs(node: string): boolean {
      if (node === end) {
        path.push(node);
        return true;
      }
      visited.add(node);
      path.push(node);

      const neighbors = adjacency[node] || [];
      for (const neighbor of neighbors) {
        if (!visited.has(neighbor) && dfs(neighbor)) {
          return true;
        }
      }

      path.pop();
      return false;
    }

    dfs(start);
    return path;
  }, [flows]);

  const handleTracePath = useCallback((from: string, to: string) => {
    const path = findPath(from, to);
    setHighlightedPath(path);
  }, [findPath, setHighlightedPath]);

  const clearTrace = useCallback(() => {
    setTraceMode(false);
    setTraceStart(null);
    setTraceEnd(null);
    setHighlightedPath(null);
  }, [setHighlightedPath]);

  const copyMermaid = useCallback(async () => {
    if (!orgGraph?.mermaid) return;
    await navigator.clipboard.writeText(orgGraph.mermaid);
    setMermaidCopied(true);
    window.setTimeout(() => setMermaidCopied(false), 1500);
  }, [orgGraph]);

  if (loading) {
    return (
      <div
        className="dashboard-page"
        role="status"
        aria-busy="true"
        aria-label="Loading system visualization"
      >
        {/* Skeleton header */}
        <div className="flex items-start justify-between gap-4 rounded-2xl border border-slate-800/80 bg-slate-950/35 px-4 py-4 shadow-sm shadow-black/10">
          <div className="flex items-start gap-3 min-w-0">
            <div className="w-11 h-11 rounded-xl bg-slate-800/70 animate-pulse" />
            <div className="space-y-2 min-w-0">
              <SkeletonRow width="w-28" />
              <SkeletonRow width="w-48" />
            </div>
          </div>
          <div className="flex gap-2">
            <SkeletonRow width="w-24" />
            <SkeletonRow width="w-10" />
          </div>
        </div>

        {/* Skeleton KPI strip */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map(i => (
            <div
              key={i}
              className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 flex items-start gap-3 animate-pulse"
            >
              <div className="w-10 h-10 rounded-lg bg-slate-800/80" />
              <div className="flex-1 space-y-2">
                <SkeletonRow width="w-20" />
                <SkeletonRow width="w-14" />
                <SkeletonRow width="w-28" />
              </div>
            </div>
          ))}
        </div>

        {/* Skeleton viz surface */}
        <div className="dashboard-surface p-6 min-h-[420px]">
          <div className="flex items-center justify-between mb-4">
            <SkeletonRow width="w-40" />
            <SkeletonRow width="w-24" />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {[0, 1, 2].map(i => (
              <div
                key={i}
                className="h-44 rounded-lg border border-slate-800/80 bg-slate-950/40 animate-pulse"
              />
            ))}
          </div>
        </div>

        <span className="sr-only">Loading system visualization...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="dashboard-page">
        <PageHeader
          icon="alert"
          title="System Visualization"
          description="Unable to load the control-plane graph"
        />
        <ErrorBanner
          tone="error"
          title="Failed to load system hierarchy"
          action={
            <button
              onClick={handleRefresh}
              className="px-3 py-1.5 text-xs font-medium rounded-md bg-slate-800 hover:bg-slate-700 text-slate-100 transition-colors"
            >
              Retry
            </button>
          }
        >
          {error}
        </ErrorBanner>
        <EmptyState
          icon="alert"
          title="Visualization unavailable"
          description="We could not reach the system hierarchy endpoint. Retry, or check the System status page if this keeps happening."
          action={
            <button
              onClick={handleRefresh}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors"
            >
              <RefreshCw size={14} />
              Retry
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="dashboard-page">
      {/* Breadcrumbs — keep simple, semantic, keyboard-friendly */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-slate-500">
        <a
          href="/"
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:text-slate-200 hover:bg-slate-800/60 focus-visible:ring-2 focus-visible:ring-blue-400/70 transition-colors"
        >
          <Home size={12} aria-hidden="true" />
          Dashboard
        </a>
        <ChevronRight size={12} aria-hidden="true" className="text-slate-700" />
        <a
          href="/system"
          className="rounded px-1.5 py-0.5 hover:text-slate-200 hover:bg-slate-800/60 focus-visible:ring-2 focus-visible:ring-blue-400/70 transition-colors"
        >
          System
        </a>
        <ChevronRight size={12} aria-hidden="true" className="text-slate-700" />
        <span className="rounded px-1.5 py-0.5 text-slate-300 bg-slate-800/60" aria-current="page">
          Visualization
        </span>
      </nav>

      <PageHeader
        icon="network"
        title="System Visualization"
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>Explore the agent team hierarchy, permissions, and orchestration flows.</span>
            {lastRefreshed && (
              <span
                className="inline-flex items-center gap-1 text-slate-500"
                title={lastRefreshed.toLocaleString()}
              >
                <Clock size={12} aria-hidden="true" />
                Last refreshed {formatRelativeTime(lastRefreshed)}
              </span>
            )}
          </span>
        }
        actions={
          <>
            {viewMode === "orchestration" && (
              <button
                onClick={() => setTraceMode(!traceMode)}
                aria-pressed={traceMode}
                aria-label="Toggle path trace mode"
                className={clsx(
                  "inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
                  "focus-visible:ring-2 focus-visible:ring-blue-400/70",
                  traceMode
                    ? "bg-amber-600 hover:bg-amber-500 text-white"
                    : "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700/70"
                )}
              >
                <Search size={14} aria-hidden="true" />
                Trace Path
              </button>
            )}
            <button
              onClick={handleRefresh}
              aria-label="Refresh visualization data"
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700/70 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70"
            >
              <RefreshCw size={14} aria-hidden="true" />
              Refresh
            </button>
          </>
        }
      />

      {/* View-mode switcher as a toolbar */}
      <div
        role="tablist"
        aria-label="Visualization views"
        className="dashboard-toolbar inline-flex items-center gap-1 p-1"
      >
        {VIEW_MODES.map(mode => {
          const Icon = mode.icon;
          const active = viewMode === mode.id;
          return (
            <button
              key={mode.id}
              role="tab"
              aria-selected={active}
              aria-controls={`viz-panel-${mode.id}`}
              onClick={() => {
                setViewMode(mode.id);
                setSelectedTeam(null);
                setSelectedFlow(null);
                clearTrace();
              }}
              className={clsx(
                "inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                "focus-visible:ring-2 focus-visible:ring-blue-400/70",
                active
                  ? "bg-blue-600 text-white shadow-sm shadow-blue-950/40"
                  : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/70"
              )}
            >
              <Icon size={14} aria-hidden="true" />
              {mode.label}
            </button>
          );
        })}
      </div>

      {/* Top-line KPI strip — quick situational awareness */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          icon="users"
          tone="info"
          label="Teams"
          value={teams.length}
          hint={viewMode === "hierarchy" ? "Click a node for details" : "Across all teams"}
        />
        <KpiCard
          icon="git-branch"
          tone="warning"
          label="Orchestration flows"
          value={flows.length}
          hint={states.length > 0 ? `${states.length} workflow states` : "No states loaded"}
        />
        <KpiCard
          icon="shield"
          tone="positive"
          label="Policy matrix"
          value={permissionData ? Object.keys(permissionData.communicationMatrix ?? {}).length : 0}
          hint="Sender roles tracked"
        />
        <KpiCard
          icon="network"
          tone="neutral"
          label="Org graph"
          value={orgGraph ? (orgGraph.nodes ?? []).length : 0}
          hint={orgGraph ? `${(orgGraph.edges ?? []).length} edges` : "Not loaded"}
        />
      </div>

      {orgGraph?.mermaid && (
        <section
          aria-label="Mermaid export"
          className="dashboard-surface p-4"
        >
          <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_auto] gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-3 mb-2">
                <div className="text-sm font-medium text-slate-100">Mermaid Export</div>
                <div className="text-xs text-slate-500">
                  {(orgGraph.nodes ?? []).length} nodes / {(orgGraph.edges ?? []).length} edges /{" "}
                  {(orgGraph.capability_edges ?? []).length} capability links
                </div>
              </div>
              <pre
                aria-label="Mermaid export source"
                className="max-h-32 overflow-auto rounded-lg border border-slate-800 bg-slate-950/80 px-3 py-2 text-xs text-slate-300 font-mono"
              >
                {orgGraph.mermaid}
              </pre>
            </div>
            <button
              onClick={copyMermaid}
              aria-label="Copy mermaid definition to clipboard"
              className={clsx(
                "self-start inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border transition-colors",
                "focus-visible:ring-2 focus-visible:ring-blue-400/70",
                mermaidCopied
                  ? "bg-emerald-600/20 border-emerald-500/40 text-emerald-300"
                  : "bg-slate-800 border-slate-700 text-slate-200 hover:border-slate-500 hover:bg-slate-700"
              )}
            >
              <Copy size={14} aria-hidden="true" />
              {mermaidCopied ? "Copied" : "Copy Mermaid"}
            </button>
          </div>
        </section>
      )}

      {traceMode && viewMode === "orchestration" && (
        <div
          className="flex flex-wrap items-center gap-3 px-4 py-3 bg-amber-950/25 border border-amber-800/60 rounded-xl"
          role="region"
          aria-label="Path trace controls"
        >
          <span className="text-sm font-medium text-amber-300">Path Trace:</span>
          <label className="sr-only" htmlFor="trace-start">Start node</label>
          <select
            id="trace-start"
            value={traceStart || ""}
            onChange={(e) => setTraceStart(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-sm text-slate-100 focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <option value="">Select start node...</option>
            {flows.find(f => f.id === selectedFlow)?.nodes.map(n => (
              <option key={n.id} value={n.id}>{n.label}</option>
            ))}
          </select>
          <ArrowRight size={16} className="text-slate-500" aria-hidden="true" />
          <label className="sr-only" htmlFor="trace-end">End node</label>
          <select
            id="trace-end"
            value={traceEnd || ""}
            onChange={(e) => setTraceEnd(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-sm text-slate-100 focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <option value="">Select end node...</option>
            {flows.find(f => f.id === selectedFlow)?.nodes.map(n => (
              <option key={n.id} value={n.id}>{n.label}</option>
            ))}
          </select>
          <button
            onClick={() => traceStart && traceEnd && handleTracePath(traceStart, traceEnd)}
            disabled={!traceStart || !traceEnd}
            aria-label="Find path between selected nodes"
            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 disabled:text-slate-500 text-white text-sm font-medium rounded-md transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            Find Path
          </button>
          {highlightedPath && (
            <button
              onClick={clearTrace}
              className="px-3 py-1 bg-slate-700 hover:bg-slate-600 text-white text-sm font-medium rounded-md transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70"
            >
              Clear
            </button>
          )}
          {highlightedPath && (
            <span className="text-xs text-amber-200/80">
              {highlightedPath.length} step{highlightedPath.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}

      {/* Viz + sidebar — fixed-height flex row so panels stay scrollable
          independently without forcing the page itself to overflow. */}
      <div className="flex flex-col lg:flex-row gap-4 min-h-[520px]">
        <div
          id={`viz-panel-${viewMode}`}
          role="tabpanel"
          aria-label={`${VIEW_MODES.find(m => m.id === viewMode)?.label ?? "Visualization"} view`}
          className="flex-1 min-w-0 dashboard-surface overflow-hidden"
        >
          {viewMode === "hierarchy" && (
            <HierarchyViz
              key={`hierarchy-${refetchKey}`}
              hierarchy={hierarchy}
              onNodeClick={(teamId) => setSelectedTeam(teamId === selectedTeam ? null : teamId)}
              selectedTeam={selectedTeam}
              highlightedPath={highlightedPath}
            />
          )}
          {viewMode === "permissions" && permissionData && (
            <PermissionsViz
              key={`permissions-${refetchKey}`}
              permissions={permissionData}
              teams={teams}
              selectedTeam={selectedTeam}
              onTeamSelect={(teamId) => setSelectedTeam(teamId === selectedTeam ? null : teamId)}
              onTracePath={(from, to) => handleTracePath(from, to)}
            />
          )}
          {viewMode === "permissions" && !permissionData && (
            <EmptyState
              icon="alert"
              tone="muted"
              title="Permissions data unavailable"
              description="The permissions endpoint did not return a response. Refresh to retry, or check the System status page."
              action={
                <button
                  onClick={handleRefresh}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-100 text-sm font-medium border border-slate-700 transition-colors"
                >
                  <RefreshCw size={14} aria-hidden="true" />
                  Retry
                </button>
              }
            />
          )}
          {viewMode === "orchestration" && (
            <OrchestrationViz
              key={`orchestration-${refetchKey}`}
              flows={flows}
              states={states}
              selectedFlowId={selectedFlow}
              onFlowSelect={(flowId) => {
                setSelectedFlow(flowId);
                clearTrace();
              }}
              highlightedPath={highlightedPath}
              onTracePath={(nodeId) => {
                if (traceMode && traceStart && traceEnd) {
                  handleTracePath(traceStart, nodeId);
                }
              }}
            />
          )}
          {viewMode === "orchestration" && flows.length === 0 && !loading && (
            <EmptyState
              icon="git-branch"
              tone="muted"
              title="No orchestration flows defined"
              description="No flows were returned by the orchestration endpoint. Once a flow is defined it will appear here."
            />
          )}
        </div>

        {(selectedTeam || selectedFlow) && (
          <aside
            aria-label="Selection details"
            className="w-full lg:w-80 flex-shrink-0 dashboard-surface overflow-auto"
          >
            <div className="sticky top-0 z-10 flex items-center justify-between gap-2 px-4 py-3 border-b border-slate-800/80 bg-slate-900/95 backdrop-blur">
              <h3 className="text-sm font-semibold text-slate-100">
                {selectedFlow && viewMode === "orchestration" ? "Flow Details" : "Team Details"}
              </h3>
              <button
                onClick={() => {
                  setSelectedTeam(null);
                  setSelectedFlow(null);
                }}
                aria-label="Close details panel"
                className="p-1 rounded-md text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>

            {selectedTeam && viewMode === "hierarchy" && teams.find(t => t.teamId === selectedTeam) && (
              <div className="p-4 space-y-4">
                {(() => {
                  const team = teams.find(t => t.teamId === selectedTeam)!;
                  return (
                    <>
                      <DetailRow label="Team">
                        <div className="text-slate-100 font-medium">{team.displayName}</div>
                        <div className="text-xs text-slate-500 font-mono break-all">{team.teamId}</div>
                      </DetailRow>
                      <DetailRow label="Tier">
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-slate-800 border border-slate-700 text-xs text-slate-200 capitalize">
                          <span
                            className="w-1.5 h-1.5 rounded-full"
                            style={{ backgroundColor: TIER_DOT[team.tier] ?? "#94a3b8" }}
                            aria-hidden="true"
                          />
                          {team.tier.replace("_", " ")}
                        </span>
                      </DetailRow>
                      <DetailRow label="Admin Agent">
                        <div className="text-slate-100">{team.admin.displayName}</div>
                        <div className="text-xs text-slate-500 font-mono break-all">{team.admin.agentId}</div>
                      </DetailRow>
                      {team.workers.length > 0 && (
                        <DetailSection
                          label={`Workers (${team.workers.length})`}
                          empty="No workers assigned"
                        >
                          <ul className="space-y-2">
                            {team.workers.map(w => (
                              <li
                                key={w.agentId}
                                className="p-2 rounded-md bg-slate-800/70 border border-slate-700/70 hover:border-slate-600 transition-colors"
                              >
                                <div className="text-slate-100 text-sm font-medium">{w.displayName}</div>
                                <div className="text-xs text-slate-500 font-mono break-all">{w.agentId}</div>
                              </li>
                            ))}
                          </ul>
                        </DetailSection>
                      )}
                      <DetailSection
                        label={`Allowed Tools (${team.admin.tools.length})`}
                        empty="No tools registered"
                      >
                        <div className="flex flex-wrap gap-1">
                          {team.admin.tools.map(tool => (
                            <span
                              key={tool}
                              className="px-2 py-0.5 bg-blue-500/10 border border-blue-500/25 text-blue-300 text-xs rounded-md font-mono"
                            >
                              {tool}
                            </span>
                          ))}
                        </div>
                      </DetailSection>
                    </>
                  );
                })()}
              </div>
            )}

            {selectedTeam && viewMode === "permissions" && permissionData && teams.find(t => t.teamId === selectedTeam) && (
              <div className="p-4 space-y-4">
                <div className="text-sm font-semibold text-slate-100">
                  Permissions for{" "}
                  <span className="text-blue-300">
                    {teams.find(t => t.teamId === selectedTeam)?.displayName}
                  </span>
                </div>
                <DetailRow label="Team Tier">
                  <div className="text-slate-100">
                    {permissionData.teamTiers[selectedTeam] ?? "—"}
                  </div>
                </DetailRow>
                <DetailSection label="Allowed Senders" empty="No inbound permissions">
                  <ul className="space-y-1">
                    {Object.entries(permissionData.communicationMatrix).map(([role, targets]) => {
                      const allowed = targets[selectedTeam]?.allowed;
                      if (!allowed) return null;
                      const msgTypes = targets[selectedTeam]?.msgTypes ?? [];
                      return (
                        <li
                          key={role}
                          className="flex items-start gap-2 text-sm text-slate-200"
                        >
                          <ChevronRight size={12} className="text-emerald-400 mt-0.5" aria-hidden="true" />
                          <div className="min-w-0">
                            <div className="capitalize">{role}</div>
                            {msgTypes.length > 0 && (
                              <div className="text-xs text-slate-500 truncate">
                                {msgTypes.join(", ")}
                              </div>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </DetailSection>
              </div>
            )}

            {selectedFlow && viewMode === "orchestration" && flows.find(f => f.id === selectedFlow) && (
              <div className="p-4 space-y-4">
                {(() => {
                  const flow = flows.find(f => f.id === selectedFlow)!;
                  return (
                    <>
                      <DetailRow label="Name">
                        <div className="text-slate-100 font-medium">{flow.name}</div>
                      </DetailRow>
                      {flow.description && (
                        <DetailRow label="Description">
                          <div className="text-slate-300 text-sm leading-relaxed">
                            {flow.description}
                          </div>
                        </DetailRow>
                      )}
                      <DetailSection
                        label={`Nodes (${flow.nodes.length})`}
                        empty="No nodes"
                      >
                        <ul className="max-h-48 overflow-auto space-y-1 pr-1">
                          {flow.nodes.map(n => (
                            <li
                              key={n.id}
                              className="text-xs text-slate-300 flex items-center gap-2 px-2 py-1 rounded hover:bg-slate-800/60 transition-colors"
                            >
                              <span className="font-mono text-slate-400">{n.id}</span>
                              <span className="truncate">{n.label}</span>
                            </li>
                          ))}
                        </ul>
                      </DetailSection>
                      <DetailSection
                        label={`Edges (${flow.edges.length})`}
                        empty="No edges"
                      >
                        <ul className="max-h-48 overflow-auto space-y-1 pr-1">
                          {flow.edges.map((e, i) => (
                            <li
                              key={i}
                              className="text-xs text-slate-300 flex items-center gap-1 px-2 py-1 rounded hover:bg-slate-800/60 transition-colors"
                            >
                              <span className="font-mono text-slate-400">{e.source}</span>
                              <ChevronRight size={10} className="text-slate-600" aria-hidden="true" />
                              <span className="font-mono text-slate-400">{e.target}</span>
                              {e.condition && (
                                <span className="text-amber-300 font-mono">({e.condition})</span>
                              )}
                            </li>
                          ))}
                        </ul>
                      </DetailSection>
                    </>
                  );
                })()}
              </div>
            )}
          </aside>
        )}
      </div>

      {/* Back link — small accessibility / navigation aid */}
      <div className="pt-1">
        <a
          href="/system"
          className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-200 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded px-1 py-0.5"
        >
          <ChevronLeft size={12} aria-hidden="true" />
          Back to System Control
        </a>
      </div>
    </div>
  );
}

// Inline presentational helpers used by the selection side panel. Kept inside
// the file to avoid leaking micro-components into the global ui library.
function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1">
        {label}
      </div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

function DetailSection({
  label,
  empty,
  children,
}: {
  label: string;
  empty: string;
  children: React.ReactNode;
}) {
  // We only check for the section's own "empty" child in render — children
  // are the developer's responsibility; this keeps the wrapper dumb.
  void empty;
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1.5">
        {label}
      </div>
      {children}
    </div>
  );
}
