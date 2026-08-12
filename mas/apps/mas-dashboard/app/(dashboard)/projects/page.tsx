"use client";

import { useState, useEffect, FormEvent, useMemo, useRef } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import { WORKFLOW_STATES, STATE_COLORS, type WorkflowState } from "@/lib/constants";
import { formatDistanceToNow } from "date-fns";
import {
  Archive,
  ArrowRight,
  ArrowUpDown,
  ChevronDown,
  ChevronRight,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { BulkActionBar, RowCheckbox, SelectAllCheckbox } from "@/components/ui/BulkActionBar";
import { useBulkSelection } from "@/lib/use-bulk-selection";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { FilterChip } from "@/components/ui/FilterChips";

type HttpError = Error & { status?: number };

interface Project {
  id: string;
  name: string;
  description?: string;
  state: WorkflowState;
  created_at: string;
  updated_at: string;
}

interface Flow {
  id: string;
  name: string;
  version: number;
}

// Available sort columns for the projects table.
type SortKey = "name" | "state" | "created_at" | "updated_at";
type SortDir = "asc" | "desc";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "updated_at", label: "Updated" },
  { key: "created_at", label: "Created" },
  { key: "state", label: "State" },
];

function projectActionError(detail: unknown, fallback: string) {
  const raw = typeof (detail as { error?: unknown })?.error === "string"
    ? (detail as { error: string }).error
    : fallback;

  try {
    const parsed = JSON.parse(raw);
    return typeof parsed?.detail === "string" ? parsed.detail : raw;
  } catch {
    return raw;
  }
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("non-archived");
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newInitialContext, setNewInitialContext] = useState("");
  const [newRepositoryUrl, setNewRepositoryUrl] = useState("");
  const [newGitMode, setNewGitMode] = useState<"clone" | "init">("clone");
  const [newGitBranch, setNewGitBranch] = useState("");
  const [newTags, setNewTags] = useState("");
  const [selectedFlowId, setSelectedFlowId] = useState<string>("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loadStale, setLoadStale] = useState(false);
  const [bulkArchiving, setBulkArchiving] = useState(false);
  const [bulkDeleteLoading, setBulkDeleteLoading] = useState(false);
  const [bulkError, setBulkError] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const [hasReadContext, setHasReadContext] = useState(false);
  const projectsRef = useRef<Project[] | null>(null);
  const flowsRef = useRef<Flow[] | null>(null);
  const hasReadContextRef = useRef(false);
  // Sort controls: which field to sort by and which direction.
  // Defaults to "Recently updated" — the most common intent on a project list.
  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  // Tracks which project row has its full description expanded inline.
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      // Flip direction when re-clicking the active sort column.
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Sensible default per column: text columns ascend, dates descend.
      setSortDir(key === "name" || key === "state" ? "asc" : "desc");
    }
  };

  function handleAccessDenied() {
    const hadData = hasReadContextRef.current || projectsRef.current !== null || flowsRef.current !== null;
    setAccessDenied(true);
    setHasReadContext(hadData);
    setLoadError("");
    setLoadStale(false);
    setError("");
    setBulkError("");
    setShowCreate(false);
    setFilter("non-archived");
    setExpandedId(null);
  }

  async function load() {
    if (accessDenied) return;
    const hadData = projectsRef.current !== null || flowsRef.current !== null;
    if (!hadData) setLoading(true);
    setLoadError("");
    try {
      const [projRes, flowRes] = await Promise.all([
        fetch("/api/projects?limit=1000", { cache: "no-store" }),
        fetch("/api/flows?is_active=true&limit=1000", { cache: "no-store" }),
      ]);
      if (!projRes.ok || !flowRes.ok) {
        const denied = [projRes, flowRes].find((response) => response.status === 401 || response.status === 403);
        const error = new Error("Project and flow data are unavailable from the control plane") as HttpError;
        error.status = denied?.status;
        throw error;
      }
      const [projData, flowData] = await Promise.all([projRes.json(), flowRes.json()]);
      const nextProjects = Array.isArray(projData) ? projData : projData.projects ?? [];
      const nextFlows = Array.isArray(flowData) ? flowData : [];
      projectsRef.current = nextProjects;
      flowsRef.current = nextFlows;
      hasReadContextRef.current = true;
      setProjects(nextProjects);
      setFlows(nextFlows);
      setHasReadContext(true);
      setLoadStale(false);
    } catch (cause) {
      const status = cause instanceof Error ? (cause as HttpError).status : undefined;
      if (status === 401 || status === 403) {
        handleAccessDenied();
        return;
      }
      setLoadError(cause instanceof Error ? cause.message : String(cause));
      setLoadStale(hadData);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function resetCreateForm() {
    setNewName("");
    setNewDesc("");
    setNewInitialContext("");
    setNewRepositoryUrl("");
    setNewGitMode("clone");
    setNewGitBranch("");
    setNewTags("");
    setSelectedFlowId("");
    setError("");
  }

  async function handleCreate(e: FormEvent) {
    if (accessDenied) return;
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      const tags = newTags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean);
      const config: Record<string, unknown> = {};
      if (newRepositoryUrl.trim()) config.repository_url = newRepositoryUrl.trim();
      if (tags.length > 0) config.tags = tags;
      const repositoryUrl = newRepositoryUrl.trim();
      const workspaceMode = repositoryUrl ? newGitMode : "init";
      const initialContext: Record<string, unknown>[] = [];
      if (newDesc.trim()) {
        initialContext.push({
          item_type: "TEXT",
          name: "Project goal",
          content_text: newDesc.trim(),
          tags: ["project-goal", ...tags],
        });
      }
      if (newInitialContext.trim()) {
        initialContext.push({
          item_type: "TEXT",
          name: "Initial project context",
          content_text: newInitialContext.trim(),
          tags: ["project-brief", ...tags],
        });
      }
      if (repositoryUrl) {
        initialContext.push({
          item_type: "URL",
          name: "Source repository",
          url: repositoryUrl,
          tags: ["repository", ...tags],
        });
      }

      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newName.trim(),
          description: newDesc.trim() || null,
          config: Object.keys(config).length > 0 ? config : undefined,
          workspace: {
            mode: workspaceMode,
            repository_url: repositoryUrl || undefined,
            branch: newGitBranch.trim() || undefined,
            remote_name: "origin",
          },
          flow_id: selectedFlowId || undefined,
          initial_context: initialContext,
        }),
      });
      if (res.ok) {
        await res.json();
        setShowCreate(false);
        resetCreateForm();
        await load();
      } else {
        if (res.status === 401 || res.status === 403) {
          handleAccessDenied();
          return;
        }
        const d = await res.json().catch(() => null);
        setError(projectActionError(d, "Failed to create project"));
      }
    } finally {
      setCreating(false);
    }
  }

  const filtered = filter === "non-archived"
    ? projects.filter((p) => p.state !== "ARCHIVED")
    : filter
      ? projects.filter((p) => p.state === filter)
      : projects;

  // Apply sort. We always return a new array (don't mutate `filtered` in place)
  // so memoization on `filteredIds` stays correct.
  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (sortKey === "name" || sortKey === "state") {
        return dir * String(av).localeCompare(String(bv));
      }
      // Date columns: compare as timestamps.
      return dir * (new Date(av as string).getTime() - new Date(bv as string).getTime());
    });
  }, [filtered, sortKey, sortDir]);

  const activeCount = useMemo(
    () => projects.filter((p) => !["COMPLETED", "ARCHIVED", "FAILED"].includes(p.state)).length,
    [projects]
  );
  const nonArchivedCount = useMemo(
    () => projects.filter((p) => p.state !== "ARCHIVED").length,
    [projects]
  );

  const filteredIds = useMemo(() => sorted.map((p) => p.id), [sorted]);
  const selection = useBulkSelection(filteredIds);
  // Drop selections whose projects are no longer in the filtered view.
  useEffect(() => {
    selection.prune();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredIds.join(",")]);
  useEffect(() => {
    if (accessDenied) selection.clear();
    // Selection is intentionally cleared only when the authority boundary is entered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessDenied]);

  async function handleBulkArchive() {
    if (accessDenied || selection.selectedCount === 0) return;
    const ids = Array.from(selection.selected);
    setBulkArchiving(true);
    setBulkError("");
    let failed = 0;
    try {
      // Archive in parallel — the archive endpoint is independent per project.
      const results = await Promise.allSettled(
        ids.map((id) => fetch(`/api/projects/${id}/archive`, { method: "POST" }))
      );
      const denied = results.some(
        (result) =>
          result.status === "fulfilled" &&
          (result.value.status === 401 || result.value.status === 403),
      );
      if (denied) {
        handleAccessDenied();
        return;
      }
      for (const r of results) {
        if (r.status === "rejected" || !r.value.ok) failed++;
      }
      if (failed > 0) {
        setBulkError(
          `Archived ${ids.length - failed} of ${ids.length} project${ids.length === 1 ? "" : "s"} (${failed} failed).`
        );
      }
      await load();
      selection.clear();
    } finally {
      setBulkArchiving(false);
    }
  }

  async function archiveProject(project: Project) {
    if (accessDenied) return;
    setBulkArchiving(true);
    setBulkError("");
    try {
      const res = await fetch(`/api/projects/${project.id}/archive`, { method: "POST" });
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          handleAccessDenied();
          return;
        }
        const detail = await res.json().catch(() => null);
        const message = projectActionError(detail, res.statusText);
        setBulkError(`Failed to archive "${project.name}": ${message}`);
        return;
      }
      await load();
    } finally {
      setBulkArchiving(false);
    }
  }

  async function deleteProject(project: Project) {
    if (accessDenied) return;
    setBulkDeleteLoading(true);
    setBulkError("");
    try {
      const res = await fetch(`/api/projects/${project.id}`, { method: "DELETE" });
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          handleAccessDenied();
          return;
        }
        const detail = await res.json().catch(() => null);
        const message = projectActionError(detail, res.statusText);
        setBulkError(`Failed to delete "${project.name}": ${message}`);
        return;
      }
      await load();
      selection.prune();
    } finally {
      setBulkDeleteLoading(false);
    }
  }

  return (
    <div className="dashboard-page">
      {/* Header */}
      <PageHeader
        icon="folder-kanban"
        title="Projects"
        description={`${projects.length} total · ${activeCount} active`}
        actions={!accessDenied ? (
          <>
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              aria-label="Refresh projects"
              title="Refresh"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-slate-700 text-slate-400 hover:text-slate-100 hover:border-slate-500 hover:bg-slate-800 transition-colors"
            >
              <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
            </button>
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="inline-flex min-h-11 items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg shadow-sm shadow-blue-500/10 transition-colors"
            >
              <Plus size={14} />
              New Project
            </button>
          </>
        ) : undefined}
      />

      {accessDenied && (
        <section
          className="mx-4 mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"
          role="region"
          aria-label="Projects access status"
        >
          <h2 className="font-medium text-amber-50">Projects access denied</h2>
          <p className="mt-1 text-amber-100/80">
            {hasReadContext
              ? "Previously loaded project and active-flow definitions remain visible for reference. Refresh, retry, New Project, filters, sorting, selection, and archive/delete controls are hidden until authorization is restored."
              : "No live project definitions are available while authorization is unavailable. Project controls are hidden until authorization is restored."}
          </p>
        </section>
      )}

      {loadError && !accessDenied && (
        <ErrorBanner
          tone={loadStale ? "warning" : "error"}
          title={loadStale ? "Showing last known project list" : "Project list unavailable"}
          action={(
            <button type="button" onClick={() => void load()} disabled={loading} className="rounded border border-current px-2.5 py-1 text-xs font-medium hover:bg-white/10 disabled:opacity-50">
              Retry
            </button>
          )}
        >
          {loadStale ? `${loadError}. The latest project refresh failed; retained projects remain visible.` : loadError}
        </ErrorBanner>
      )}

      {/* Filter + sort toolbar */}
      {projects.length > 0 && !accessDenied && (
        <div className="dashboard-toolbar flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter projects by state">
            <FilterChip
              active={filter === "non-archived"}
              onClick={() => setFilter("non-archived")}
              count={nonArchivedCount}
              className="min-h-11"
            >
              Non archived
            </FilterChip>
            <FilterChip
              active={filter === ""}
              onClick={() => setFilter("")}
              count={projects.length}
              className="min-h-11"
            >
              All
            </FilterChip>
            {WORKFLOW_STATES.filter((s) => projects.some((p) => p.state === s)).map((s) => (
              <FilterChip
                key={s}
                active={filter === s}
                onClick={() => setFilter(s)}
                count={projects.filter((p) => p.state === s).length}
                className="min-h-11"
              >
                {s.replace(/_/g, " ")}
              </FilterChip>
            ))}
          </div>

          {/* Sort controls — click a header-style chip to sort by that field. */}
          <div
            className="ml-auto flex items-center gap-1.5"
            role="group"
            aria-label="Sort projects"
          >
            <span className="hidden sm:inline-flex items-center gap-1 text-xxs font-semibold uppercase tracking-wider text-slate-500 mr-1">
              <ArrowUpDown size={11} aria-hidden="true" />
              Sort
            </span>
            {SORT_OPTIONS.map((opt) => {
              const active = sortKey === opt.key;
              return (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => toggleSort(opt.key)}
                  aria-label={`Sort by ${opt.label} ${active ? (sortDir === "asc" ? "(ascending)" : "(descending)") : ""}`}
                  aria-pressed={active}
                  className={clsx(
                    "inline-flex min-h-11 items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold border transition-colors",
                    active
                      ? "bg-blue-500/20 text-blue-100 border-blue-400/45"
                      : "bg-slate-950/55 text-slate-400 border-slate-700 hover:bg-slate-900 hover:text-slate-200"
                  )}
                >
                  {opt.label}
                  {active && (
                    <span aria-hidden="true" className="text-blue-300">
                      {sortDir === "asc" ? "↑" : "↓"}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Bulk action bar */}
      {!accessDenied && selection.selectedCount > 0 && (
        <BulkActionBar
          selectedCount={selection.selectedCount}
          totalCount={sorted.length}
          loading={bulkArchiving || bulkDeleteLoading}
          action="archive"
          actionLabel={`Archive ${selection.selectedCount} selected`}
          onAction={handleBulkArchive}
          onClear={selection.clear}
        />
      )}

      {bulkError && !accessDenied && (
        <ErrorBanner tone="warning">{bulkError}</ErrorBanner>
      )}

      {/* Table */}
      <div className="dashboard-surface overflow-hidden">
        {loading && !accessDenied ? (
          <div className="p-8 text-center text-slate-500 text-sm">Loading…</div>
        ) : accessDenied && projects.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="key"
              title="No live project definitions are available"
              description="Project definitions cannot be read while authorization is unavailable. Controls remain hidden until access is restored."
            />
          </div>
        ) : filtered.length === 0 ? (
          projects.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon="folder-kanban"
                title="No projects yet"
                description="Create your first project to get the agent teams working."
                action={
                  <button
                    type="button"
                    onClick={() => setShowCreate(true)}
                    className="inline-flex min-h-11 items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    <Plus size={14} />
                    New Project
                  </button>
                }
              />
            </div>
          ) : (
            <div className="p-6">
              <EmptyState
                icon="folder-kanban"
                title="No projects in this state"
                description="Try a different filter or clear it to see everything."
                action={
                  <button
                    type="button"
                    onClick={() => setFilter("non-archived")}
                    className="inline-flex min-h-11 items-center rounded px-2 text-xs text-blue-400 hover:text-blue-300"
                  >
                    Show non archived
                  </button>
                }
              />
            </div>
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label="Projects list">
              <caption className="sr-only">
                Projects list. Use the filters and sort controls to change the
                displayed projects.
              </caption>
              <thead>
                <tr className="border-b border-slate-800">
                  <th scope="col" className="px-4 py-3 w-16">
                    {!accessDenied && (
                      <SelectAllCheckbox
                        checked={selection.isAllSelected}
                        indeterminate={selection.isIndeterminate}
                        onChange={selection.toggleAll}
                        ariaLabel="Select all projects"
                        className="min-h-11 min-w-11"
                      />
                    )}
                  </th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Name</th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">State</th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider hidden sm:table-cell">Created</th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider hidden md:table-cell">Updated</th>
                  <th scope="col" className="px-4 py-3">
                    <span className="sr-only">Quick actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {sorted.map((p) => {
                  const isSelected = selection.selected.has(p.id);
                  const isExpanded = expandedId === p.id;
                  const hasDescription = Boolean(p.description);
                  return (
                    <ProjectRow
                      key={p.id}
                      project={p}
                      isSelected={isSelected}
                      isExpanded={isExpanded}
                      hasDescription={hasDescription}
                      onToggleSelect={() => selection.toggle(p.id)}
                      onToggleExpand={() =>
                        setExpandedId((curr) => (curr === p.id ? null : p.id))
                      }
                      onArchive={() => archiveProject(p)}
                      onDelete={() => deleteProject(p)}
                      actionsDisabled={bulkArchiving || bulkDeleteLoading}
                      readOnly={accessDenied}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create modal */}
      {showCreate && !accessDenied && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="dashboard-surface-strong p-6 w-full max-w-xl shadow-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-start justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-white">New Project</h2>
                <p className="text-xs text-slate-500 mt-0.5">
                  Give the teams a useful starting brief, context, and workflow.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setShowCreate(false);
                  resetCreateForm();
                }}
                className="p-1 text-slate-500 hover:text-slate-200 rounded"
              >
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-sm text-slate-300 mb-1.5">Name</label>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  required
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="my-project"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-300 mb-1.5">Goal / scope</label>
                <textarea
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  rows={3}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
                  placeholder="What should the agents build?"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-300 mb-1.5">
                  Initial context <span className="text-slate-600">(optional)</span>
                </label>
                <textarea
                  value={newInitialContext}
                  onChange={(e) => setNewInitialContext(e.target.value)}
                  rows={4}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-y"
                  placeholder="Requirements, constraints, users, risks, or notes the teams should read first..."
                />
                <p className="text-xs text-slate-500 mt-1">
                  Saved as project context immediately, so feasibility and future workers can use it.
                </p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-slate-300 mb-1.5">
                    Git repository URL <span className="text-slate-600">(optional)</span>
                  </label>
                  <input
                    value={newRepositoryUrl}
                    onChange={(e) => setNewRepositoryUrl(e.target.value)}
                    type="url"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="https://github.com/org/repo"
                  />
                </div>
                <div>
                  <label className="block text-sm text-slate-300 mb-1.5">Workspace mode</label>
                  <select
                    value={newGitMode}
                    onChange={(e) => setNewGitMode(e.target.value as "clone" | "init")}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  >
                    <option value="clone">Clone repository into project workspace</option>
                    <option value="init">Initialize a new Git repository</option>
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-slate-300 mb-1.5">
                    Branch / ref <span className="text-slate-600">(optional)</span>
                  </label>
                  <input
                    value={newGitBranch}
                    onChange={(e) => setNewGitBranch(e.target.value)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="default branch (clone) or main (new repo)"
                  />
                  <p className="text-xs text-slate-500 mt-1">
                    The workspace is created at the tool-service project boundary and is Git-managed.
                  </p>
                </div>
                <div>
                  <label className="block text-sm text-slate-300 mb-1.5">
                    Tags <span className="text-slate-600">(comma-separated)</span>
                  </label>
                  <input
                    value={newTags}
                    onChange={(e) => setNewTags(e.target.value)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="product, web, priority"
                  />
                </div>
              </div>
              {flows.length > 0 && (
                <div>
                  <label className="block text-sm text-slate-300 mb-1.5">Attach Flow (optional)</label>
                  <select
                    value={selectedFlowId}
                    onChange={(e) => setSelectedFlowId(e.target.value)}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  >
                    <option value="">None — use default workflow</option>
                    {flows.map((flow) => (
                      <option key={flow.id} value={flow.id}>
                        {flow.name} (v{flow.version})
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-slate-500 mt-1">
                    The selected flow will replace the default 18-state workflow for this project.
                  </p>
                </div>
              )}
              {error && <ErrorBanner tone="error">{error}</ErrorBanner>}
              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreate(false);
                    resetCreateForm();
                  }}
                  className="flex-1 px-3 py-2 border border-slate-700 rounded-lg text-sm text-slate-300 hover:text-white hover:border-slate-600 hover:bg-slate-800 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating || !newName.trim()}
                  className="flex-1 px-3 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
                >
                  {creating ? "Creating…" : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

interface ProjectRowProps {
  project: Project;
  isSelected: boolean;
  isExpanded: boolean;
  hasDescription: boolean;
  onToggleSelect: () => void;
  onToggleExpand: () => void;
  onArchive: () => void;
  onDelete: () => void;
  actionsDisabled: boolean;
  readOnly: boolean;
}

/**
 * Single row in the projects table. Owns the hover-revealed quick actions and
 * the optional inline description expansion. Extracted from the main page to
 * keep the table body readable.
 */
function ProjectRow({
  project: p,
  isSelected,
  isExpanded,
  hasDescription,
  onToggleSelect,
  onToggleExpand,
  onArchive,
  onDelete,
  actionsDisabled,
  readOnly,
}: ProjectRowProps) {
  // Quick-action buttons stay hidden by default to keep the row calm, but they
  // become visible on hover/focus and stay visible while the row is selected
  // or expanded so the user never loses access to them.
  const quickActionsHidden = !isSelected && !isExpanded;

  return (
    <>
      <tr
        className={clsx(
          "group transition-colors",
          "hover:bg-slate-800/40",
          "focus-within:bg-slate-800/30",
          isSelected && "bg-blue-950/30 hover:bg-blue-950/40"
        )}
      >
        <td className="px-4 py-3">
          {!readOnly && (
            <RowCheckbox
              checked={isSelected}
              onChange={onToggleSelect}
              ariaLabel={`Select ${p.name}`}
              className="min-h-11 min-w-11"
            />
          )}
        </td>
        <td className="px-4 py-3">
          <div className="flex items-start gap-1.5">
            {hasDescription ? (
              <button
                type="button"
                onClick={onToggleExpand}
                aria-label={
                  isExpanded
                    ? `Collapse description for ${p.name}`
                    : `Expand description for ${p.name}`
                }
                aria-expanded={isExpanded}
                aria-controls={`project-description-${p.id}`}
                title={isExpanded ? "Hide description" : "Show description"}
                className="mt-0.5 inline-flex min-h-11 min-w-11 items-center justify-center rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800/70 focus-visible:bg-slate-800/70 transition-colors"
              >
                {isExpanded ? (
                  <ChevronDown size={12} aria-hidden="true" />
                ) : (
                  <ChevronRight size={12} aria-hidden="true" />
                )}
              </button>
            ) : (
              <span className="w-4 inline-block" aria-hidden="true" />
            )}
            <div className="min-w-0 flex-1">
              {readOnly ? (
                <span className="inline-flex min-h-11 items-center font-medium text-slate-100">
                  {p.name}
                </span>
              ) : (
                <Link
                  href={`/projects/${p.id}`}
                  prefetch={false}
                  className="inline-flex min-h-11 items-center font-medium text-slate-100 group-hover:text-white transition-colors"
                >
                  {p.name}
                </Link>
              )}
              {hasDescription && !isExpanded && (
                <div className="text-xs text-slate-500 mt-0.5 truncate max-w-xs">
                  {p.description}
                </div>
              )}
            </div>
          </div>
        </td>
        <td className="px-4 py-3">
          <span
            className={clsx(
              "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium text-white",
              STATE_COLORS[p.state] ?? "bg-slate-600"
            )}
          >
            <span className="w-1 h-1 rounded-full bg-white/60" />
            {p.state?.replace(/_/g, " ")}
          </span>
        </td>
        <td className="px-4 py-3 text-slate-500 text-xs hidden sm:table-cell">
          {formatDistanceToNow(new Date(p.created_at), { addSuffix: true })}
        </td>
        <td className="px-4 py-3 text-slate-500 text-xs hidden md:table-cell">
          {formatDistanceToNow(new Date(p.updated_at), { addSuffix: true })}
        </td>
        <td className="px-4 py-3 text-right">
          <div
            className={clsx(
              "flex items-center justify-end gap-1 transition-opacity",
              // Reveal on hover (group) or keyboard focus-within; keep visible
              // when the row is selected or expanded.
              quickActionsHidden &&
                "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-within:opacity-100"
            )}
          >
            {!readOnly && (
              <>
                <Link
                  href={`/projects/${p.id}`}
                  prefetch={false}
                  className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1 px-3 py-2 text-xs text-slate-400 hover:text-white rounded transition-colors"
                  aria-label={`Open ${p.name}`}
                >
                  Open
                  <ArrowRight size={12} aria-hidden="true" />
                </Link>
                <button
                  type="button"
                  onClick={async () => {
                    if (!window.confirm(`Archive project "${p.name}"? This removes it from active work.`)) return;
                    onArchive();
                  }}
                  disabled={actionsDisabled}
                  title="Archive project"
                  aria-label={`Archive project ${p.name}`}
                  className="inline-flex min-h-11 min-w-11 items-center justify-center text-slate-500 hover:text-amber-300 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Archive size={12} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    if (!window.confirm(`Permanently delete project "${p.name}"? This cannot be undone.`)) return;
                    onDelete();
                  }}
                  disabled={actionsDisabled}
                  title="Delete project"
                  aria-label={`Delete project ${p.name}`}
                  className="inline-flex min-h-11 min-w-11 items-center justify-center text-slate-500 hover:text-red-400 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Trash2 size={12} aria-hidden="true" />
                </button>
              </>
            )}
          </div>
        </td>
      </tr>
      {/* Expanded description row — full text, no truncation. Only rendered
          when this project is the expanded one AND has a description. */}
      {isExpanded && hasDescription && (
        <tr
          className={clsx(
            "bg-slate-950/40",
            isSelected && "bg-blue-950/25"
          )}
        >
          <td />
          <td id={`project-description-${p.id}`} colSpan={5} className="px-4 py-3">
            <div className="rounded-md border border-slate-800/80 bg-slate-900/60 p-3">
              <div className="text-xxs font-semibold uppercase tracking-wider text-slate-500 mb-1">
                Description
              </div>
              <p className="text-sm text-slate-300 whitespace-pre-wrap break-words">
                {p.description}
              </p>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
