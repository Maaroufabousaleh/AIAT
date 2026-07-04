"use client";

import React, {
  useEffect,
  useState,
  useCallback,
  useRef,
  useMemo,
} from "react";
import {
  RefreshCw,
  Play,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  CheckSquare,
  Square,
  Inbox,
  ServerCrash,
  Sparkles,
  AlertCircle,
  Info,
  Clock,
  ArrowUpDown,
  X,
  Flame,
} from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { KpiCard } from "@/components/ui/KpiCard";
import { FilterChip } from "@/components/ui/FilterChips";
import { formatDistanceToNow } from "date-fns";
import clsx from "clsx";

interface DeadLetter {
  id: string;
  stream: string;
  message_type: string;
  failure_reason: string;
  retry_count: number;
  created_at: string;
  envelope: Record<string, unknown>;
}

interface DLQResponse {
  dead_letters: DeadLetter[];
  total: number;
}

/**
 * Severity buckets used for sorting and visual hierarchy.
 *  - critical: many retries — likely a poison message
 *  - high:     recent failures with at least one retry
 *  - medium:   failures with some retries
 *  - low:      no retries yet (just failed once)
 */
type Severity = "critical" | "high" | "medium" | "low";

type SortMode = "severity" | "age-desc" | "age-asc" | "retries-desc";

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

/**
 * Classify a dead-letter entry into a severity bucket. The buckets drive the
 * sort order (critical first) and the colored left-border on each card.
 */
function severityFor(letter: DeadLetter): Severity {
  if (letter.retry_count >= 3) return "critical";
  if (letter.retry_count >= 2) return "high";
  if (letter.retry_count >= 1) return "medium";
  return "low";
}

const SEVERITY_STYLES: Record<Severity, { border: string; pill: string }> = {
  critical: {
    border: "border-l-rose-500/80",
    pill: "bg-rose-500/15 text-rose-200 border-rose-400/40",
  },
  high: {
    border: "border-l-orange-500/70",
    pill: "bg-orange-500/15 text-orange-200 border-orange-400/40",
  },
  medium: {
    border: "border-l-amber-500/70",
    pill: "bg-amber-500/15 text-amber-200 border-amber-400/40",
  },
  low: {
    border: "border-l-slate-500/60",
    pill: "bg-slate-700/40 text-slate-300 border-slate-600/50",
  },
};

export default function DLQPage() {
  const [data, setData] = useState<DLQResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [replaying, setReplaying] = useState<Set<string>>(new Set());
  const [replayResults, setReplayResults] = useState<
    Record<string, "ok" | "err">
  >({});
  /** Optional severity filter chip — when set, only entries in that bucket render. */
  const [severityFilter, setSeverityFilter] = useState<Severity | null>(null);
  /** Active sort mode. Defaults to severity (critical first). */
  const [sortMode, setSortMode] = useState<SortMode>("severity");
  const [lastFetchedAt, setLastFetchedAt] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchDLQ = useCallback(async () => {
    try {
      const res = await fetch("/api/dlq");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: DLQResponse = await res.json();
      setData(json);
      setLastFetchedAt(Date.now());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch DLQ");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDLQ();
    intervalRef.current = setInterval(fetchDLQ, 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchDLQ]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (!data) return;
    if (selected.size === data.dead_letters.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(data.dead_letters.map((d) => d.id)));
    }
  };

  const replayOne = async (id: string) => {
    setReplaying((prev) => new Set(prev).add(id));
    try {
      const res = await fetch(`/api/dlq/${id}/replay`, { method: "POST" });
      setReplayResults((prev) => ({ ...prev, [id]: res.ok ? "ok" : "err" }));
      if (res.ok) {
        setTimeout(() => fetchDLQ(), 1000);
      }
    } catch {
      setReplayResults((prev) => ({ ...prev, [id]: "err" }));
    } finally {
      setReplaying((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const replaySelected = async () => {
    const ids = Array.from(selected);
    await Promise.all(ids.map((id) => replayOne(id)));
    setSelected(new Set());
  };

  const letters = useMemo(() => data?.dead_letters ?? [], [data?.dead_letters]);

  // Counts per severity bucket — surfaced as KPI cards and filter chip badges.
  const severityCounts = useMemo(() => {
    const counts: Record<Severity, number> = {
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
    };
    for (const l of letters) counts[severityFor(l)]++;
    return counts;
  }, [letters]);

  // Apply the optional severity filter and the active sort mode.
  const visibleLetters = useMemo(() => {
    const filtered = severityFilter
      ? letters.filter((l) => severityFor(l) === severityFilter)
      : letters;
    const ts = (l: DeadLetter) => new Date(l.created_at).getTime();
    const sorted = [...filtered].sort((a, b) => {
      switch (sortMode) {
        case "severity": {
          const ra = SEVERITY_RANK[severityFor(a)];
          const rb = SEVERITY_RANK[severityFor(b)];
          if (ra !== rb) return ra - rb;
          return ts(b) - ts(a);
        }
        case "age-desc":
          return ts(b) - ts(a);
        case "age-asc":
          return ts(a) - ts(b);
        case "retries-desc":
          return b.retry_count - a.retry_count;
      }
    });
    return sorted;
  }, [letters, severityFilter, sortMode]);

  if (loading) {
    return (
      <div
        className="flex items-center justify-center h-64"
        role="status"
        aria-live="polite"
      >
        <RefreshCw className="w-6 h-6 animate-spin text-blue-400" />
        <span className="sr-only">Loading dead letter queue…</span>
      </div>
    );
  }

  const allSelected = letters.length > 0 && selected.size === letters.length;
  const visibleSelectedCount = visibleLetters.filter((l) =>
    selected.has(l.id),
  ).length;

  return (
    <div className="dashboard-page">
      <PageHeader
        icon="inbox"
        title="Dead Letter Queue"
        description={`${data?.total ?? 0} message${data?.total !== 1 ? "s" : ""} in queue · auto-refresh every 30s`}
        actions={
          <>
            {selected.size > 0 && (
              <button
                type="button"
                onClick={replaySelected}
                aria-label={`Replay ${selected.size} selected dead letters`}
                className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white rounded-lg text-sm font-medium shadow-sm shadow-blue-950/20 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
              >
                <Play className="w-4 h-4" />
                Replay {selected.size} selected
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setLoading(true);
                fetchDLQ();
              }}
              aria-label="Refresh dead letter queue"
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 active:bg-slate-800/80 text-slate-200 rounded-lg text-sm transition-colors border border-slate-700/80 focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            >
              <RefreshCw className="w-4 h-4" />
              Refresh
            </button>
          </>
        }
      />

      {/* KPI summary — gives the page a quick at-a-glance health read. */}
      {letters.length > 0 && (
        <section
          aria-label="Dead letter queue summary"
          className="grid grid-cols-2 lg:grid-cols-4 gap-3"
        >
          <KpiCard
            label="Total in queue"
            value={letters.length}
            icon="inbox"
            tone="info"
            hint={`Across ${new Set(letters.map((l) => l.stream)).size} stream${new Set(letters.map((l) => l.stream)).size === 1 ? "" : "s"}`}
          />
          <KpiCard
            label="Critical"
            value={severityCounts.critical}
            icon="alert-triangle"
            tone="negative"
            hint="3+ retries — likely poison"
          />
          <KpiCard
            label="Recently failed"
            value={
              letters.filter(
                (l) =>
                  lastFetchedAt - new Date(l.created_at).getTime() <
                  60 * 60 * 1000,
              ).length
            }
            icon="clock"
            tone="warning"
            hint="Last hour"
          />
          <KpiCard
            label="Replayed"
            value={
              Object.values(replayResults).filter((r) => r === "ok").length
            }
            icon="check-circle"
            tone="positive"
            hint="Since you opened this view"
          />
        </section>
      )}

      {/* Toolbar — severity filter chips and sort selector. */}
      {letters.length > 0 && (
        <div
          className="dashboard-toolbar flex flex-wrap items-center gap-3"
          role="toolbar"
          aria-label="Queue filters and sorting"
        >
          <div
            className="flex items-center gap-2"
            role="group"
            aria-label="Filter by severity"
          >
            <span className="text-xxs font-semibold uppercase tracking-wider text-slate-500">
              Severity
            </span>
            <FilterChip
              active={severityFilter === null}
              onClick={() => setSeverityFilter(null)}
              activeTone="blue"
              count={letters.length}
            >
              All
            </FilterChip>
            {(Object.keys(SEVERITY_RANK) as Severity[]).map((sev) => (
              <FilterChip
                key={sev}
                active={severityFilter === sev}
                onClick={() =>
                  setSeverityFilter(sev === severityFilter ? null : sev)
                }
                activeTone={sev === "critical" ? "amber" : "gray"}
                count={severityCounts[sev]}
              >
                {sev}
              </FilterChip>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <ArrowUpDown
              className="w-3.5 h-3.5 text-slate-500"
              aria-hidden="true"
            />
            <label
              htmlFor="dlq-sort"
              className="text-xxs font-semibold uppercase tracking-wider text-slate-500"
            >
              Sort
            </label>
            <select
              id="dlq-sort"
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortMode)}
              className="bg-slate-950/55 border border-slate-700 hover:border-slate-600 text-slate-200 text-xs rounded-md px-2 py-1 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            >
              <option value="severity">Severity (critical first)</option>
              <option value="retries-desc">Most retries</option>
              <option value="age-desc">Newest first</option>
              <option value="age-asc">Oldest first</option>
            </select>
            {(severityFilter || sortMode !== "severity") && (
              <button
                type="button"
                onClick={() => {
                  setSeverityFilter(null);
                  setSortMode("severity");
                }}
                className="inline-flex items-center gap-1 px-2 py-1 text-xxs text-slate-400 hover:text-slate-200 transition-colors"
                aria-label="Reset filters and sort"
              >
                <X className="w-3 h-3" />
                Reset
              </button>
            )}
          </div>
        </div>
      )}

      {error && (
        <ErrorBanner
          tone="warning"
          title="Could not reach the dead-letter queue"
        >
          {error}
        </ErrorBanner>
      )}

      {letters.length === 0 && !error ? (
        <div className="py-8">
          <EmptyState
            icon="sparkles"
            tone="positive"
            title="Queue is empty"
            description="No dead letters — all messages processed successfully. This page auto-refreshes every 30 seconds."
          />
        </div>
      ) : (
        <>
          {/* Selection bar — only when items are checked. */}
          {selected.size > 0 && (
            <div
              role="region"
              aria-label="Bulk selection"
              className="flex items-center gap-3 px-4 py-2.5 rounded-lg border border-blue-800/60 bg-blue-950/40"
            >
              <CheckSquare size={16} className="text-blue-400" />
              <span className="text-sm font-medium text-blue-200">
                {selected.size} selected
                {visibleSelectedCount !== selected.size && (
                  <span className="text-xs opacity-70 ml-1.5">
                    ({visibleSelectedCount} on screen)
                  </span>
                )}
              </span>
              <div className="ml-auto flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSelected(new Set())}
                  className="flex items-center gap-1 px-2.5 py-1 text-xs text-slate-300 hover:text-white rounded transition-colors"
                >
                  <X size={12} />
                  Clear
                </button>
                <button
                  type="button"
                  onClick={toggleSelectAll}
                  className="px-2.5 py-1 text-xs text-slate-300 hover:text-white rounded transition-colors"
                >
                  {allSelected ? "Deselect all" : "Select all"}
                </button>
              </div>
            </div>
          )}

          {visibleLetters.length === 0 ? (
            <div className="py-8">
              <EmptyState
                icon="inbox"
                tone="muted"
                title="No matches"
                description={`No dead letters match the "${severityFilter}" filter. Try clearing it.`}
                action={
                  <button
                    type="button"
                    onClick={() => setSeverityFilter(null)}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    <X className="w-3 h-3" />
                    Clear filter
                  </button>
                }
              />
            </div>
          ) : (
            <div
              className="space-y-2"
              role="list"
              aria-label="Dead letter queue entries"
            >
              {visibleLetters.map((letter) => {
                const sev = severityFor(letter);
                const sevStyle = SEVERITY_STYLES[sev];
                const isExpanded = expanded.has(letter.id);
                const isSelected = selected.has(letter.id);
                const isReplaying = replaying.has(letter.id);
                const result = replayResults[letter.id];

                return (
                  <article
                    key={letter.id}
                    role="listitem"
                    aria-labelledby={`dlq-${letter.id}-title`}
                    className={clsx(
                      "dashboard-surface overflow-hidden border-l-4 transition-colors",
                      sevStyle.border,
                      isSelected && "ring-1 ring-blue-500/60",
                      "hover:border-slate-700",
                    )}
                  >
                    {/* Card header — selector, severity, identity, age, replay action. */}
                    <header className="flex flex-wrap items-center gap-3 px-4 py-3">
                      <button
                        type="button"
                        onClick={() => toggleSelect(letter.id)}
                        role="checkbox"
                        aria-checked={isSelected}
                        aria-label={`Select dead letter ${letter.id}`}
                        className="text-slate-400 hover:text-white transition-colors"
                      >
                        {isSelected ? (
                          <CheckSquare className="w-4 h-4 text-blue-400" />
                        ) : (
                          <Square className="w-4 h-4" />
                        )}
                      </button>

                      <span
                        className={clsx(
                          "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xxs font-semibold uppercase tracking-wider border",
                          sevStyle.pill,
                        )}
                        aria-label={`Severity: ${sev}`}
                      >
                        {sev === "critical" && (
                          <Flame className="w-3 h-3" aria-hidden="true" />
                        )}
                        {sev === "critical" ? "critical" : sev}
                      </span>

                      <div className="min-w-0 flex-1">
                        <div
                          id={`dlq-${letter.id}-title`}
                          className="flex items-center gap-2 min-w-0"
                        >
                          <span className="font-mono text-xs text-blue-300 truncate">
                            {letter.stream}
                          </span>
                          <span className="text-slate-600" aria-hidden="true">
                            /
                          </span>
                          <span className="font-mono text-xs text-slate-200 truncate">
                            {letter.message_type}
                          </span>
                        </div>
                        <div className="flex items-center gap-3 mt-0.5 text-xxs text-slate-500">
                          <span className="inline-flex items-center gap-1">
                            <Clock className="w-3 h-3" aria-hidden="true" />
                            <time
                              dateTime={letter.created_at}
                              title={new Date(
                                letter.created_at,
                              ).toLocaleString()}
                            >
                              {formatDistanceToNow(
                                new Date(letter.created_at),
                                { addSuffix: true },
                              )}
                            </time>
                          </span>
                          <span aria-hidden="true">·</span>
                          <span className="font-mono text-slate-600">
                            {letter.id.slice(0, 8)}
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 ml-auto">
                        <span
                          className={clsx(
                            "px-2 py-0.5 rounded-full text-xs font-medium",
                            letter.retry_count >= 3
                              ? "bg-rose-500/15 text-rose-300 border border-rose-400/30"
                              : letter.retry_count >= 1
                                ? "bg-amber-500/15 text-amber-300 border border-amber-400/30"
                                : "bg-slate-700/40 text-slate-300 border border-slate-600/50",
                          )}
                          aria-label={`${letter.retry_count} retries`}
                        >
                          {letter.retry_count}{" "}
                          {letter.retry_count === 1 ? "retry" : "retries"}
                        </span>

                        {result === "ok" ? (
                          <span
                            className="text-xs text-emerald-400 font-medium"
                            role="status"
                          >
                            Replayed
                          </span>
                        ) : result === "err" ? (
                          <span
                            className="text-xs text-rose-400 font-medium"
                            role="status"
                          >
                            Replay failed
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => replayOne(letter.id)}
                            disabled={isReplaying}
                            aria-label={`Replay dead letter ${letter.id}`}
                            className="inline-flex items-center gap-1.5 px-3 py-1 bg-slate-800 hover:bg-blue-600 active:bg-blue-700 text-slate-200 hover:text-white border border-slate-700 hover:border-blue-500 rounded text-xs font-medium transition-colors disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                          >
                            {isReplaying ? (
                              <RefreshCw
                                className="w-3 h-3 animate-spin"
                                aria-hidden="true"
                              />
                            ) : (
                              <Play className="w-3 h-3" aria-hidden="true" />
                            )}
                            {isReplaying ? "Replaying…" : "Replay"}
                          </button>
                        )}
                      </div>
                    </header>

                    {/* Card body — failure reason with full-text reveal on expand.
                        The collapsed view is truncated; the expanded view shows
                        the entire reason and the JSON envelope for debugging. */}
                    <div className="px-4 pb-3">
                      <div className="flex items-start gap-2 rounded-lg bg-slate-950/55 border border-slate-800/80 px-3 py-2">
                        {sev === "critical" ? (
                          <AlertCircle
                            className="w-3.5 h-3.5 text-rose-400 flex-shrink-0 mt-0.5"
                            aria-hidden="true"
                          />
                        ) : (
                          <AlertTriangle
                            className="w-3.5 h-3.5 text-amber-400 flex-shrink-0 mt-0.5"
                            aria-hidden="true"
                          />
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="text-xxs font-semibold uppercase tracking-wider text-slate-500 mb-0.5">
                            Failure reason
                          </div>
                          <p
                            className={clsx(
                              "text-xs font-mono text-rose-200/90 break-words",
                              !isExpanded && "line-clamp-2",
                            )}
                            title={letter.failure_reason}
                          >
                            {letter.failure_reason}
                          </p>
                        </div>
                      </div>
                    </div>

                    {/* Card footer — toggle for full envelope JSON. */}
                    <footer className="flex items-center gap-2 px-4 py-2 border-t border-slate-800/80 bg-slate-950/35">
                      <button
                        type="button"
                        onClick={() => toggleExpand(letter.id)}
                        aria-expanded={isExpanded}
                        aria-controls={`dlq-${letter.id}-envelope`}
                        className="inline-flex items-center gap-1.5 text-xxs font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 rounded px-1 py-0.5"
                      >
                        {isExpanded ? (
                          <ChevronDown
                            className="w-3.5 h-3.5"
                            aria-hidden="true"
                          />
                        ) : (
                          <ChevronRight
                            className="w-3.5 h-3.5"
                            aria-hidden="true"
                          />
                        )}
                        {isExpanded ? "Hide envelope" : "Inspect envelope"}
                      </button>
                      <span className="ml-auto text-xxs text-slate-500 inline-flex items-center gap-1">
                        <Info className="w-3 h-3" aria-hidden="true" />
                        Original message payload
                      </span>
                    </footer>

                    {isExpanded && (
                      <div
                        id={`dlq-${letter.id}-envelope`}
                        className="border-t border-slate-800/80 bg-slate-950/65"
                      >
                        <div className="px-4 py-2 border-b border-slate-800/60 flex items-center gap-2">
                          <ServerCrash
                            className="w-3.5 h-3.5 text-slate-500"
                            aria-hidden="true"
                          />
                          <span className="text-xxs font-semibold uppercase tracking-wider text-slate-500">
                            Envelope JSON
                          </span>
                        </div>
                        <div className="rounded-b-lg overflow-auto max-h-72">
                          <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-all px-4 py-3">
                            {JSON.stringify(letter.envelope, null, 2)}
                          </pre>
                        </div>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
