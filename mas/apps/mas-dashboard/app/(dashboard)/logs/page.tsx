"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { clsx } from "clsx";
import { CONTAINER_NAMES } from "@/lib/constants";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { FilterChip } from "@/components/ui/FilterChips";
import {
  Check,
  Copy,
  Download,
  Eraser,
  Play,
  Search,
  ScrollText,
  Square,
} from "lucide-react";

type LogLine = {
  id: number;
  text: string;
  ts: string;
};

type TraceIncident = {
  schema_version: string;
  trace_id: string;
  generated_at: string | null;
  status: "clear" | "attention" | "not_found";
  severity: "info" | "warning" | "critical";
  coverage_status: "complete" | "partial" | "empty";
  item_count: number;
  finding_count: number;
  affected_sources: string[];
  notice_codes: string[];
};

/** Log severity buckets we surface in the UI. "all" means "no filter". */
type LogLevel = "all" | "error" | "warn" | "info" | "debug";

const MAX_LINES = 2000;

const LEVEL_LEGEND: { key: Exclude<LogLevel, "all">; label: string; swatch: string; match: (t: string) => boolean }[] = [
  { key: "error", label: "Error / Critical / Fatal", swatch: "bg-rose-500", match: (t) => t.includes("error") || t.includes("critical") || t.includes("fatal") },
  { key: "warn",  label: "Warn",                       swatch: "bg-amber-400", match: (t) => t.includes("warn") },
  { key: "info",  label: "Info",                       swatch: "bg-emerald-400", match: () => true }, // info is the default bucket
  { key: "debug", label: "Debug",                      swatch: "bg-slate-500", match: (t) => t.includes("debug") },
];

/**
 * Container Logs viewer. Streams container output from `/api/logs/[container]`
 * and lets the operator tail, follow, search, filter by level, copy, and
 * download the recent buffer.
 */
export default function LogsPage() {
  const [traceId, setTraceId] = useState("");
  const [container, setContainer] = useState<string>(CONTAINER_NAMES[0]);
  const [tail, setTail] = useState<number>(200);
  const [follow, setFollow] = useState<boolean>(false);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [hasReadContext, setHasReadContext] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const counterRef = useRef(0);
  const pendingBufferResetRef = useRef(false);
  const streamHadDataRef = useRef(false);
  const linesRef = useRef<LogLine[]>([]);
  const hasReadContextRef = useRef(false);
  const streamGenerationRef = useRef(0);
  const incidentRef = useRef<TraceIncident | null>(null);

  // UI-only filter state
  const [search, setSearch] = useState("");
  const [level, setLevel] = useState<LogLevel>("all");
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  const [incident, setIncident] = useState<TraceIncident | null>(null);
  const [incidentState, setIncidentState] = useState<"idle" | "loading" | "loaded" | "stale" | "unavailable" | "denied">("idle");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setTraceId(params.get("trace_id")?.trim() ?? "");
  }, []);

  useEffect(() => {
    if (!traceId) {
      incidentRef.current = null;
      setIncident(null);
      setIncidentState("idle");
      return;
    }
    let active = true;
    const controller = new AbortController();
    setIncidentState("loading");
    void fetch(`/api/observability/incidents/${encodeURIComponent(traceId)}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (response.status === 401 || response.status === 403) {
          setIncidentState("denied");
          return null;
        }
        if (!response.ok) throw new Error("trace incident unavailable");
        return response.json() as Promise<TraceIncident>;
      })
      .then((value) => {
        if (!active || !value) return;
        incidentRef.current = value;
        setIncident(value);
        setIncidentState("loaded");
      })
      .catch(() => {
        if (!active) return;
        setIncidentState(incidentRef.current ? "stale" : "unavailable");
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [traceId]);

  const stopStream = () => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setStreaming(false);
  };

  const handleAccessDenied = () => {
    streamGenerationRef.current += 1;
    stopStream();
    const hadReadContext = hasReadContextRef.current || linesRef.current.length > 0;
    setAccessDenied(true);
    setHasReadContext(hadReadContext);
    setError(null);
    setStale(false);
    setSearch("");
    setLevel("all");
  };

  const startStream = () => {
    if (accessDenied) return;
    const hadRetainedData = lines.length > 0;
    const generation = ++streamGenerationRef.current;
    stopStream();
    setError(null);
    setStale(false);
    counterRef.current = 0;
    pendingBufferResetRef.current = true;
    streamHadDataRef.current = hadRetainedData;

    const url = `/api/logs/${encodeURIComponent(container)}?tail=${tail}&follow=${follow}`;
    const es = new EventSource(url);
    esRef.current = es;
    setStreaming(true);

    es.onmessage = (ev) => {
      if (streamGenerationRef.current !== generation) return;
      const text: string = ev.data;
      // Check for error payload
      try {
        const parsed = JSON.parse(text);
        if (parsed.error) {
          if (parsed.status === 401 || parsed.status === 403 || /HTTP (401|403)\b/.test(parsed.error)) {
            handleAccessDenied();
            return;
          }
          setError(parsed.error);
          setStale(streamHadDataRef.current);
          stopStream();
          return;
        }
      } catch {
        // Not JSON — normal log line
      }

      // Parse optional leading docker timestamp (RFC3339)
      const tsMatch = text.match(/^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s+(.*)/);
      const ts = tsMatch ? tsMatch[1] : new Date().toISOString();
      const body = tsMatch ? tsMatch[2] : text;

      const id = ++counterRef.current;
      const nextLine = { id, text: body, ts };
      const resetBuffer = pendingBufferResetRef.current;
      pendingBufferResetRef.current = false;
      streamHadDataRef.current = true;
      hasReadContextRef.current = true;
      setHasReadContext(true);
      setError(null);
      setStale(false);
      setLines((prev) => {
        const next = resetBuffer ? [nextLine] : [...prev, nextLine];
        linesRef.current = next;
        return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
      });
    };

    es.onerror = () => {
      if (streamGenerationRef.current !== generation) return;
      if (!follow) {
        // Non-follow streams will close naturally
        stopStream();
      } else {
        setError("Stream disconnected.");
        setStale(streamHadDataRef.current);
        stopStream();
      }
    };
  };

  // Auto-scroll when following
  useEffect(() => {
    if (follow && streaming) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [lines, follow, streaming]);

  // Cleanup on unmount
  useEffect(() => () => stopStream(), []);

  /**
   * Map a log line's text to a severity bucket. The order matters — error /
   * warn are checked before the catch-all "info" bucket.
   */
  const classifyLevel = (text: string): Exclude<LogLevel, "all"> => {
    const t = text.toLowerCase();
    if (t.includes("error") || t.includes("critical") || t.includes("fatal")) return "error";
    if (t.includes("warn")) return "warn";
    if (t.includes("debug")) return "debug";
    return "info";
  };

  /** Tailwind classes for the line text — kept aligned with the legend swatches. */
  const levelClass = (text: string) => {
    const lvl = classifyLevel(text);
    if (lvl === "error") return "text-rose-300";
    if (lvl === "warn") return "text-amber-300";
    if (lvl === "debug") return "text-slate-500";
    return "text-emerald-300";
  };

  // Counts per level (over the full retained buffer — used for filter-chip badges)
  const levelCounts = useMemo(() => {
    const counts: Record<Exclude<LogLevel, "all">, number> = { error: 0, warn: 0, info: 0, debug: 0 };
    for (const l of lines) counts[classifyLevel(l.text)] += 1;
    return counts;
  }, [lines]);

  // Apply search + level filter without mutating the underlying buffer.
  const filteredLines = useMemo(() => {
    const q = search.trim().toLowerCase();
    return lines.filter((l) => {
      if (level !== "all" && classifyLevel(l.text) !== level) return false;
      if (q && !l.text.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [lines, search, level]);

  /** Serialize the currently visible (filtered) lines back to a plain-text blob. */
  const buildDownload = (): string => {
    const header = `# Container: ${container}\n# Tail: ${tail}\n# Exported: ${new Date().toISOString()}\n# Lines: ${filteredLines.length} (of ${lines.length})\n`;
    const body = filteredLines
      .map((l) => `${l.ts} ${l.text}`)
      .join("\n");
    return `${header}\n${body}\n`;
  };

  const handleCopy = async () => {
    const text = buildDownload();
    try {
      await navigator.clipboard.writeText(text);
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 1500);
    } catch {
      // Fallback for older browsers — best-effort.
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopyState("copied");
        setTimeout(() => setCopyState("idle"), 1500);
      } catch {
        /* ignore */
      } finally {
        document.body.removeChild(ta);
      }
    }
  };

  const handleDownload = () => {
    const text = buildDownload();
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${container}-${new Date().toISOString().replace(/[:.]/g, "-")}.log`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <main className="dashboard-page flex flex-col min-h-0" aria-label="Container logs">
      <PageHeader
        icon="scroll"
        title="Container Logs"
        description="Tail containers, follow live output, and keep recent lines readable."
        actions={!accessDenied ? (
          <div className="dashboard-toolbar flex flex-wrap items-center gap-2">
            {/* Container select */}
            <label className="flex items-center gap-1.5 text-xxs font-semibold uppercase tracking-wider text-slate-500">
              <span className="sr-only sm:not-sr-only">Container</span>
              <select
                aria-label="Container"
                className="min-h-11 bg-slate-950/70 text-slate-200 border border-slate-700 rounded-md px-2 py-1 text-xs hover:border-slate-500 focus:border-blue-400 transition-colors disabled:opacity-50"
                value={container}
                onChange={(e) => setContainer(e.target.value)}
                disabled={streaming}
              >
                {CONTAINER_NAMES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>

            {/* Tail lines */}
            <label className="flex items-center gap-1.5 text-xxs font-semibold uppercase tracking-wider text-slate-500">
              <span className="sr-only sm:not-sr-only">Tail</span>
              <select
                aria-label="Tail lines"
                className="min-h-11 bg-slate-950/70 text-slate-200 border border-slate-700 rounded-md px-2 py-1 text-xs hover:border-slate-500 focus:border-blue-400 transition-colors disabled:opacity-50"
                value={tail}
                onChange={(e) => setTail(Number(e.target.value))}
                disabled={streaming}
              >
                {[50, 100, 200, 500, 1000].map((n) => (
                  <option key={n} value={n}>
                    Last {n}
                  </option>
                ))}
              </select>
            </label>

            {/* Follow toggle */}
            <label className="flex min-h-11 items-center gap-1.5 text-xs text-slate-300 cursor-pointer select-none px-1.5">
              <input
                type="checkbox"
                aria-label="Follow live output"
                checked={follow}
                onChange={(e) => setFollow(e.target.checked)}
                disabled={streaming}
                className="min-h-11 min-w-11 accent-blue-500"
              />
              Follow
            </label>

            {/* Load / Stop */}
            {!streaming ? (
              <button
                type="button"
                onClick={startStream}
                className="inline-flex min-h-11 items-center gap-1.5 px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded-md text-xs font-medium shadow-sm shadow-blue-500/10 transition-colors"
              >
                <Play size={12} />
                Load
              </button>
            ) : (
              <button
                type="button"
                onClick={stopStream}
                className="inline-flex min-h-11 items-center gap-1.5 px-3 py-1 bg-rose-600 hover:bg-rose-500 text-white rounded-md text-xs font-medium shadow-sm shadow-rose-500/10 transition-colors"
              >
                <Square size={12} />
                Stop
              </button>
            )}

            {/* Clear buffer */}
            <button
              type="button"
              onClick={() => setLines([])}
              disabled={lines.length === 0}
              className="inline-flex min-h-11 items-center gap-1.5 px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-md text-xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              aria-label="Clear log buffer"
            >
              <Eraser size={12} />
              Clear
            </button>

            {/* Copy visible lines */}
            <button
              type="button"
              onClick={handleCopy}
              disabled={filteredLines.length === 0}
              className={clsx(
                "inline-flex min-h-11 items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed",
                copyState === "copied"
                  ? "bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm shadow-emerald-500/10"
                  : "bg-slate-800 hover:bg-slate-700 text-slate-200"
              )}
              aria-label="Copy visible log lines to clipboard"
            >
              {copyState === "copied" ? <Check size={12} /> : <Copy size={12} />}
              {copyState === "copied" ? "Copied" : "Copy"}
            </button>

            {/* Download */}
            <button
              type="button"
              onClick={handleDownload}
              disabled={filteredLines.length === 0}
              className="inline-flex min-h-11 items-center gap-1.5 px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-md text-xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              aria-label="Download visible log lines"
            >
              <Download size={12} />
              Download
            </button>
          </div>
        ) : undefined}
      />

      {traceId && (
        <section
          className="dashboard-surface border border-cyan-400/20 bg-cyan-400/5 px-4 py-3"
          aria-label="Trace incident summary"
          data-testid="trace-incident-summary"
          aria-busy={incidentState === "loading"}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-cyan-200">Trace incident</p>
              <p className="mt-1 break-all font-mono text-xs text-slate-400">{traceId}</p>
            </div>
            {incident && (
              <div className="flex flex-wrap gap-2 text-xs" aria-label="Trace incident status">
                <span className="rounded border border-slate-700 px-2 py-1 text-slate-200">Status: {incident.status}</span>
                <span className="rounded border border-slate-700 px-2 py-1 text-slate-200">Severity: {incident.severity}</span>
                <span className="rounded border border-slate-700 px-2 py-1 text-slate-200">Coverage: {incident.coverage_status}</span>
                <span className="rounded border border-slate-700 px-2 py-1 text-slate-200">Findings: {incident.finding_count}</span>
              </div>
            )}
          </div>
          {incidentState === "loading" && <p className="mt-2 text-xs text-slate-500" role="status">Loading bounded incident summary…</p>}
          {incidentState === "unavailable" && <p className="mt-2 text-xs text-amber-300" role="status">Incident summary is temporarily unavailable; the trace citation remains valid.</p>}
          {incidentState === "stale" && <p className="mt-2 text-xs text-amber-300" role="status">Showing the last known incident summary; refresh is temporarily unavailable.</p>}
          {incidentState === "denied" && <p className="mt-2 text-xs text-amber-300" role="status">Incident summary access is unavailable; the trace citation remains valid.</p>}
          {incident && (incident.affected_sources.length > 0 || incident.notice_codes.length > 0) && (
            <p className="mt-2 break-words text-xs text-slate-400">
              {incident.affected_sources.length > 0 ? `Affected sources: ${incident.affected_sources.join(", ")}. ` : ""}
              {incident.notice_codes.length > 0 ? `Notices: ${incident.notice_codes.join(", ")}.` : ""}
            </p>
          )}
        </section>
      )}

      {/* Search + level filter row */}
      {!accessDenied && <section className="dashboard-surface flex flex-wrap items-center gap-3 px-4 py-3" aria-label="Log filters">
        {/* Search input */}
        <div className="relative flex-1 min-w-[220px]">
          <Search
            size={14}
            aria-hidden="true"
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter log text (case-insensitive)"
            aria-label="Filter log text"
            className="w-full min-h-11 bg-slate-950/70 text-slate-200 placeholder:text-slate-500 border border-slate-700 rounded-md pl-8 pr-2 py-1.5 text-xs hover:border-slate-500 focus:border-blue-400 transition-colors"
          />
        </div>

        {/* Level filter chips */}
        <div className="flex items-center gap-1.5" role="group" aria-label="Filter by log level">
          <FilterChip<LogLevel>
            active={level === "all"}
            onClick={() => setLevel("all")}
            count={lines.length}
            activeTone="blue"
            className="min-h-11"
          >
            All
          </FilterChip>
          <FilterChip<LogLevel>
            active={level === "error"}
            onClick={() => setLevel("error")}
            count={levelCounts.error}
            activeTone="amber"
            className="min-h-11"
          >
            Error
          </FilterChip>
          <FilterChip<LogLevel>
            active={level === "warn"}
            onClick={() => setLevel("warn")}
            count={levelCounts.warn}
            activeTone="amber"
            className="min-h-11"
          >
            Warn
          </FilterChip>
          <FilterChip<LogLevel>
            active={level === "info"}
            onClick={() => setLevel("info")}
            count={levelCounts.info}
            activeTone="emerald"
            className="min-h-11"
          >
            Info
          </FilterChip>
          <FilterChip<LogLevel>
            active={level === "debug"}
            onClick={() => setLevel("debug")}
            count={levelCounts.debug}
            activeTone="gray"
            className="min-h-11"
          >
            Debug
          </FilterChip>
        </div>
      </section>}

      {/* Color legend — small, semantic, hidden on very small screens */}
      <section
        className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-xxs text-slate-500 border-b border-slate-800/60"
        aria-label="Log level color legend"
        role="region"
      >
        <span className="font-semibold uppercase tracking-wider">Legend:</span>
        {LEVEL_LEGEND.map((l) => (
          <span key={l.key} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={clsx("inline-block w-2.5 h-2.5 rounded-sm", l.swatch)}
            />
            {l.label}
          </span>
        ))}
      </section>

      {accessDenied && (
        <section
          className="dashboard-surface border border-amber-500/30 bg-amber-500/5 px-4 py-3"
          role="region"
          aria-label="Log access status"
          aria-live="polite"
        >
          <p className="text-sm font-medium text-amber-200">Log access denied</p>
          <p className="mt-1 text-xs text-amber-200/80">
            {hasReadContext
              ? "Previously loaded log lines remain visible for reference. Load, retry, filter, clear, copy, and download controls are hidden until authorization is restored."
              : "No live log data is available while authorization is unavailable. Log controls are hidden until authorization is restored."}
          </p>
        </section>
      )}

      {error && !accessDenied && (
        <ErrorBanner
          tone={stale ? "warning" : "error"}
          title={stale ? "Showing last known logs" : "Log stream error"}
          action={(
            <button type="button" onClick={startStream} className="min-h-11 px-3 rounded border border-current text-xs font-medium hover:bg-white/10">
              Retry
            </button>
          )}
        >
          {stale ? `${error} The latest log refresh failed; retained lines remain visible.` : error}
        </ErrorBanner>
      )}

      {/* Log output */}
      <div
        className="flex-1 overflow-auto rounded-xl border border-slate-800 bg-black/55 font-mono text-xs p-3 min-h-0 shadow-inner shadow-black/40"
        role="log"
        aria-live="polite"
        aria-busy={!accessDenied && streaming}
        aria-label={accessDenied ? `Last known log output for ${container}` : `Log output for ${container}`}
      >
        {lines.length === 0 && !streaming && (
          <div className="h-full flex items-center justify-center">
            {accessDenied ? (
              <EmptyState
                icon="key"
                title="No live log data is available"
                description="Authorization is required before container logs can be loaded."
                className="!border-0 !bg-transparent"
              />
            ) : (
              <EmptyState
                icon="scroll"
                title="No logs loaded"
                description="Pick a container above, choose how many lines to tail, and click Load. Toggle Follow to live-stream new entries."
                className="!border-0 !bg-transparent"
              />
            )}
          </div>
        )}
        {lines.length > 0 && filteredLines.length === 0 && (
          <div className="h-full flex items-center justify-center">
            <EmptyState
              icon="scroll"
              title="No matches"
              description={`No log lines match the current search and level filter. Buffer still holds ${lines.length} line${lines.length === 1 ? "" : "s"}.`}
              className="!border-0 !bg-transparent"
            />
          </div>
        )}
        {filteredLines.map((l) => (
          <div key={l.id} className="flex gap-2 leading-5">
            <span className="text-slate-600 shrink-0 w-[175px] truncate">{l.ts}</span>
            <span className={levelClass(l.text)}>{l.text}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <section className="text-xs text-slate-500 flex items-center gap-3 flex-wrap" aria-label="Log status">
        <span>
          Showing <span className="text-slate-300 font-medium">{filteredLines.length}</span> of{" "}
          {lines.length} line{lines.length !== 1 ? "s" : ""} (max {MAX_LINES} retained)
        </span>
        {streaming && (
          <span className="text-blue-400 animate-pulse inline-flex items-center gap-1" aria-live="polite">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-400" />
            streaming
          </span>
        )}
        {!accessDenied && (search.trim() !== "" || level !== "all") && (
          <button
            type="button"
            onClick={() => {
              setSearch("");
              setLevel("all");
            }}
            className="min-h-11 px-2 text-slate-500 hover:text-slate-300 underline-offset-2 hover:underline transition-colors"
          >
            Clear filters
          </button>
        )}
      </section>
    </main>
  );
}
