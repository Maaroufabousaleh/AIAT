"use client";

import { useState, useEffect, useRef } from "react";
import { clsx } from "clsx";
import { CONTAINER_NAMES } from "@/lib/constants";
import { format } from "date-fns";
import { Pause, Play, Trash2, Plus, X } from "lucide-react";

interface LogLine {
  raw: string;
  parsed: Record<string, unknown> | null;
  ts: number;
  level?: string;
}

const LEVEL_COLORS: Record<string, string> = {
  debug:    "text-gray-500",
  info:     "text-blue-400",
  warning:  "text-amber-400",
  error:    "text-red-400",
  critical: "text-red-300 font-bold",
};

function parseLine(raw: string): LogLine {
  let parsed: Record<string, unknown> | null = null;
  let level: string | undefined;
  const trimmed = raw.trim();
  if (trimmed.startsWith("{")) {
    try {
      parsed = JSON.parse(trimmed);
      level = (parsed?.level as string)?.toLowerCase();
    } catch { /* not JSON */ }
  }
  return { raw, parsed, ts: Date.now(), level };
}

function LogLineRow({ line, filter }: { line: LogLine; filter: string }) {
  if (filter && !line.raw.toLowerCase().includes(filter.toLowerCase())) return null;
  const lvlClass = LEVEL_COLORS[line.level ?? "info"] ?? "text-gray-400";

  if (line.parsed) {
    return (
      <div className={clsx("flex gap-2 text-xxs font-mono px-3 py-0.5 hover:bg-gray-800/40", lvlClass)}>
        <span className="text-gray-700 flex-shrink-0">
          {format(line.ts, "HH:mm:ss.SSS")}
        </span>
        <span className="flex-shrink-0 w-16 uppercase truncate">
          {(line.parsed.level as string) ?? "—"}
        </span>
        <span className="flex-shrink-0 w-28 text-gray-600 truncate">
          {(line.parsed.logger as string) ?? (line.parsed.service as string) ?? ""}
        </span>
        <span className="flex-1 truncate">
          {(line.parsed.event as string) ?? line.raw.slice(0, 120)}
        </span>
        {Boolean(line.parsed.project_id) && (
          <span className="text-gray-600 flex-shrink-0 hidden lg:inline">
            {String(line.parsed.project_id).slice(0, 8)}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="flex gap-2 text-xxs font-mono px-3 py-0.5 hover:bg-gray-800/40 text-gray-500">
      <span className="text-gray-700 flex-shrink-0">{format(line.ts, "HH:mm:ss.SSS")}</span>
      <span className="flex-1 truncate">{line.raw.slice(0, 200)}</span>
    </div>
  );
}

function ContainerPanel({ container, onRemove }: { container: string; onRemove: () => void }) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("");
  const [levelFilter, setLevelFilter] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(false);
  pausedRef.current = paused;

  useEffect(() => {
    const es = new EventSource(`/api/logs/stream?container=${encodeURIComponent(container)}&tail=200`);
    es.onmessage = (e) => {
      if (pausedRef.current) return;
      try {
        const { line } = JSON.parse(e.data);
        const parsed = parseLine(line);
        setLines((prev) => [...prev.slice(-1999), parsed]);
      } catch { /* ignore */ }
    };
    return () => es.close();
  }, [container]);

  useEffect(() => {
    if (!paused) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines, paused]);

  const displayed = lines.filter((l) => {
    if (levelFilter && l.level !== levelFilter) return false;
    if (filter && !l.raw.toLowerCase().includes(filter.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="flex flex-col bg-gray-950 border border-gray-800 rounded-xl overflow-hidden h-full min-h-0">
      {/* Panel header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900 flex-shrink-0">
        <span className="text-xs font-medium text-gray-300 truncate flex-1">{container}</span>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="filter..."
          className="w-24 bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-xxs
                     text-gray-300 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <select
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-1 py-0.5 text-xxs text-gray-300
                     focus:outline-none"
        >
          <option value="">all</option>
          <option value="debug">debug</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </select>
        <button
          onClick={() => setPaused((p) => !p)}
          className="text-gray-500 hover:text-gray-300"
        >
          {paused ? <Play size={12} /> : <Pause size={12} />}
        </button>
        <button onClick={() => setLines([])} className="text-gray-500 hover:text-gray-300">
          <Trash2 size={12} />
        </button>
        <button onClick={onRemove} className="text-gray-600 hover:text-red-400">
          <X size={12} />
        </button>
      </div>

      {/* Log lines */}
      <div className="flex-1 overflow-y-auto">
        {displayed.length === 0 ? (
          <div className="p-4 text-center text-gray-600 text-xxs">
            Connecting to {container}...
          </div>
        ) : (
          displayed.map((line, i) => (
            <LogLineRow key={i} line={line} filter="" />
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <div className="px-3 py-1 border-t border-gray-900 text-xxs text-gray-700 flex-shrink-0">
        {lines.length} lines{paused ? " · PAUSED" : ""}
      </div>
    </div>
  );
}

export default function LogsPage() {
  const [containers, setContainers] = useState<string[]>(["mas-team-exec-ceo"]);
  const [addOpen, setAddOpen] = useState(false);

  function addContainer(c: string) {
    if (!containers.includes(c)) setContainers((prev) => [...prev, c]);
    setAddOpen(false);
  }

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-gray-800 flex items-center gap-3 flex-shrink-0">
        <h1 className="text-lg font-semibold text-white">Log Viewer</h1>
        <button
          onClick={() => setAddOpen(true)}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium
                     bg-blue-600/20 text-blue-400 border border-blue-700 hover:bg-blue-600/40
                     rounded-lg transition-colors"
        >
          <Plus size={12} />
          Add Container
        </button>
      </div>

      {/* Container grid */}
      <div
        className={clsx(
          "flex-1 grid gap-2 p-4 min-h-0",
          containers.length === 1 ? "grid-cols-1" :
          containers.length === 2 ? "grid-cols-2" :
          containers.length <= 4 ? "grid-cols-2" : "grid-cols-2"
        )}
      >
        {containers.map((c) => (
          <ContainerPanel
            key={c}
            container={c}
            onRemove={() => setContainers((prev) => prev.filter((x) => x !== c))}
          />
        ))}
      </div>

      {/* Add container modal */}
      {addOpen && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 w-80">
            <h2 className="text-sm font-semibold text-white mb-3">Add Container</h2>
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {CONTAINER_NAMES.map((c) => (
                <button
                  key={c}
                  onClick={() => addContainer(c)}
                  disabled={containers.includes(c)}
                  className="w-full text-left px-3 py-2 rounded-lg text-xs text-gray-300
                             hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed
                             transition-colors"
                >
                  {c}
                </button>
              ))}
            </div>
            <button
              onClick={() => setAddOpen(false)}
              className="mt-3 w-full px-3 py-2 border border-gray-700 rounded-lg text-xs
                         text-gray-400 hover:text-gray-100 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
