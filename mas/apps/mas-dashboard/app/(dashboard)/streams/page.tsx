"use client";

import React, { useState, useEffect, useRef } from "react";
import { clsx } from "clsx";
import { TEAM_STREAMS, MSG_TYPE_COLORS, type TeamStreamId } from "@/lib/constants";
import { format } from "date-fns";
import { Pause, Play, Trash2 } from "lucide-react";

interface MessageEnvelope {
  id?: string;
  message_type: string;
  sender_id?: string;
  sender_role?: string;
  recipient_id?: string;
  project_id?: string;
  payload?: unknown;
  timestamp?: string;
  trace_id?: string;
}

interface FeedEntry {
  raw: string;
  parsed: MessageEnvelope | null;
  ts: number;
}

export default function StreamsPage() {
  const [teamId, setTeamId] = useState<TeamStreamId>("exec_ceo");
  const [entries, setEntries] = useState<FeedEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(false);
  const esRef = useRef<EventSource | null>(null);

  pausedRef.current = paused;

  useEffect(() => {
    setEntries([]);
    setConnected(false);

    const es = new EventSource(`/api/streams/${teamId}`);
    esRef.current = es;

    es.addEventListener("connected", () => setConnected(true));
    es.addEventListener("error", () => setConnected(false));

    es.onmessage = (e) => {
      if (pausedRef.current) return;
      let parsed: MessageEnvelope | null = null;
      try { parsed = JSON.parse(e.data); } catch { /* non-JSON */ }
      setEntries((prev) => {
        const next = [...prev, { raw: e.data, parsed, ts: Date.now() }];
        return next.slice(-500); // cap at 500
      });
    };

    return () => es.close();
  }, [teamId]);

  // Auto-scroll
  useEffect(() => {
    if (!paused) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [entries, paused]);

  function msgTypeColor(type: string) {
    return MSG_TYPE_COLORS[type] ?? "bg-gray-700";
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-gray-800 flex items-center gap-3 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold text-white">Agent Stream Monitor</h1>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={clsx("w-1.5 h-1.5 rounded-full", connected ? "bg-green-400" : "bg-gray-600")} />
            <span className="text-xs text-gray-500">{connected ? "connected" : "connecting..."}</span>
          </div>
        </div>

        <select
          value={teamId}
          onChange={(e) => setTeamId(e.target.value as TeamStreamId)}
          className="ml-auto bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5
                     text-sm text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {TEAM_STREAMS.map((t) => (
            <option key={t.id} value={t.id}>
              {t.role} — {t.label}
            </option>
          ))}
        </select>

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

      {/* Feed */}
      <div className="flex-1 overflow-y-auto font-mono">
        {entries.length === 0 ? (
          <div className="p-8 text-center text-gray-600 text-sm">
            Waiting for messages on <span className="text-gray-400">stream:{teamId}</span>...
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-900 border-b border-gray-800">
              <tr>
                <th className="text-left px-3 py-2 text-gray-600 font-medium w-24">Time</th>
                <th className="text-left px-3 py-2 text-gray-600 font-medium w-32">Type</th>
                <th className="text-left px-3 py-2 text-gray-600 font-medium w-36 hidden md:table-cell">Sender</th>
                <th className="text-left px-3 py-2 text-gray-600 font-medium w-28 hidden lg:table-cell">Project</th>
                <th className="text-left px-3 py-2 text-gray-600 font-medium">Payload</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, i) => (
                <React.Fragment key={i}>
                  <tr
                    className="border-b border-gray-900 hover:bg-gray-800/40 cursor-pointer"
                    onClick={() => setExpanded(expanded === i ? null : i)}
                  >
                    <td className="px-3 py-1.5 text-gray-600">
                      {format(entry.ts, "HH:mm:ss.SSS")}
                    </td>
                    <td className="px-3 py-1.5">
                      {entry.parsed?.message_type ? (
                        <span className={clsx(
                          "inline-flex px-1.5 py-0.5 rounded text-xxs font-medium text-white",
                          msgTypeColor(entry.parsed.message_type)
                        )}>
                          {entry.parsed.message_type}
                        </span>
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-gray-500 truncate max-w-xs hidden md:table-cell">
                      {entry.parsed?.sender_id ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 text-gray-500 truncate hidden lg:table-cell">
                      {entry.parsed?.project_id?.slice(0, 8) ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 text-gray-500 truncate max-w-sm">
                      {Boolean(entry.parsed?.payload)
                        ? JSON.stringify(entry.parsed!.payload).slice(0, 80)
                        : entry.raw.slice(0, 80)}
                    </td>
                  </tr>
                  {expanded === i && (
                    <tr key={`${i}-expanded`} className="bg-gray-950">
                      <td colSpan={5} className="px-3 py-2">
                        <pre className="text-xxs text-gray-400 overflow-x-auto whitespace-pre-wrap">
                          {JSON.stringify(entry.parsed ?? entry.raw, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-gray-800 text-xxs text-gray-600">
        {entries.length} messages{paused ? " — PAUSED" : ""}
      </div>
    </div>
  );
}
