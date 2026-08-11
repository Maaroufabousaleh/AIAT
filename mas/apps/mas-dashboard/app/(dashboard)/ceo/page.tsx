"use client";

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { clsx } from "clsx";
import {
  Brain,
  Check,
  Copy,
  Pause,
  Play,
  RefreshCw,
  Search,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { PageHeader } from "@/components/ui/PageHeader";
import { KpiCard } from "@/components/ui/KpiCard";
import { FilterChip } from "@/components/ui/FilterChips";
import { formatInTz } from "@/lib/datetime";
import {
  parseFirstTimestamp,
  entryFromRaw,
  getTypeClass,
  getTypeAccent,
  getTypeBadgeClass,
  getChipToneForType,
  KNOWN_TYPES,
  type FeedEntry,
  type MessageEnvelope,
  type RecentStreamEntry,
  type KnownType,
  TypeBadge,
} from "@/lib/ceo-feed";

export default function CeoPage() {
  const [entries, setEntries] = useState<FeedEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [activeType, setActiveType] = useState<KnownType>("ALL");
  const [groupByCycle, setGroupByCycle] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  // Message composer state
  const [composerText, setComposerText] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(false);
  const entriesRef = useRef<FeedEntry[]>([]);
  const esRef = useRef<EventSource | null>(null);
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

    fetch("/api/streams/exec_ceo?history=1&limit=50", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as { entries?: RecentStreamEntry[] };
      })
      .then((data) => {
        if (!isCurrent() || !data.entries) return;
        const history = data.entries.map((entry) => entryFromRaw(entry.envelope));
        const liveEntries = entriesRef.current;
        const liveRaw = new Set(liveEntries.map((entry) => entry.raw));
        const next = [...history.filter((entry) => !liveRaw.has(entry.raw)), ...liveEntries].slice(-300);
        entriesRef.current = next;
        setEntries(next);
        if (!streamFailedRef.current) setLoadError(null);
      })
      .catch((cause: unknown) => {
        if (!isCurrent()) return;
        reportError(cause instanceof Error ? `CEO history unavailable: ${cause.message}` : "CEO history unavailable");
      });

    const es = new EventSource("/api/streams/exec_ceo");
    esRef.current = es;
    es.addEventListener("connected", () => {
      if (!isCurrent()) return;
      setConnected(true);
    });
    es.addEventListener("error", (event) => {
      const raw = (event as MessageEvent<string>).data;
      let message = "CEO stream disconnected.";
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
        const next = [...prev, entryFromRaw(e.data)].slice(-300);
        entriesRef.current = next;
        return next;
      });
    };
  }, []);

  useEffect(() => {
    connect(false);
    return () => {
      connectionIdRef.current += 1;
      esRef.current?.close();
      esRef.current = null;
    };
  }, [connect]);

  // Auto-scroll to bottom while the live feed is unpaused.
  useEffect(() => {
    if (!paused) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, paused]);

  // Derive the message type for an entry, falling back to "UNKNOWN" when the
  // envelope is missing or malformed.
  const getType = useCallback((entry: FeedEntry): KnownType => {
    if (entry.outbound) return "OUTBOUND";
    const t = entry.parsed?.message_type ?? entry.parsed?.msg_type ?? "UNKNOWN";
    return (KNOWN_TYPES as string[]).includes(t) ? (t as KnownType) : "UNKNOWN";
  }, []);

  // Counts per message type — used by the filter chip badges and the KPI row.
  const typeCounts = useMemo(() => {
    const counts: Record<KnownType, number> = {
      ALL: entries.length,
      TOOL_CALL: 0,
      TOOL_RESULT: 0,
      DIRECTIVE: 0,
      REPORT: 0,
      RESPONSE: 0,
      VETO: 0,
      HEARTBEAT: 0,
      PROGRESS: 0,
      OUTBOUND: 0,
      UNKNOWN: 0,
    };
    for (const entry of entries) counts[getType(entry)]++;
    return counts;
  }, [entries, getType]);

  // Apply type filter + free-text search. Search matches against the parsed
  // envelope (type, sender, project, payload) and the raw JSON string as a
  // catch-all.
  const filteredEntries = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (activeType === "ALL" && !needle) return entries;
    return entries.filter((entry) => {
      if (activeType !== "ALL" && getType(entry) !== activeType) return false;
      if (!needle) return true;
      if (entry.raw.toLowerCase().includes(needle)) return true;
      const parsed = entry.parsed;
      if (!parsed) return false;
      if ((parsed.message_type ?? "").toLowerCase().includes(needle))
        return true;
      if ((parsed.sender_id ?? "").toLowerCase().includes(needle)) return true;
      if ((parsed.project_id ?? "").toLowerCase().includes(needle)) return true;
      if (
        parsed.payload &&
        JSON.stringify(parsed.payload).toLowerCase().includes(needle)
      )
        return true;
      return false;
    });
  }, [entries, activeType, search, getType]);

  // Group filtered entries by think cycle (project_id). When grouping is
  // disabled we render a flat list, preserving the original entry index so
  // expansion / copy-raw state is unaffected.
  interface CycleGroup {
    cycleKey: string;
    label: string;
    indices: number[];
  }
  const groups = useMemo<CycleGroup[]>(() => {
    if (!groupByCycle) {
      return [
        {
          cycleKey: "__flat__",
          label: "Live feed",
          indices: filteredEntries.map((e) => entries.indexOf(e)),
        },
      ];
    }
    const order: string[] = [];
    const map = new Map<string, number[]>();
    filteredEntries.forEach((entry) => {
      const originalIndex = entries.indexOf(entry);
      const projectId = entry.parsed?.project_id;
      const key = projectId ?? "__no_project__";
      if (!map.has(key)) {
        map.set(key, []);
        order.push(key);
      }
      map.get(key)!.push(originalIndex);
    });
    return order.map((key) => ({
      cycleKey: key,
      label:
        key === "__no_project__" ? "No project" : `Project ${key.slice(0, 8)}`,
      indices: map.get(key)!,
    }));
  }, [filteredEntries, entries, groupByCycle]);

  // Copy the raw envelope JSON to the clipboard.
  const handleCopy = useCallback(async (raw: string, index: number) => {
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(raw);
      } else {
        const ta = document.createElement("textarea");
        ta.value = raw;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopiedIndex(index);
      window.setTimeout(() => {
        setCopiedIndex((current) => (current === index ? null : current));
      }, 1500);
    } catch {
      // Silent: copy failures are not fatal; user can still expand + select.
    }
  }, []);

  // Send a message to the CEO.
  const handleSend = useCallback(async () => {
    const text = composerText.trim();
    if (!text || sending) return;
    setSending(true);
    setSendError(null);
    try {
      const res = await fetch("/api/ceo/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Unknown error" }));
        setSendError(err.error ?? `HTTP ${res.status}`);
      } else {
        // Add the outbound message to the local feed so it appears immediately.
        const now = Date.now();
        const outboundEntry: FeedEntry = {
          parsed: {
            message_type: "OUTBOUND",
            sender_id: "you",
            payload: { instruction: text },
            timestamp: new Date().toISOString(),
          },
          raw: JSON.stringify({
            message_type: "OUTBOUND",
            sender_id: "you",
            payload: { instruction: text },
          }),
          ts: now,
          outbound: true,
        };
        setEntries((prev) => {
          const next = [...prev.slice(-299), outboundEntry];
          entriesRef.current = next;
          return next;
        });
        setComposerText("");
      }
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "Network error");
    } finally {
      setSending(false);
    }
  }, [composerText, sending]);

  // Handle Enter+Shift for newlines, Enter alone to send.
  const handleComposerKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div className="dashboard-page flex flex-col h-full">
      <PageHeader
        icon="brain"
        title="CEO Live Feed"
        description={
          <span className="flex items-center gap-2 flex-wrap">
            <span
              className={clsx(
                "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xxs font-semibold border",
                connected
                  ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                  : "bg-slate-800/60 text-slate-400 border-slate-700",
              )}
              aria-live="polite"
              aria-label={
                connected
                  ? "Stream connected"
                  : loadError
                    ? "Stream disconnected"
                    : "Stream connecting"
              }
            >
              <span
                className={clsx(
                  "w-1.5 h-1.5 rounded-full",
                  connected ? "bg-emerald-400 animate-pulse" : "bg-slate-500",
                )}
              />
              {connected
                ? "stream:exec_ceo connected"
                : loadError
                  ? "stream disconnected"
                  : "connecting..."}
            </span>
            <span className="text-slate-500">
              {entries.length} buffered · {filteredEntries.length} shown
              {paused ? " · PAUSED" : ""}
            </span>
          </span>
        }
        actions={
          <>
            <button
              type="button"
              onClick={() => setGroupByCycle((g) => !g)}
              aria-pressed={groupByCycle}
              aria-label="Group entries by think cycle"
              className={clsx(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                groupByCycle
                  ? "bg-indigo-500/15 text-indigo-200 border-indigo-500/40 hover:bg-indigo-500/25"
                  : "bg-slate-900/60 text-slate-400 border-slate-700 hover:bg-slate-800 hover:text-slate-200",
              )}
            >
              <Brain size={12} />
              {groupByCycle ? "Grouped by cycle" : "Flat view"}
            </button>
            <button
              type="button"
              onClick={() => setPaused((p) => !p)}
              aria-pressed={paused}
              aria-label={paused ? "Resume live feed" : "Pause live feed"}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                paused
                  ? "bg-amber-500/15 text-amber-200 border-amber-500/40 hover:bg-amber-500/25"
                  : "bg-slate-900/60 text-slate-300 border-slate-700 hover:bg-slate-800 hover:text-slate-100",
              )}
            >
              {paused ? <Play size={12} /> : <Pause size={12} />}
              {paused ? "Resume" : "Pause"}
            </button>
            <button
              type="button"
              onClick={() => {
                entriesRef.current = [];
                setEntries([]);
              }}
              aria-label="Clear buffered messages"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-900/60 text-slate-400 hover:bg-slate-800 hover:text-rose-300 hover:border-rose-500/40 transition-colors"
            >
              <Trash2 size={12} />
              Clear
            </button>
            <button
              type="button"
              onClick={() => connect(true)}
              aria-label="Reconnect CEO feed"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-900/60 text-slate-400 hover:bg-slate-800 hover:text-slate-100 transition-colors"
            >
              <RefreshCw size={12} />
              Reconnect
            </button>
          </>
        }
      />

      {loadError && (
        <div className="mx-4 mt-3">
          <ErrorBanner
            tone={stale ? "warning" : "error"}
            title={stale ? "Showing last known CEO feed" : "CEO feed unavailable"}
            action={(
              <button type="button" onClick={() => connect(true)} className="rounded border border-current px-2.5 py-1 text-xs font-medium hover:bg-white/10">
                Retry
              </button>
            )}
          >
            {stale ? `${loadError} Retained messages remain visible while the feed is retried.` : loadError}
          </ErrorBanner>
        </div>
      )}

      {/* Message composer — talk directly to the CEO */}
      <div className="mx-4 mt-3 mb-1">
        <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-3 space-y-2">
          <div className="flex items-center gap-2">
            <Send
              size={13}
              className="text-violet-400 flex-shrink-0"
              aria-hidden="true"
            />
            <span className="text-xs font-semibold text-violet-200">
              Message the CEO
            </span>
          </div>
          <div className="flex gap-2">
            <textarea
              value={composerText}
              onChange={(e) => setComposerText(e.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="Send a directive, question, or request to the CEO… (Enter to send, Shift+Enter for newline)"
              rows={2}
              aria-label="Message to CEO"
              className="flex-1 rounded-lg bg-slate-950/80 border border-slate-700 text-xs text-slate-100 placeholder-slate-500 px-3 py-2 resize-none focus:border-violet-500/60 focus:outline-none transition-colors"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={!composerText.trim() || sending}
              aria-label="Send message to CEO"
              className={clsx(
                "flex-shrink-0 flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold border transition-colors",
                composerText.trim() && !sending
                  ? "bg-violet-600 hover:bg-violet-500 text-white border-violet-500/60 hover:border-violet-400"
                  : "bg-slate-800 text-slate-500 border-slate-700 cursor-not-allowed",
              )}
            >
              {sending ? (
                <span className="w-3 h-3 border border-slate-400 border-t-transparent rounded-full animate-spin" />
              ) : (
                <Send size={12} />
              )}
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
          {sendError && (
            <div className="flex items-center gap-1.5 text-xxs text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded px-2 py-1">
              <X size={11} />
              {sendError}
            </div>
          )}
        </div>
      </div>

      {/* KPI summary row — quick at-a-glance counts per message type. */}
      {entries.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 px-4 mt-2">
          <KpiCard
            label="Buffered"
            value={entries.length}
            hint={`${filteredEntries.length} match filter`}
            icon="inbox"
            tone="info"
          />
          <KpiCard
            label="Directives"
            value={typeCounts.DIRECTIVE}
            hint={`${typeCounts.REPORT} reports`}
            icon="scroll"
            tone="info"
          />
          <KpiCard
            label="Tool calls"
            value={typeCounts.TOOL_CALL}
            hint={`${typeCounts.TOOL_RESULT} results`}
            icon="wrench"
            tone="warning"
          />
          <KpiCard
            label="Vetos"
            value={typeCounts.VETO}
            hint={`${typeCounts.HEARTBEAT} heartbeats`}
            icon="alert-triangle"
            tone={typeCounts.VETO > 0 ? "negative" : "neutral"}
          />
        </div>
      )}

      {/* Search + filter chips */}
      {entries.length > 0 && (
        <div className="flex flex-col gap-2 px-4 mt-2">
          <div className="relative">
            <Search
              size={13}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none"
              aria-hidden="true"
            />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search message type, sender, project_id, payload..."
              aria-label="Search CEO feed"
              className="w-full pl-8 pr-8 py-1.5 rounded-lg bg-slate-950/60 border border-slate-800 text-xs text-slate-200 placeholder-slate-500 focus:border-blue-500/60 focus:bg-slate-900 transition-colors"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-200 transition-colors"
              >
                <X size={12} />
              </button>
            )}
          </div>
          <div
            className="flex flex-wrap gap-1.5"
            role="group"
            aria-label="Filter by message type"
          >
            {KNOWN_TYPES.map((type) => {
              if (type === "UNKNOWN" && typeCounts.UNKNOWN === 0) return null;
              return (
                <FilterChip
                  key={type}
                  active={activeType === type}
                  onClick={() => setActiveType(type)}
                  count={typeCounts[type]}
                  activeTone={getChipToneForType(type)}
                >
                  {type === "ALL" ? "All" : type.replace(/_/g, " ")}
                </FilterChip>
              );
            })}
          </div>
        </div>
      )}

      <div className="dashboard-surface flex-1 overflow-y-auto p-4 space-y-3">
        {entries.length === 0 ? (
          <div className="py-12">
            <EmptyState
              icon="radio"
              title={
                connected ? "No recent CEO activity" : "Connecting to stream…"
              }
              description={
                loadError
                  ? "The last CEO feed refresh failed. Use Retry to reconnect while retained messages remain visible."
                  : connected
                  ? "The live connection is open. CEO messages will appear here immediately, and retained Redis history is loaded when available."
                  : "The dashboard is establishing a Server-Sent Events connection to the orchestrator."
              }
            />
          </div>
        ) : filteredEntries.length === 0 ? (
          <div className="py-10">
            <EmptyState
              icon="inbox"
              title="No messages match the current filter"
              description={`Adjust the search query or clear the active message-type filter to see more results.`}
            />
          </div>
        ) : (
          groups.map((group) => (
            <section
              key={group.cycleKey}
              aria-label={group.label}
              className={clsx(
                "rounded-xl border border-slate-800/80 overflow-hidden",
                groupByCycle &&
                  group.cycleKey !== "__flat__" &&
                  "bg-slate-950/30",
              )}
            >
              {groupByCycle && group.cycleKey !== "__flat__" && (
                <header className="flex items-center gap-2 px-3 py-2 border-b border-slate-800/80 bg-slate-900/40">
                  <Brain
                    size={12}
                    className="text-indigo-300"
                    aria-hidden="true"
                  />
                  <span className="text-xs font-semibold text-slate-200 tracking-wide">
                    {group.label}
                  </span>
                  <span className="text-xxs text-slate-500 font-mono">
                    {group.indices.length} message
                    {group.indices.length === 1 ? "" : "s"}
                  </span>
                </header>
              )}
              <div
                className={clsx(
                  "p-2 space-y-1.5",
                  groupByCycle &&
                    group.cycleKey !== "__flat__" &&
                    "bg-slate-950/15",
                )}
              >
                {group.indices.map((originalIndex) => {
                  const entry = entries[originalIndex];
                  if (!entry) return null;
                  const type = getType(entry);
                  const isExpanded = expanded === originalIndex;
                  const outbound = entry.outbound ?? false;
                  return (
                    <article
                      key={originalIndex}
                      className={clsx(
                        "group border rounded-lg p-3 transition-all cursor-pointer",
                        getTypeClass(type, outbound),
                      )}
                      onClick={() =>
                        setExpanded(isExpanded ? null : originalIndex)
                      }
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setExpanded(isExpanded ? null : originalIndex);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      aria-expanded={isExpanded}
                      aria-label={`${type} message from ${entry.parsed?.sender_id ?? (outbound ? "you" : "unknown sender")}`}
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <TypeBadge type={type} outbound={outbound} />
                        <span className="text-xxs text-slate-500 font-mono tabular-nums">
                          {formatInTz(entry.ts, "HH:mm:ss.SSS")}
                        </span>
                        {entry.parsed?.project_id && (
                          <span className="text-xxs text-slate-400 font-mono bg-slate-800/70 border border-slate-700 px-1.5 py-0.5 rounded">
                            {entry.parsed.project_id.slice(0, 8)}
                          </span>
                        )}
                        {entry.parsed?.sender_id && (
                          <span
                            className={clsx(
                              "text-xs font-medium",
                              getTypeAccent(type, outbound),
                            )}
                          >
                            {outbound ? "→ you" : entry.parsed.sender_id}
                          </span>
                        )}
                        {outbound && (
                          <span className="ml-auto text-xxs text-violet-400 font-medium">
                            queued
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCopy(entry.raw, originalIndex);
                          }}
                          aria-label={
                            copiedIndex === originalIndex
                              ? "Copied raw envelope to clipboard"
                              : "Copy raw envelope to clipboard"
                          }
                          className={clsx(
                            "ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xxs font-medium border transition-colors",
                            copiedIndex === originalIndex
                              ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
                              : "bg-slate-900/60 text-slate-400 border-slate-700 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 hover:bg-slate-800 hover:text-slate-100",
                          )}
                        >
                          {copiedIndex === originalIndex ? (
                            <>
                              <Check size={11} aria-hidden="true" />
                              Copied
                            </>
                          ) : (
                            <>
                              <Copy size={11} aria-hidden="true" />
                              Copy raw
                            </>
                          )}
                        </button>
                      </div>

                      {/* Tool call / result summary */}
                      {type === "TOOL_CALL" && entry.parsed?.payload && (
                        <div className="mt-1.5 text-xs text-orange-200">
                          <span className="font-semibold">
                            {(entry.parsed.payload as { tool_name?: string })
                              .tool_name ?? "tool"}
                          </span>
                          {(
                            entry.parsed.payload as {
                              kwargs?: Record<string, unknown>;
                            }
                          ).kwargs && (
                            <span className="text-slate-500 ml-2 font-mono">
                              {JSON.stringify(
                                (
                                  entry.parsed.payload as {
                                    kwargs?: Record<string, unknown>;
                                  }
                                ).kwargs,
                              ).slice(0, 60)}
                            </span>
                          )}
                        </div>
                      )}
                      {type === "TOOL_RESULT" && entry.parsed?.payload && (
                        <div className="mt-1.5 text-xs text-amber-200 truncate">
                          {JSON.stringify(
                            (
                              entry.parsed.payload as {
                                result?: Record<string, unknown>;
                              }
                            ).result ?? entry.parsed.payload,
                          ).slice(0, 100)}
                        </div>
                      )}
                      {(type === "DIRECTIVE" ||
                        type === "REPORT" ||
                        type === "OUTBOUND") &&
                        entry.parsed?.payload && (
                          <div className="mt-1.5 text-xs text-slate-400 truncate">
                            {JSON.stringify(entry.parsed.payload).slice(0, 120)}
                          </div>
                        )}

                      {/* Expanded JSON view */}
                      {isExpanded && (
                        <pre
                          onClick={(e) => e.stopPropagation()}
                          className="mt-2 text-xxs text-slate-300 bg-slate-950/80 border border-slate-800 rounded-md p-2 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed"
                        >
                          {JSON.stringify(entry.parsed ?? entry.raw, null, 2)}
                        </pre>
                      )}
                    </article>
                  );
                })}
              </div>
            </section>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <div
        className="dashboard-toolbar flex items-center justify-between text-xxs text-slate-500"
        aria-live="polite"
      >
        <span>
          {filteredEntries.length}
          {filteredEntries.length !== entries.length &&
            ` of ${entries.length}`}{" "}
          message
          {filteredEntries.length === 1 ? "" : "s"}
          {groupByCycle &&
            filteredEntries.length > 0 &&
            " · grouped by think cycle"}
          {paused && " · PAUSED"}
        </span>
        {search && (
          <span className="text-slate-600 truncate max-w-xs">
            search: &ldquo;{search}&rdquo;
          </span>
        )}
      </div>
    </div>
  );
}
