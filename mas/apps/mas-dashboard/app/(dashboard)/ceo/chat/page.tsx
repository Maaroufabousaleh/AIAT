"use client";

import { useState, useRef, useCallback } from "react";
import { clsx } from "clsx";
import { Send } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatInTz, getChatDateLabel } from "@/lib/datetime";
import {
  useCeoStream,
  KNOWN_TYPES,
  getTypeClass,
  getTypeAccent,
  payloadText,
  type FeedEntry,
  type KnownType,
} from "@/lib/ceo-feed";

const CHAT_DISPLAY_TYPES: KnownType[] = [
  "DIRECTIVE",
  "REPORT",
  "RESPONSE",
  "TOOL_RESULT",
  "VETO",
  "OUTBOUND",
  "UNKNOWN",
];

function isOperatorEntry(entry: FeedEntry): boolean {
  return entry.outbound === true || entry.parsed?.sender_id === "human_operator";
}

function isUserSentEntry(entry: FeedEntry): boolean {
  // Check if this is a user-sent message (either optimistic or from Redis)
  const parsed = entry.parsed;
  if (!parsed) return false;
  // Optimistic entry has sender_id: "you"
  if (parsed.sender_id === "you") return true;
  // Redis entry has sender_id: "human_operator" and msg_type: "TASK" with HUMAN_DIRECTIVE action
  if (parsed.sender_id === "human_operator" && parsed.msg_type === "TASK") {
    const payload = parsed.payload as Record<string, unknown> | undefined;
    if (payload?.action === "HUMAN_DIRECTIVE") return true;
  }
  return false;
}

function isCeoSentEntry(entry: FeedEntry): boolean {
  const senderId = entry.parsed?.sender_id;
  return senderId === "ceo" || senderId === "ceo_agent";
}

function entryType(entry: FeedEntry): KnownType {
  const rawType = entry.parsed?.message_type ?? entry.parsed?.msg_type ?? "UNKNOWN";
  if (isOperatorEntry(entry)) return "OUTBOUND";
  return CHAT_DISPLAY_TYPES.includes(rawType as KnownType) ? (rawType as KnownType) : "UNKNOWN";
}

function entryText(entry: FeedEntry): string {
  const payload = entry.parsed?.payload;
  if (isOperatorEntry(entry)) {
    if (payload?.instruction != null) return String(payload.instruction);
    if (payload?.message != null) return String(payload.message);
  }
  return payloadText(payload) ?? "";
}

function isChatEntry(entry: FeedEntry): boolean {
  const parsed = entry.parsed;
  if (!parsed) return false;
  const type = parsed?.message_type ?? parsed?.msg_type ?? "UNKNOWN";
  // Include operator entries (both optimistic and from Redis)
  if (isUserSentEntry(entry)) return true;
  // Include CEO responses and other chat-relevant messages
  if (isCeoSentEntry(entry)) {
    if (CHAT_DISPLAY_TYPES.includes(type as KnownType)) return true;
  }
  return false;
}

export default function CeoChatPage() {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeType, setActiveType] = useState<KnownType>("ALL");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { entries, connected: streamConnected, clear, append } = useCeoStream(
    "exec_ceo",
    isChatEntry,
    50,
  );

  const connected = streamConnected;

  const filteredEntries = entries.filter((entry) => {
    if (activeType === "ALL") return true;
    return entryType(entry) === activeType;
  });

  const typeCounts = entries.reduce<Record<KnownType, number>>((counts, entry) => {
    const type = entryType(entry);
    counts[type] = (counts[type] || 0) + 1;
    return counts;
  }, {
    ALL: entries.length,
    TOOL_CALL: 0,
    DIRECTIVE: 0,
    REPORT: 0,
    RESPONSE: 0,
    TOOL_RESULT: 0,
    VETO: 0,
    HEARTBEAT: 0,
    OUTBOUND: 0,
    UNKNOWN: 0,
  });

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch("/api/ceo/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, action: "CHAT" }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Unknown error" }));
        setError(err.error ?? `HTTP ${res.status}`);
      } else {
        // Append optimistic outbound entry immediately
        const now = Date.now();
        const outboundEntry: FeedEntry = {
          parsed: {
            message_type: "OUTBOUND",
            sender_id: "you",
            payload: { instruction: text },
            timestamp: new Date().toISOString(),
          },
          raw: JSON.stringify({ message_type: "OUTBOUND", sender_id: "you", payload: { instruction: text } }),
          ts: now,
          outbound: true,
        };
        append(outboundEntry);
        setInput("");
        inputRef.current?.focus();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setSending(false);
    }
  }, [input, sending, append]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  return (
    <div className="dashboard-page flex flex-col h-full">
      <PageHeader
        icon="brain"
        title="CEO Chat"
        description={
          <span className="flex items-center gap-2 flex-wrap">
            <span
              className={clsx(
                "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xxs font-semibold border",
                connected
                  ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                  : "bg-slate-800/60 text-slate-400 border-slate-700",
              )}
            >
              <span className={clsx("w-1.5 h-1.5 rounded-full", connected ? "bg-emerald-400 animate-pulse" : "bg-slate-500")} />
              {connected ? "stream:exec_ceo connected" : "connecting…"}
            </span>
            <span className="text-slate-500 text-xxs">
              {filteredEntries.length} chat message{filteredEntries.length === 1 ? "" : "s"}
            </span>
          </span>
        }
        actions={
          <button
            type="button"
            onClick={clear}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-900/60 text-slate-400 hover:bg-slate-800 hover:text-rose-300 hover:border-rose-500/40 transition-colors"
          >
            Clear
          </button>
        }
      />

      {entries.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-4 mt-2">
          {KNOWN_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => setActiveType(type)}
              className={clsx(
                "inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xxs font-semibold border transition-colors",
                activeType === type
                  ? "bg-blue-500/15 text-blue-200 border-blue-500/40"
                  : "bg-slate-900/60 text-slate-400 border-slate-700 hover:bg-slate-800 hover:text-slate-200",
              )}
            >
              {type === "ALL" ? "All" : type.replace(/_/g, " ")}
              <span className="font-mono opacity-80">
                {typeCounts[type] || 0}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {filteredEntries.length === 0 ? (
          <div className="py-16">
            <EmptyState
              icon="radio"
              title="Start a conversation with the CEO"
              description={
                connected
                  ? "The connection is live. Type a message below and press Enter to send."
                  : "Connecting to the CEO stream… Messages will appear once connected."
              }
            />
          </div>
        ) : (
          <>
            {(() => {
              // Group entries by date
              const groups = new Map<string, FeedEntry[]>();
              for (const entry of filteredEntries) {
                const dateLabel = getChatDateLabel(entry.ts);
                if (!groups.has(dateLabel)) {
                  groups.set(dateLabel, []);
                }
                groups.get(dateLabel)!.push(entry);
              }

              return Array.from(groups.entries()).map(([dateLabel, entries]) => (
                <div key={dateLabel} className="space-y-2">
                  <div className="flex items-center gap-2 my-2">
                    <div className="flex-1 border-t border-slate-800" />
                    <span className="text-xs text-slate-500 px-2 font-medium bg-slate-950/50">{dateLabel}</span>
                    <div className="flex-1 border-t border-slate-800" />
                  </div>
                  {entries.map((entry) => {
                    const parsed = entry.parsed;
                    const type = (parsed?.message_type ?? parsed?.msg_type ?? "UNKNOWN") as KnownType;
                    const isOutbound = isUserSentEntry(entry);
                    const operatorMessage = isOutbound
                      ? parsed?.payload
                        ? (typeof parsed.payload === "object" && parsed.payload && "instruction" in parsed.payload
                            ? String((parsed.payload as Record<string, unknown>).instruction)
                            : JSON.stringify(parsed.payload))
                        : ""
                      : "";
                    const ceoMessage = isOutbound
                      ? ""
                      : payloadText(parsed?.payload) ?? "";
                    const text = isOutbound ? operatorMessage : ceoMessage;
                    const role = isOutbound ? "operator" as const : "ceo" as const;

                    if (!text) return null;

                    return (
                      <div
                        key={entry.raw}
                        className={clsx("flex gap-3", role === "operator" ? "flex-row-reverse" : "flex-row")}
                      >
                        <div
                          className={clsx(
                            "flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold",
                            role === "operator" ? "bg-violet-600 text-white" : "bg-blue-600 text-white",
                          )}
                        >
                          {role === "operator" ? "OP" : "CEO"}
                        </div>
                        <div
                          className={clsx(
                            "max-w-[70%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                            role === "operator"
                              ? "bg-violet-600 text-white rounded-tr-sm"
                              : clsx("rounded-tl-sm", getTypeClass(type)),
                          )}
                        >
                          <p className="whitespace-pre-wrap break-words">{text}</p>
                          <span
                            className={clsx(
                              "block mt-1 text-xxs",
                              role === "operator" ? "text-violet-200" : clsx(getTypeAccent(type)),
                            )}
                          >
                            {formatInTz(entry.ts, "HH:mm:ss")}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ));
            })()}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-slate-800 px-4 py-3">
        <div className="flex gap-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message the CEO… (Enter to send, Shift+Enter for newline)"
            rows={1}
            aria-label="Message to CEO"
            className="flex-1 rounded-xl bg-slate-950/80 border border-slate-700 text-sm text-slate-100 placeholder-slate-500 px-4 py-3 resize-none focus:border-blue-500/60 focus:outline-none transition-colors"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!input.trim() || sending}
            aria-label="Send message"
            className={clsx(
              "flex-shrink-0 flex items-center justify-center w-11 h-11 rounded-xl border transition-colors",
              input.trim() && !sending
                ? "bg-blue-600 hover:bg-blue-500 text-white border-blue-500/60 hover:border-blue-400"
                : "bg-slate-800 text-slate-500 border-slate-700 cursor-not-allowed",
            )}
          >
            {sending ? (
              <span className="w-4 h-4 border border-slate-400 border-t-transparent rounded-full animate-spin" />
            ) : (
              <Send size={16} />
            )}
          </button>
        </div>
        {error && (
          <div className="mt-2 flex items-center gap-2 text-xs text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
