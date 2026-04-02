"use client";

import { useState, useEffect, useRef } from "react";
import { clsx } from "clsx";
import { format } from "date-fns";
import { Pause, Play, Trash2 } from "lucide-react";

interface MessageEnvelope {
  message_type: string;
  sender_id?: string;
  project_id?: string;
  payload?: Record<string, unknown>;
  timestamp?: string;
}

interface FeedEntry {
  parsed: MessageEnvelope | null;
  raw: string;
  ts: number;
  cycleKey?: string;
}

// Group messages into "think cycles" by project_id
function getTypeClass(type: string) {
  switch (type) {
    case "TOOL_CALL":   return "border-orange-700 bg-orange-950/20";
    case "TOOL_RESULT": return "border-yellow-700 bg-yellow-950/20";
    case "DIRECTIVE":   return "border-blue-700 bg-blue-950/20";
    case "REPORT":      return "border-green-700 bg-green-950/20";
    case "VETO":        return "border-red-700 bg-red-950/20";
    default:            return "border-gray-800 bg-gray-900/20";
  }
}

function TypeBadge({ type }: { type: string }) {
  const color = {
    TOOL_CALL:   "bg-orange-500",
    TOOL_RESULT: "bg-yellow-500 text-gray-900",
    DIRECTIVE:   "bg-blue-600",
    REPORT:      "bg-green-600",
    VETO:        "bg-red-600",
    HEARTBEAT:   "bg-gray-700",
  }[type] ?? "bg-gray-700";
  return (
    <span className={clsx("inline-flex px-1.5 py-0.5 rounded text-xxs font-bold text-white", color)}>
      {type}
    </span>
  );
}

export default function CeoPage() {
  const [entries, setEntries] = useState<FeedEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(false);
  pausedRef.current = paused;

  useEffect(() => {
    const es = new EventSource("/api/streams/exec_ceo");
    es.addEventListener("connected", () => setConnected(true));
    es.addEventListener("error", () => setConnected(false));
    es.onmessage = (e) => {
      if (pausedRef.current) return;
      let parsed: MessageEnvelope | null = null;
      try { parsed = JSON.parse(e.data); } catch { /* ignore */ }
      setEntries((prev) => [...prev.slice(-300), { raw: e.data, parsed, ts: Date.now() }]);
    };
    return () => es.close();
  }, []);

  useEffect(() => {
    if (!paused) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, paused]);

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-gray-800 flex items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">CEO Live Feed</h1>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={clsx("w-1.5 h-1.5 rounded-full", connected ? "bg-green-400" : "bg-gray-600")} />
            <span className="text-xs text-gray-500">
              {connected ? "stream:exec_ceo connected" : "connecting..."}
            </span>
          </div>
        </div>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setPaused((p) => !p)}
            className={clsx(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
              paused
                ? "bg-amber-600/20 text-amber-400 border border-amber-700"
                : "bg-gray-800 text-gray-400 border border-gray-700 hover:bg-gray-700"
            )}
          >
            {paused ? <Play size={12} /> : <Pause size={12} />}
            {paused ? "Resume" : "Pause"}
          </button>
          <button
            onClick={() => setEntries([])}
            className="p-1.5 rounded-lg border border-gray-700 text-gray-500 hover:text-gray-300"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {entries.length === 0 ? (
          <div className="text-center text-gray-600 text-sm py-16">
            Waiting for CEO agent messages...
          </div>
        ) : (
          entries.map((entry, i) => {
            const type = entry.parsed?.message_type ?? "UNKNOWN";
            return (
              <div
                key={i}
                className={clsx(
                  "border rounded-lg p-3 cursor-pointer transition-colors hover:border-gray-600",
                  getTypeClass(type)
                )}
                onClick={() => setExpanded(expanded === i ? null : i)}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <TypeBadge type={type} />
                  <span className="text-xs text-gray-600 font-mono">
                    {format(entry.ts, "HH:mm:ss.SSS")}
                  </span>
                  {entry.parsed?.project_id && (
                    <span className="text-xxs text-gray-500 font-mono bg-gray-800 px-1.5 py-0.5 rounded">
                      {entry.parsed.project_id.slice(0, 8)}
                    </span>
                  )}
                  {entry.parsed?.sender_id && (
                    <span className="text-xs text-gray-500">{entry.parsed.sender_id}</span>
                  )}
                </div>

                {/* Tool call / result summary */}
                {type === "TOOL_CALL" && entry.parsed?.payload && (
                  <div className="mt-1.5 text-xs text-orange-300">
                    <span className="font-medium">
                      {(entry.parsed.payload as { tool_name?: string }).tool_name ?? "tool"}
                    </span>
                    {(entry.parsed.payload as { kwargs?: Record<string, unknown> }).kwargs && (
                      <span className="text-gray-500 ml-2">
                        {JSON.stringify((entry.parsed.payload as { kwargs?: Record<string, unknown> }).kwargs).slice(0, 60)}
                      </span>
                    )}
                  </div>
                )}
                {type === "TOOL_RESULT" && entry.parsed?.payload && (
                  <div className="mt-1.5 text-xs text-yellow-300 truncate">
                    {JSON.stringify((entry.parsed.payload as { result?: Record<string, unknown> }).result ?? entry.parsed.payload).slice(0, 100)}
                  </div>
                )}
                {(type === "DIRECTIVE" || type === "REPORT") && entry.parsed?.payload && (
                  <div className="mt-1.5 text-xs text-gray-400 truncate">
                    {JSON.stringify(entry.parsed.payload).slice(0, 120)}
                  </div>
                )}

                {/* Expanded JSON */}
                {expanded === i && (
                  <pre className="mt-2 text-xxs text-gray-400 bg-gray-950 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                    {JSON.stringify(entry.parsed ?? entry.raw, null, 2)}
                  </pre>
                )}
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>

      <div className="px-4 py-2 border-t border-gray-800 text-xxs text-gray-600">
        {entries.length} messages buffered{paused ? " — PAUSED" : ""}
      </div>
    </div>
  );
}
