"use client";

import React, { useCallback, useState, useEffect, useRef, useMemo } from "react";
import { clsx } from "clsx";
import {
  TEAM_STREAMS,
  MSG_TYPE_COLORS,
  type TeamStreamId,
} from "@/lib/constants";
import { formatInTz } from "@/lib/datetime";
import {
  Copy,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Search,
  Trash2,
  X,
  Check,
} from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { PageHeader } from "@/components/ui/PageHeader";
import { FilterChip } from "@/components/ui/FilterChips";

interface MessageEnvelope {
  id?: string;
  type?: string;
  msg_type?: string;
  message_type: string;
  sender_id?: string;
  sender_role?: string;
  recipient_id?: string;
  project_id?: string;
  payload?: unknown;
  timestamp?: string;
  sent_at?: string;
  trace_id?: string;
  envelope?: MessageEnvelope;
}

interface FeedEntry {
  raw: string;
  parsed: MessageEnvelope | null;
  ts: number;
}

interface RecentStreamEntry {
  entry_id: string;
  envelope: string;
}

function parseFirstTimestamp(...values: Array<string | undefined>): number {
  for (const value of values) {
    if (!value) continue;
    const timestamp = Date.parse(value);
    if (Number.isFinite(timestamp)) return timestamp;
  }
  return Date.now();
}

function entryFromRaw(raw: string): FeedEntry {
  let parsed: MessageEnvelope | null = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    /* non-JSON */
  }
  const timestamp = parseFirstTimestamp(
    parsed?.timestamp,
    parsed?.sent_at,
    parsed?.envelope?.timestamp,
  );
  return { raw, parsed, ts: timestamp };
}

/** Strip a leading ISO timestamp prefix that some agents prepend to message_type. */
function normalizeMessageType(rawType: string | undefined): string {
  if (!rawType) return "—";
  return rawType.replace(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?\./,
    "",
  );
}

export default function StreamsPage() {
  const [teamId, setTeamId] = useState<TeamStreamId>("exec_ceo");
  const [entries, setEntries] = useState<FeedEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [groupByType, setGroupByType] = useState(false);
  const [copiedKey, setCopiedKey] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(false);
  const esRef = useRef<EventSource | null>(null);
  const entriesRef = useRef<FeedEntry[]>([]);
  const connectionIdRef = useRef(0);
  const streamFailedRef = useRef(false);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  const connect = useCallback((preserveEntries: boolean) => {
    const connectionId = ++connectionIdRef.current;
    const hadEntries = entriesRef.current.length > 0;
    const isCurrent = () => connectionIdRef.current === connectionId;

    esRef.current?.close();
    esRef.current = null;
    streamFailedRef.current = false;
    setConnected(false);
    setLoadError(null);
    setStale(false);
    if (!preserveEntries) {
      entriesRef.current = [];
      setEntries([]);
    }

    const reportError = (message: string) => {
      if (!isCurrent()) return;
      streamFailedRef.current = true;
      setConnected(false);
      setLoadError(message);
      setStale(entriesRef.current.length > 0 || hadEntries);
      esRef.current?.close();
      esRef.current = null;
    };

    fetch(`/api/streams/${teamId}?history=1&limit=50`, { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as { entries?: RecentStreamEntry[] };
      })
      .then((data) => {
        if (!isCurrent() || !data.entries) return;
        const history = data.entries.map((entry) => entryFromRaw(entry.envelope));
        const liveEntries = entriesRef.current;
        const liveRaw = new Set(liveEntries.map((entry) => entry.raw));
        const next = [...history.filter((entry) => !liveRaw.has(entry.raw)), ...liveEntries].slice(-500);
        entriesRef.current = next;
        setEntries(next);
        if (!streamFailedRef.current) setLoadError(null);
      })
      .catch((cause: unknown) => {
        if (!isCurrent()) return;
        reportError(cause instanceof Error ? `Stream history unavailable: ${cause.message}` : "Stream history unavailable");
      });

    const es = new EventSource(`/api/streams/${teamId}`);
    esRef.current = es;

    es.addEventListener("connected", () => {
      if (!isCurrent()) return;
      setConnected(true);
    });
    es.addEventListener("error", (event) => {
      const raw = (event as MessageEvent<string>).data;
      let message = "Stream disconnected.";
      if (raw) {
        try {
          const parsed = JSON.parse(raw) as { error?: string };
          if (parsed.error) message = parsed.error;
        } catch {
          message = raw;
        }
      }
      reportError(message);
    });

    es.onmessage = (e) => {
      if (!isCurrent() || pausedRef.current) return;
      setEntries((prev) => {
        const next = [...prev, entryFromRaw(e.data)].slice(-500);
        entriesRef.current = next;
        return next;
      });
    };
  }, [teamId]);

  useEffect(() => {
    setTypeFilter("all");
    setQuery("");
    connect(false);
  }, [connect]);

  useEffect(() => () => {
    connectionIdRef.current += 1;
    esRef.current?.close();
    esRef.current = null;
  }, []);

  // Auto-scroll
  useEffect(() => {
    if (!paused) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [entries, paused]);

  // Derive unique message types currently visible so we can populate filter chips.
  const availableTypes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of entries) {
      const t = normalizeMessageType(
        entry.parsed?.message_type ?? entry.parsed?.msg_type,
      );
      if (t === "—") continue;
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([type, count]) => ({ type, count }));
  }, [entries]);

  // Apply search query and type filter.
  const filteredEntries = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter((entry) => {
      const type = normalizeMessageType(
        entry.parsed?.message_type ?? entry.parsed?.msg_type,
      );
      if (typeFilter !== "all" && type !== typeFilter) return false;
      if (!q) return true;
      // Match against the raw JSON, sender, project, trace, and type for power-search.
      const haystack = [
        entry.raw,
        entry.parsed?.sender_id ?? "",
        entry.parsed?.project_id ?? "",
        entry.parsed?.trace_id ?? "",
        type,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [entries, query, typeFilter]);

  // Group filtered entries by message type (preserves chronological order within each group).
  const groupedEntries = useMemo(() => {
    if (!groupByType) return null;
    const groups = new Map<
      string,
      Array<{ entry: FeedEntry; index: number }>
    >();
    filteredEntries.forEach((entry, index) => {
      const type = normalizeMessageType(
        entry.parsed?.message_type ?? entry.parsed?.msg_type,
      );
      const bucket = groups.get(type) ?? [];
      bucket.push({ entry, index });
      groups.set(type, bucket);
    });
    return Array.from(groups.entries()).sort((a, b) =>
      a[0].localeCompare(b[0]),
    );
  }, [filteredEntries, groupByType]);

  const activeTeam = TEAM_STREAMS.find((t) => t.id === teamId);

  function msgTypeColor(type: string) {
    return MSG_TYPE_COLORS[type] ?? "bg-slate-600";
  }

  async function copyEntry(entry: FeedEntry, key: number) {
    const text =
      entry.raw || (entry.parsed ? JSON.stringify(entry.parsed, null, 2) : "");
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for older browsers / non-secure contexts.
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "absolute";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopiedKey(key);
      window.setTimeout(
        () => setCopiedKey((k) => (k === key ? null : k)),
        1500,
      );
    } catch {
      // Silent failure — clipboard rejection is non-critical here.
    }
  }

  function renderEntryRow(entry: FeedEntry, index: number) {
    const type = normalizeMessageType(
      entry.parsed?.message_type ?? entry.parsed?.msg_type,
    );
    const isOpen = expanded === index;
    const justCopied = copiedKey === index;

    return (
      <React.Fragment key={`row-${index}`}>
        <tr
          className={clsx(
            "border-b border-slate-800/70 transition-colors cursor-pointer",
            "hover:bg-slate-800/40 focus-within:bg-slate-800/40",
            isOpen && "bg-slate-800/30",
          )}
          onClick={() => setExpanded(isOpen ? null : index)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              setExpanded(isOpen ? null : index);
            }
          }}
          tabIndex={0}
          aria-expanded={isOpen}
          aria-label={`${type} message${entry.parsed?.sender_id ? ` from ${entry.parsed.sender_id}` : ""}`}
        >
          <td className="px-3 py-1.5 text-slate-500 whitespace-nowrap">
            {formatInTz(entry.ts, "yyyy-MM-dd HH:mm:ss.SSS")}
          </td>
          <td className="px-3 py-1.5">
            {type !== "—" ? (
              <span
                className={clsx(
                  "inline-flex px-1.5 py-0.5 rounded text-xxs font-medium text-white",
                  msgTypeColor(type),
                )}
              >
                {type}
              </span>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
          <td className="px-3 py-1.5 text-slate-400 truncate max-w-xs hidden md:table-cell">
            {entry.parsed?.sender_id ?? "—"}
          </td>
          <td className="px-3 py-1.5 text-slate-400 truncate hidden lg:table-cell">
            {entry.parsed?.project_id?.slice(0, 8) ?? "—"}
          </td>
          <td className="px-3 py-1.5 text-slate-400 truncate max-w-sm font-mono">
            {Boolean(entry.parsed?.payload)
              ? (() => {
                  const { timestamp: _ts, ...rest } = entry.parsed!;
                  return JSON.stringify(rest).slice(0, 80);
                })()
              : (() => {
                  try {
                    const p = JSON.parse(entry.raw);
                    delete p.timestamp;
                    return JSON.stringify(p).slice(0, 80);
                  } catch {
                    return entry.raw.slice(0, 80);
                  }
                })()}
          </td>
          <td className="px-2 py-1.5 text-right w-12">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                copyEntry(entry, index);
              }}
              aria-label={
                justCopied ? "Copied to clipboard" : "Copy message payload"
              }
              title={justCopied ? "Copied!" : "Copy payload"}
              className={clsx(
                "inline-flex min-h-11 min-w-11 items-center justify-center rounded-md border transition-colors",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70",
                justCopied
                  ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                  : "border-slate-700 text-slate-500 hover:text-slate-100 hover:border-slate-500 hover:bg-slate-800",
              )}
            >
              {justCopied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </td>
        </tr>
        {isOpen && (
          <tr key={`row-${index}-expanded`} className="bg-slate-950/60">
            <td colSpan={6} className="px-3 py-2">
              <pre className="text-xxs text-slate-300 overflow-x-auto whitespace-pre-wrap leading-relaxed">
                {JSON.stringify(entry.parsed ?? entry.raw, null, 2)}
              </pre>
            </td>
          </tr>
        )}
      </React.Fragment>
    );
  }

  return (
    <main
      className="flex h-full flex-col p-5 sm:p-6 lg:p-8 gap-4"
      aria-label="Agent stream monitor"
    >
      {/* Header */}
      <PageHeader
        icon="radio"
        title="Agent Stream Monitor"
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>{activeTeam?.description ?? "Live agent message feed."}</span>
            <span className="flex items-center gap-1.5">
              <span
                className={clsx(
                  "w-1.5 h-1.5 rounded-full",
                  connected ? "bg-emerald-400" : "bg-slate-600",
                )}
                aria-hidden
              />
              <span className="text-slate-500">
                {connected ? "connected" : loadError ? "disconnected" : "connecting…"}
              </span>
            </span>
          </span>
        }
        actions={
          <>
            <div className="relative">
              <Search
                size={13}
                aria-hidden
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500"
              />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter messages…"
                aria-label="Filter messages by text, sender, or project"
                className="min-h-11 bg-slate-950/60 border border-slate-700 rounded-lg pl-8 pr-8 py-1.5 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-400/60 focus:border-blue-400/40 w-56"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  className="absolute right-1.5 top-1/2 min-h-11 min-w-11 -translate-y-1/2 text-slate-500 hover:text-slate-200 rounded transition-colors"
                >
                  <X size={12} />
                </button>
              )}
            </div>

            <select
              value={teamId}
              onChange={(e) => setTeamId(e.target.value as TeamStreamId)}
              aria-label="Select team stream"
              className="min-h-11 bg-slate-950/60 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-400/60"
            >
              {TEAM_STREAMS.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.role} — {t.label}
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={() => connect(true)}
              aria-label="Reconnect stream"
              className="inline-flex min-h-11 items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 hover:text-white transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60"
            >
              <RefreshCw size={12} />
              Reconnect
            </button>

            <label className="inline-flex min-h-11 items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-700 text-xs text-slate-300 cursor-pointer hover:bg-slate-800/50 transition-colors">
              <input
                type="checkbox"
                checked={groupByType}
                onChange={(e) => setGroupByType(e.target.checked)}
                className="min-h-11 min-w-11 rounded border-slate-600 bg-slate-900 text-blue-500 focus:ring-blue-400/60"
              />
              Group by type
            </label>

            <button
              onClick={() => setPaused((p) => !p)}
              aria-pressed={paused}
              aria-label={paused ? "Resume live feed" : "Pause live feed"}
              className={clsx(
                "flex min-h-11 items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60",
                paused
                  ? "bg-amber-500/15 text-amber-300 border border-amber-500/40 hover:bg-amber-500/25"
                  : "bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 hover:text-white",
              )}
            >
              {paused ? <Play size={12} /> : <Pause size={12} />}
              {paused ? "Resume" : "Pause"}
            </button>

            <button
              onClick={() => {
                entriesRef.current = [];
                setEntries([]);
              }}
              aria-label="Clear message history"
              title="Clear history"
              className="min-h-11 min-w-11 rounded-lg border border-slate-700 text-slate-500 hover:text-slate-100 hover:border-slate-500 hover:bg-slate-800 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60"
            >
              <Trash2 size={13} />
            </button>
          </>
        }
      />

      {loadError && (
        <ErrorBanner
          tone={stale ? "warning" : "error"}
          title={stale ? "Showing last known stream data" : "Stream unavailable"}
          action={(
            <button type="button" onClick={() => connect(true)} className="min-h-11 px-3 rounded border border-current text-xs font-medium hover:bg-white/10">
              Retry
            </button>
          )}
        >
          {stale ? `${loadError} The latest stream refresh failed; retained messages remain visible.` : loadError}
        </ErrorBanner>
      )}

      {/* Type filter chips */}
      {availableTypes.length > 0 && (
        <section role="region" aria-label="Message type filters">
          <div
            className="flex flex-wrap gap-1.5 items-center"
            role="toolbar"
            aria-label="Filter messages by type"
          >
            <span className="text-xxs uppercase tracking-wider text-slate-500 font-semibold mr-1">
              Type:
            </span>
            <FilterChip
              active={typeFilter === "all"}
              onClick={() => setTypeFilter("all")}
              count={filteredEntries.length}
              activeTone="blue"
              className="min-h-11"
            >
              All
            </FilterChip>
            {availableTypes.map(({ type, count }) => (
              <FilterChip
                key={type}
                active={typeFilter === type}
                onClick={() => setTypeFilter(type)}
                count={count}
                className="min-h-11"
              >
                {type}
              </FilterChip>
            ))}
          </div>
        </section>
      )}

      {/* Feed */}
      <div
        className="dashboard-surface flex-1 overflow-y-auto font-mono"
        role="region"
        aria-label="Live message feed"
        aria-busy={!connected && !loadError}
      >
        {entries.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="radio"
              title={
                connected
                  ? `No recent messages on stream:${teamId}`
                  : "Connecting to stream…"
              }
              description="The live connection is open. New team messages will appear here immediately, and recent history is loaded when Redis has retained entries."
            />
          </div>
        ) : filteredEntries.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="inbox"
              title="No messages match the current filters"
              description="Try clearing the search query or selecting a different message type."
              action={
                <button
                  type="button"
                  onClick={() => {
                    setQuery("");
                    setTypeFilter("all");
                  }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded-lg transition-colors"
                >
                  Clear filters
                </button>
              }
            />
          </div>
        ) : (
          <table className="w-full text-xs">
            <caption className="sr-only">Agent message stream</caption>
            <thead className="sticky top-0 bg-slate-950/80 backdrop-blur border-b border-slate-800">
              <tr>
                <th
                  className="text-left px-3 py-2 text-slate-500 font-semibold w-44"
                  scope="col"
                >
                  Time
                </th>
                <th
                  className="text-left px-3 py-2 text-slate-500 font-semibold w-32"
                  scope="col"
                >
                  Type
                </th>
                <th
                  className="text-left px-3 py-2 text-slate-500 font-semibold w-36 hidden md:table-cell"
                  scope="col"
                >
                  Sender
                </th>
                <th
                  className="text-left px-3 py-2 text-slate-500 font-semibold w-28 hidden lg:table-cell"
                  scope="col"
                >
                  Project
                </th>
                <th
                  className="text-left px-3 py-2 text-slate-500 font-semibold"
                  scope="col"
                >
                  Payload
                </th>
                <th className="px-2 py-2 w-12" scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {groupedEntries
                ? groupedEntries.map(([type, group]) => (
                    <React.Fragment key={`group-${type}`}>
                      <tr className="bg-slate-900/60">
                        <td
                          colSpan={6}
                          className="px-3 py-1.5 text-xxs uppercase tracking-wider text-slate-400 font-semibold border-y border-slate-800"
                        >
                          <span className="inline-flex items-center gap-2">
                            <span
                              className={clsx(
                                "inline-block w-2 h-2 rounded-sm",
                                msgTypeColor(type),
                              )}
                              aria-hidden
                            />
                            {type}
                            <span className="text-slate-600 font-normal normal-case">
                              ({group.length} message
                              {group.length === 1 ? "" : "s"})
                            </span>
                          </span>
                        </td>
                      </tr>
                      {group.map(({ entry, index }) =>
                        renderEntryRow(entry, index),
                      )}
                    </React.Fragment>
                  ))
                : filteredEntries.map((entry, index) =>
                    renderEntryRow(entry, index),
                  )}
            </tbody>
          </table>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Footer */}
      <section
        className="dashboard-toolbar text-xxs text-slate-500 flex flex-wrap items-center justify-between gap-2"
        aria-label="Stream status"
        aria-live="polite"
      >
        <span>
          {filteredEntries.length === entries.length
            ? `${entries.length} message${entries.length === 1 ? "" : "s"}`
            : `${filteredEntries.length} of ${entries.length} messages`}
          {paused ? " — PAUSED" : ""}
        </span>
        <span className="text-slate-600">
          Capped at 500 messages · auto-scroll {paused ? "off" : "on"}
        </span>
      </section>
    </main>
  );
}
