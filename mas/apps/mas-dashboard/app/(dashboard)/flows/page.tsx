"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import { ArrowRight, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import type { Flow } from "@/lib/flow-types";
import { BulkActionBar, RowCheckbox, SelectAllCheckbox } from "@/components/ui/BulkActionBar";
import { useBulkSelection } from "@/lib/use-bulk-selection";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { FilterChip } from "@/components/ui/FilterChips";

type HttpError = Error & { status?: number };

export default function FlowsPage() {
  const [flows, setFlows] = useState<Flow[]>([]);
  const flowsRef = useRef<Flow[] | null>(null);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "active" | "inactive">("all");
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loadStale, setLoadStale] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [hasReadContext, setHasReadContext] = useState(false);
  const hasReadContextRef = useRef(false);

  const handleAccessDenied = useCallback(() => {
    const hadReadContext = hasReadContextRef.current || flowsRef.current !== null;
    setAccessDenied(true);
    setHasReadContext(hadReadContext);
    setLoadError("");
    setLoadStale(false);
    setBulkError("");
    setSearch("");
    setFilter("all");
  }, []);

  const load = useCallback(async () => {
    if (accessDenied) return;
    setLoading(true);
    setLoadError("");
    try {
      const params = new URLSearchParams({ limit: "1000" });
      if (filter !== "all") params.set("is_active", String(filter === "active"));
      const res = await fetch(`/api/flows?${params}`, { cache: "no-store" });
      if (!res.ok) {
        const error = new Error(`HTTP ${res.status}`) as HttpError;
        error.status = res.status;
        throw error;
      }
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error("Invalid flows response");
      flowsRef.current = data;
      hasReadContextRef.current = true;
      setFlows(data);
      setHasLoaded(true);
      setHasReadContext(true);
      setLoadStale(false);
    } catch (cause) {
      const status = cause instanceof Error ? (cause as HttpError).status : undefined;
      if (status === 401 || status === 403) {
        handleAccessDenied();
        return;
      }
      setLoadError(cause instanceof Error ? cause.message : "Failed to load flows");
      setLoadStale(flowsRef.current !== null);
    } finally {
      setLoading(false);
    }
  }, [accessDenied, filter, handleAccessDenied]);

  useEffect(() => { void load(); }, [load]);

  const requestRefresh = () => {
    if (loading || accessDenied) return;
    void load();
  };

  async function deleteFlow(id: string) {
    if (accessDenied) return;
    setDeleting(id);
    try {
      const res = await fetch(`/api/flows/${id}`, { method: "DELETE" });
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          handleAccessDenied();
          return;
        }
        const detail = await res.text().catch(() => `HTTP ${res.status}`);
        setBulkError(`Failed to delete flow: ${detail}`);
        return;
      }
      await load();
    } finally {
      setDeleting(null);
    }
  }

  async function handleBulkDelete() {
    if (accessDenied || selection.selectedCount === 0) return;
    const ids = Array.from(selection.selected);
    setBulkDeleting(true);
    setBulkError("");
    let failed = 0;
    try {
      const results = await Promise.allSettled(
        ids.map(async (id) => {
          const res = await fetch(`/api/flows/${id}`, { method: "DELETE" });
          if (!res.ok) {
            const error = new Error(`HTTP ${res.status}`) as HttpError;
            error.status = res.status;
            throw error;
          }
        })
      );
      const denied = results.some(
        (result) =>
          result.status === "rejected" &&
          result.reason instanceof Error &&
          ((result.reason as HttpError).status === 401 ||
            (result.reason as HttpError).status === 403),
      );
      if (denied) {
        handleAccessDenied();
        return;
      }
      for (const r of results) if (r.status === "rejected") failed++;
      if (failed > 0) {
        setBulkError(
          `Deleted ${ids.length - failed} of ${ids.length} flow${ids.length === 1 ? "" : "s"} (${failed} failed).`
        );
      }
      await load();
      selection.clear();
    } finally {
      setBulkDeleting(false);
    }
  }

  const activeCount = flows.filter((f) => f.is_active).length;
  const inactiveCount = flows.length - activeCount;

  // Client-side name search layered on top of the server-side status filter.
  // Trims and case-folds the query and the candidate for forgiving matches.
  const visibleFlows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return flows;
    return flows.filter((f) => {
      if (f.name.toLowerCase().includes(q)) return true;
      if (f.description?.toLowerCase().includes(q)) return true;
      return false;
    });
  }, [flows, search]);

  // Group flows by canonical name (case-insensitive) so we can show how many
  // versions of the "same" flow exist — used to power the version comparison
  // badge in the table.
  const versionCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const f of flows) {
      const key = f.name.trim().toLowerCase();
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return map;
  }, [flows]);

  const flowIds = useMemo(() => visibleFlows.map((f) => f.id), [visibleFlows]);
  const selection = useBulkSelection(flowIds);
  // Drop selections when the filter or list changes.
  useEffect(() => {
    selection.prune();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowIds.join(",")]);
  useEffect(() => {
    if (accessDenied) selection.clear();
    // Selection is intentionally cleared only when the authority boundary is entered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessDenied]);

  return (
    <div className="dashboard-page">
      <PageHeader
        icon="git-branch"
        title="Orchestration Flows"
        description={
          <>
            {flows.length} total · {activeCount} active · {inactiveCount} inactive
            {search.trim() && (
              <span className="text-slate-500">
                {" "}
                · {visibleFlows.length} match{visibleFlows.length === 1 ? "" : "es"} for &ldquo;{search.trim()}&rdquo;
              </span>
            )}
          </>
        }
        actions={!accessDenied ? (
          <>
            <button
              type="button"
              onClick={requestRefresh}
              disabled={loading}
              title="Refresh"
              aria-label="Refresh flows"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-slate-700 text-slate-400 hover:text-slate-100 hover:border-slate-500 hover:bg-slate-800/70 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
            </button>
            <Link
              href="/flows/new"
              prefetch={false}
              className="inline-flex min-h-11 items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg shadow-sm shadow-blue-500/10 transition-colors"
            >
              <Plus size={14} />
              New Flow
            </Link>
          </>
        ) : undefined}
      />

      {accessDenied && (
        <section
          className="mx-4 mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"
          role="region"
          aria-label="Flows access status"
        >
          <h2 className="font-medium text-amber-50">Flows access denied</h2>
          <p className="mt-1 text-amber-100/80">
            {hasReadContext
              ? "Previously loaded flow definitions remain visible for reference. Refresh, retry, New Flow, search, status filters, selection, editing, and deletion controls are hidden until authorization is restored."
              : "No live flow definitions are available while authorization is unavailable. Flow controls are hidden until authorization is restored."}
          </p>
        </section>
      )}

      {flows.length > 0 && !accessDenied && (
        <div className="dashboard-toolbar flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search
              size={14}
              aria-hidden
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none"
            />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by name or description…"
              aria-label="Filter flows by name or description"
              className="min-h-11 w-full pl-8 pr-8 py-1.5 rounded-lg bg-slate-950/60 border border-slate-800 text-sm text-slate-200 placeholder:text-slate-500 focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/40 transition-colors"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
                title="Clear search"
                className="absolute right-0.5 top-1/2 min-h-11 min-w-11 -translate-y-1/2 inline-flex items-center justify-center rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800/70 transition-colors"
              >
                <X size={12} />
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter flows by status">
            <FilterChip
              active={filter === "all"}
              onClick={() => setFilter("all")}
              count={flows.length}
              className="min-h-11"
            >
              All
            </FilterChip>
            <FilterChip
              active={filter === "active"}
              onClick={() => setFilter("active")}
              count={activeCount}
              activeTone="emerald"
              className="min-h-11"
            >
              Active
            </FilterChip>
            <FilterChip
              active={filter === "inactive"}
              onClick={() => setFilter("inactive")}
              count={inactiveCount}
              className="min-h-11"
            >
              Inactive
            </FilterChip>
          </div>
        </div>
      )}

      {loadError && !accessDenied && (
        <ErrorBanner
          tone={loadStale ? "warning" : "error"}
          title={loadStale ? "Showing last known flows" : "Flows load failed"}
          action={(
            <button type="button" onClick={requestRefresh} disabled={loading} className="min-h-11 rounded border border-current px-3 py-2 text-xs font-medium hover:bg-white/10 disabled:opacity-50">
              Retry
            </button>
          )}
        >
          {loadStale ? `${loadError}. The latest flows refresh failed; retained flow definitions remain visible.` : loadError}
        </ErrorBanner>
      )}

      {bulkError && !accessDenied && <ErrorBanner tone="warning">{bulkError}</ErrorBanner>}

      {!accessDenied && selection.selectedCount > 0 && (
        <BulkActionBar
          selectedCount={selection.selectedCount}
          totalCount={visibleFlows.length}
          loading={bulkDeleting}
          action="delete"
          onAction={handleBulkDelete}
          onClear={selection.clear}
        />
      )}

      <div className="dashboard-surface overflow-hidden">
        {loading && !hasLoaded ? (
          <div className="p-8 text-center text-slate-500 text-sm">Loading…</div>
        ) : accessDenied && flows.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="key"
              title="No live flow definitions are available"
              description="Flow definitions cannot be read while authorization is unavailable. Controls remain hidden until access is restored."
            />
          </div>
        ) : loadError && !hasLoaded ? (
          <div className="p-8 text-center text-slate-400 text-sm">Unable to load flows. Use Retry to try again.</div>
        ) : flows.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="git-branch"
              title="No flows yet"
              description="Create an orchestration flow to define how projects are routed between teams and tools."
              action={
                <Link
                  href="/flows/new"
                  prefetch={false}
                  className="inline-flex min-h-11 items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
                >
                  <Plus size={14} />
                  New Flow
                </Link>
              }
            />
          </div>
        ) : visibleFlows.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="git-branch"
              title="No flows match your search"
              description={`No flows whose name or description contains &ldquo;${search.trim()}&rdquo;. Clear the search to see ${flows.length} flow${flows.length === 1 ? "" : "s"}.`}
              action={
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="inline-flex min-h-11 items-center gap-1.5 px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-100 text-sm font-medium rounded-lg transition-colors"
                >
                  <X size={14} />
                  Clear search
                </button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label="Flows list">
              <caption className="sr-only">
                Flows list. Use the search and status filters to change the displayed flows.
              </caption>
              <thead>
                <tr className="border-b border-slate-800">
                  <th scope="col" className="px-4 py-3 w-16">
                    {!accessDenied && (
                      <SelectAllCheckbox
                        checked={selection.isAllSelected}
                        indeterminate={selection.isIndeterminate}
                        onChange={selection.toggleAll}
                        ariaLabel="Select all flows"
                        className="min-h-11 min-w-11"
                      />
                    )}
                  </th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Name</th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Version</th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Nodes</th>
                  <th scope="col" className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider hidden sm:table-cell">Updated</th>
                  <th scope="col" className="px-4 py-3"><span className="sr-only">Quick actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {visibleFlows.map((flow) => {
                const nodes = flow.definition_json?.nodes ?? [];
                const nodeCount = nodes.length;
                // Bucket nodes by type for the tooltip breakdown.
                const nodeBreakdown = nodes.reduce<Record<string, number>>((acc, n) => {
                  acc[n.type] = (acc[n.type] ?? 0) + 1;
                  return acc;
                }, {});
                const breakdownEntries = Object.entries(nodeBreakdown).sort((a, b) => b[1] - a[1]);
                const tooltipLabel = breakdownEntries.length
                  ? breakdownEntries.map(([type, count]) => `${count} ${type}`).join(", ")
                  : "No nodes defined";
                // Version comparison: count how many flows share this name
                // (case-insensitive). If there are siblings, we have a
                // versioned family and can show a hint of rank/diff.
                const nameKey = flow.name.trim().toLowerCase();
                const siblingCount = versionCounts.get(nameKey) ?? 1;
                const hasOlderVersions = flow.version > 1 && siblingCount > 1;
                const hasNewerSiblings = siblingCount > 1;
                const isSelected = selection.selected.has(flow.id);
                const statusClass = flow.is_active
                  ? "bg-emerald-600/20 text-emerald-100 border border-emerald-600/30"
                  : "bg-slate-700/40 text-slate-400 border border-slate-600/30";
                return (
                  <tr
                    key={flow.id}
                    className={clsx(
                      "hover:bg-slate-800/50 transition-colors group",
                      isSelected && "bg-blue-950/30 hover:bg-blue-950/40"
                    )}
                  >
                    <td className="px-4 py-3">
                      {!accessDenied && (
                        <RowCheckbox
                          checked={isSelected}
                          onChange={() => selection.toggle(flow.id)}
                          ariaLabel={`Select ${flow.name}`}
                          className="min-h-11 min-w-11"
                        />
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {accessDenied ? (
                        <span className="inline-flex min-h-11 items-center font-medium text-slate-100">
                          {flow.name}
                        </span>
                      ) : (
                        <Link
                          href={`/flows/${flow.id}`}
                          prefetch={false}
                          className="inline-flex min-h-11 items-center font-medium text-slate-100 group-hover:text-white"
                        >
                          {flow.name}
                        </Link>
                      )}
                      {flow.description && (
                        <div className="text-xs text-slate-500 mt-0.5 truncate max-w-xs">{flow.description}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <span className="text-slate-300 font-mono text-xs">v{flow.version}</span>
                        {hasNewerSiblings && (
                          <span
                            title={
                              hasOlderVersions
                                ? `${siblingCount} versions of &ldquo;${flow.name}&rdquo; exist — this is v${flow.version} of ${siblingCount}.`
                                : `First version of &ldquo;${flow.name}&rdquo;.`
                            }
                            className={clsx(
                              "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xxs font-semibold border",
                              hasOlderVersions
                                ? "bg-indigo-500/15 text-indigo-200 border-indigo-400/30"
                                : "bg-slate-800/60 text-slate-400 border-slate-700"
                            )}
                            aria-label={
                              hasOlderVersions
                                ? `${siblingCount} versions of ${flow.name} exist`
                                : "First version"
                            }
                          >
                            {hasOlderVersions ? `${siblingCount} versions` : "v1"}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={clsx(
                        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium",
                        statusClass
                      )}>
                        <span className={clsx("w-1.5 h-1.5 rounded-full", flow.is_active ? "bg-emerald-400" : "bg-slate-500")} />
                        {flow.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-300 font-mono text-xs">
                      <span
                        title={tooltipLabel}
                        aria-label={`${nodeCount} nodes: ${tooltipLabel}`}
                        className="cursor-help underline decoration-dotted decoration-slate-600 underline-offset-2 hover:text-slate-100 hover:decoration-slate-400 transition-colors"
                      >
                        {nodeCount}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-500 text-xs hidden sm:table-cell">
                      {formatDistanceToNow(new Date(flow.updated_at), { addSuffix: true })}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex gap-1 justify-end items-center">
                        {!accessDenied && (
                          <>
                            <Link
                              href={`/flows/${flow.id}`}
                              prefetch={false}
                              className="inline-flex min-h-11 items-center gap-1 px-2 py-2 text-xs text-slate-400 hover:text-white rounded transition-colors"
                            >
                              Edit
                              <ArrowRight size={12} />
                            </Link>
                            <button
                              type="button"
                              onClick={() => {
                                if (!window.confirm(`Delete flow "${flow.name}" v${flow.version}? This cannot be undone.`)) return;
                                deleteFlow(flow.id);
                              }}
                              disabled={deleting === flow.id}
                              title="Delete flow"
                              aria-label={`Delete flow ${flow.name} v${flow.version}`}
                              className="inline-flex min-h-11 min-w-11 items-center justify-center text-slate-500 hover:text-red-400 rounded transition-colors disabled:opacity-40"
                            >
                              <Trash2 size={12} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
