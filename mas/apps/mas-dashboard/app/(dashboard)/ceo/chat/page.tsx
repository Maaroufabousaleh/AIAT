"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { clsx } from "clsx";
import {
  Activity,
  ArrowDown,
  Bot,
  CheckCircle2,
  Clock3,
  Command,
  Eraser,
  Loader2,
  RefreshCw,
  Send,
  Sparkles,
  Wifi,
  WifiOff,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatInTz, getChatDateLabel } from "@/lib/datetime";
import {
  payloadText,
  progressText,
  useCeoStream,
  type FeedEntry,
} from "@/lib/ceo-feed";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type ActiveRequest = {
  id: string;
  startedAt: number;
  stage: string;
  detail: string;
};

const QUICK_COMMANDS = [
  {
    label: "Company status",
    prompt: "Show me the live company status and tell me what needs attention.",
  },
  {
    label: "Recent projects",
    prompt: "List recent projects with their current state and the next action I should care about.",
  },
  {
    label: "Hiring board",
    prompt: "Show the hiring board workers and highlight blocked or pending candidates.",
  },
  {
    label: "Runtime readiness",
    prompt: "Show runtime and integration readiness, then summarize any gaps.",
  },
];

function isUserEntry(entry: FeedEntry): boolean {
  const parsed = entry.parsed;
  if (!parsed) return false;
  if (entry.outbound || parsed.sender_id === "you") return true;
  return (
    parsed.sender_id === "human_operator" &&
    parsed.msg_type === "TASK" &&
    parsed.payload?.action === "HUMAN_DIRECTIVE"
  );
}

function isProgressEntry(entry: FeedEntry): boolean {
  const parsed = entry.parsed;
  return (
    parsed?.msg_type === "SYSTEM_EVENT" &&
    parsed.payload?.event === "CEO_CHAT_PROGRESS"
  );
}

function isCeoResponse(entry: FeedEntry): boolean {
  const parsed = entry.parsed;
  if (!parsed || !["ceo", "ceo_agent"].includes(parsed.sender_id ?? "")) return false;
  return ["RESPONSE", "REPORT", "RESULT", "TOOL_RESULT", "VETO"].includes(
    parsed.message_type ?? parsed.msg_type ?? "",
  );
}

function isChatEntry(entry: FeedEntry): boolean {
  return isUserEntry(entry) || isProgressEntry(entry) || isCeoResponse(entry);
}

function correlationId(entry: FeedEntry): string | undefined {
  return entry.parsed?.correlation_id ?? entry.parsed?.envelope?.correlation_id;
}

function messageId(entry: FeedEntry): string | undefined {
  return entry.parsed?.message_id ?? entry.parsed?.envelope?.message_id;
}

function userText(entry: FeedEntry): string {
  const payload = entry.parsed?.payload;
  if (payload?.instruction != null) return String(payload.instruction);
  if (payload?.message != null) return String(payload.message);
  return "";
}

function safeOperatorDisplayText(text: string): string {
  const credentialIntent = /\b(?:credential|credentials|secret|secrets)\b/i.test(text);
  const secretChange = /\b(?:create|add|set|update|rotate|replace|value|token|password)\b/i.test(text);
  return credentialIntent && secretChange
    ? "Secure credential change requested. Secret-bearing details are hidden from chat history."
    : text;
}

type CeoEvidence = {
  refs: Array<{ kind: string; id: string }>;
  trace: string[];
  status?: string;
};

function ceoEvidenceHref(ref: { kind: string; id: string }): string | null {
  const id = encodeURIComponent(ref.id);
  switch (ref.kind) {
    case "project":
      return `/projects/${id}`;
    case "flow":
      return `/flows/${id}`;
    case "flow_instance":
      return `/flows?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    case "artifact":
    case "usage":
      return `/projects?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    case "company":
    case "evaluation":
    case "model":
    case "runtime":
      return `/governance?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    case "integration":
      return `/integrations?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    case "worker":
    case "worker_run":
      return `/workers?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    case "credential":
      return `/credentials?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    case "tool":
      return `/tools?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    case "trace":
      return `/logs?trace_id=${id}`;
    case "dead_letter":
      return `/dlq?evidence_kind=${encodeURIComponent(ref.kind)}&evidence_id=${id}`;
    default:
      return null;
  }
}

function ceoEvidenceRecordHref(ref: { kind: string; id: string }): string {
  return `/evidence/${encodeURIComponent(ref.kind)}/${encodeURIComponent(ref.id)}`;
}

function parseCeoEvidence(value: unknown): CeoEvidence | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const refs = Array.isArray(record.refs)
    ? record.refs.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const ref = item as Record<string, unknown>;
        return typeof ref.kind === "string" && typeof ref.id === "string"
          ? [{ kind: ref.kind, id: ref.id }]
          : [];
      })
    : [];
  const trace = Array.isArray(record.trace)
    ? record.trace.filter((item): item is string => typeof item === "string")
    : [];
  const status = typeof record.status === "string" ? record.status : undefined;
  return refs.length || trace.length ? { refs, trace, ...(status ? { status } : {}) } : null;
}

function entryKey(entry: FeedEntry, index: number): string {
  return messageId(entry) ?? `${entry.ts}-${index}-${entry.raw.slice(0, 32)}`;
}

function CeoMarkdown({ children }: { children: string }) {
  return (
    <div className="ceo-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
        a: ({ children: label, ...props }) => (
          <a {...props} target="_blank" rel="noreferrer" className="text-cyan-300 underline decoration-cyan-500/40 underline-offset-2 hover:text-cyan-200">
            {label}
          </a>
        ),
        code: ({ children: code, className, ...props }) => {
          const block = Boolean(className);
          return block ? (
            <code {...props} className={clsx(className, "block overflow-x-auto rounded-xl border border-slate-700/70 bg-slate-950/80 p-3 text-xs text-cyan-100")}>
              {code}
            </code>
          ) : (
            <code {...props} className="rounded bg-slate-950/80 px-1.5 py-0.5 text-[0.85em] text-cyan-200 ring-1 ring-slate-700/70">
              {code}
            </code>
          );
        },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export default function CeoChatPage() {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [activeRequest, setActiveRequest] = useState<ActiveRequest | null>(null);
  const [failedRequestIds, setFailedRequestIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [showJumpButton, setShowJumpButton] = useState(false);
  const [pendingConfirmationToken, setPendingConfirmationToken] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const contextWorkerIdRef = useRef<string | null>(null);
  const ownedRequestIdsRef = useRef<Set<string>>(new Set());
  const autoScrollRef = useRef(true);
  const initialScrollRef = useRef(false);

  const {
    entries,
    connected,
    stale: streamStale,
    error: streamError,
    retry: retryStream,
    clear,
    append,
  } = useCeoStream(
    "exec_ceo",
    isChatEntry,
    100,
  );

  const completedCorrelations = useMemo(
    () => new Set(entries.filter(isCeoResponse).map(correlationId).filter(Boolean)),
    [entries],
  );

  const visibleEntries = useMemo(
    () => entries.filter((entry) => !isProgressEntry(entry) || !completedCorrelations.has(correlationId(entry))),
    [completedCorrelations, entries],
  );

  useEffect(() => {
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const entry = entries[index];
      const correlation = correlationId(entry);
      if (!isCeoResponse(entry) || !correlation || !ownedRequestIdsRef.current.has(correlation)) {
        continue;
      }
      const context = entry.parsed?.payload?.context;
      const workerId = context && typeof context === "object"
        ? (context as Record<string, unknown>).worker_id
        : null;
      if (typeof workerId === "string") {
        contextWorkerIdRef.current = workerId;
        break;
      }
    }
  }, [entries]);

  useEffect(() => {
    const latestResponse = entries.findLast((entry) => {
      const correlation = correlationId(entry);
      return isCeoResponse(entry)
        && Boolean(correlation)
        && ownedRequestIdsRef.current.has(correlation!);
    });
    const context = latestResponse?.parsed?.payload?.context;
    if (!context || typeof context !== "object") return;
    const record = context as Record<string, unknown>;
    if (!Object.prototype.hasOwnProperty.call(record, "confirmation_token")) return;
    setPendingConfirmationToken(typeof record.confirmation_token === "string"
      ? record.confirmation_token
      : null);
  }, [entries]);

  useEffect(() => {
    if (!activeRequest) return;
    const matching = entries.filter((entry) => correlationId(entry) === activeRequest.id);
    const response = matching.find(isCeoResponse);
    if (response) {
      setActiveRequest(null);
      setElapsedSeconds(0);
      return;
    }
    const latestProgress = matching.filter(isProgressEntry).at(-1);
    if (latestProgress) {
      setActiveRequest((current) => current?.id === activeRequest.id
        ? {
            ...current,
            stage: String(latestProgress.parsed?.payload?.stage ?? "Working on it"),
            detail: progressText(latestProgress.parsed?.payload) ?? current.detail,
          }
        : current,
      );
    }
  }, [activeRequest, entries]);

  useEffect(() => {
    if (!activeRequest) return;
    const updateElapsed = () => {
      setElapsedSeconds(Math.max(0, Math.floor((Date.now() - activeRequest.startedAt) / 1000)));
    };
    updateElapsed();
    const interval = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(interval);
  }, [activeRequest]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    autoScrollRef.current = true;
    setShowJumpButton(false);
    bottomRef.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  useEffect(() => {
    if (!initialScrollRef.current && entries.length > 0) {
      initialScrollRef.current = true;
      window.requestAnimationFrame(() => scrollToBottom("auto"));
      return;
    }
    if (autoScrollRef.current) scrollToBottom();
  }, [activeRequest, entries.length, scrollToBottom]);

  const handleTranscriptScroll = useCallback(() => {
    const element = transcriptRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    autoScrollRef.current = distance < 120;
    setShowJumpButton(distance >= 120);
  }, []);

  const handleSend = useCallback(async (override?: string) => {
    const text = (override ?? input).trim();
    if (!text || sending || activeRequest) return;

    const requestId = crypto.randomUUID();
    const now = Date.now();
    ownedRequestIdsRef.current.add(requestId);
    const displayText = safeOperatorDisplayText(text);
    const optimisticEntry: FeedEntry = {
      parsed: {
        message_id: requestId,
        correlation_id: requestId,
        message_type: "OUTBOUND",
        sender_id: "you",
        payload: { instruction: displayText },
        created_at: new Date(now).toISOString(),
      },
      raw: JSON.stringify({ message_id: requestId, message_type: "OUTBOUND", payload: { instruction: displayText } }),
      ts: now,
      outbound: true,
    };

    append(optimisticEntry);
    setInput("");
    setError(null);
    setSending(true);
    setActiveRequest({
      id: requestId,
      startedAt: now,
      stage: "Sending to CEO",
      detail: "Publishing your request to the control plane…",
    });
    scrollToBottom();

    try {
      const res = await fetch("/api/ceo/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          request_id: requestId,
          ...(contextWorkerIdRef.current ? { context_worker_id: contextWorkerIdRef.current } : {}),
          ...(pendingConfirmationToken ? { context_confirmation_token: pendingConfirmationToken } : {}),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ error: "Unknown error" }));
        throw new Error(body.error ?? `Request failed with HTTP ${res.status}`);
      }
      setActiveRequest((current) => current?.id === requestId
        ? {
            ...current,
            stage: "CEO received your request",
            detail: "Interpreting the outcome you want and checking the live system state…",
          }
        : current,
      );
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Network error";
      setError(message);
      setFailedRequestIds((current) => new Set(current).add(requestId));
      setActiveRequest(null);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }, [activeRequest, append, input, pendingConfirmationToken, scrollToBottom, sending]);

  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }, [handleSend]);

  const handleClear = useCallback(() => {
    clear();
    setError(null);
    setFailedRequestIds(new Set());
    setPendingConfirmationToken(null);
    contextWorkerIdRef.current = null;
    ownedRequestIdsRef.current.clear();
    initialScrollRef.current = false;
  }, [clear]);

  const groupedEntries = useMemo(() => {
    const groups = new Map<string, FeedEntry[]>();
    for (const entry of visibleEntries) {
      const label = getChatDateLabel(entry.ts);
      groups.set(label, [...(groups.get(label) ?? []), entry]);
    }
    return Array.from(groups.entries());
  }, [visibleEntries]);

  const isBusy = Boolean(activeRequest);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800/80 bg-slate-950/55 px-4 py-3 backdrop-blur-xl md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl border border-cyan-400/25 bg-gradient-to-br from-blue-500/20 to-cyan-400/10 text-cyan-200 shadow-lg shadow-cyan-950/30">
            <Command size={18} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold tracking-tight text-white md:text-xl">CEO Command Center</h1>
              <span className="hidden rounded-full border border-violet-400/20 bg-violet-400/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-violet-200 sm:inline">Primary control</span>
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-xs">
              <span className={clsx("inline-flex items-center gap-1.5", connected ? "text-emerald-300" : "text-amber-300")} aria-live="polite">
                {connected ? <Wifi size={12} /> : <WifiOff size={12} />}
                {connected ? "Live" : streamStale ? "Last known" : "Reconnecting"}
              </span>
              <span className="text-slate-600">•</span>
              <span className="truncate text-slate-400">Ask for an outcome; the CEO handles routing and details.</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/ceo" className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-700/80 bg-slate-900/70 px-3 text-xs font-medium text-slate-300 transition hover:border-slate-600 hover:bg-slate-800 hover:text-white">
            <Activity size={14} />
            <span className="hidden sm:inline">Activity</span>
          </Link>
          <button type="button" onClick={handleClear} className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-700/80 bg-slate-900/70 px-3 text-xs font-medium text-slate-300 transition hover:border-rose-500/30 hover:bg-rose-500/10 hover:text-rose-200" aria-label="Clear conversation view">
            <Eraser size={14} />
            <span className="hidden sm:inline">Clear view</span>
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 p-3 md:p-4">
        <div className="mx-auto grid h-full max-w-[1600px] min-h-0 gap-4 xl:grid-cols-[minmax(0,1fr)_19rem]">
          <section className="relative flex min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-800/90 bg-slate-950/55 shadow-2xl shadow-black/20">
            {streamError && (
              <ErrorBanner
                tone={streamStale ? "warning" : "error"}
                title={streamStale ? "Showing last known CEO conversation" : "CEO conversation unavailable"}
                className="mx-3 mt-3 sm:mx-4"
                action={(
                  <button
                    type="button"
                    onClick={retryStream}
                    aria-label="Retry CEO conversation"
                    className="inline-flex min-h-11 items-center gap-2 rounded-md border border-current px-3 py-2 text-xs font-medium transition-colors hover:bg-white/10"
                  >
                    <RefreshCw size={14} aria-hidden="true" />
                    Retry
                  </button>
                )}
              >
                {streamStale
                  ? `${streamError}. Retained messages remain visible while the conversation reconnects.`
                  : `${streamError}. Retry to reconnect the CEO conversation.`}
              </ErrorBanner>
            )}
            <div ref={transcriptRef} onScroll={handleTranscriptScroll} className="min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-5 md:px-8" aria-label="CEO conversation" aria-live="polite">
              {groupedEntries.length === 0 ? (
                <div className="mx-auto flex min-h-full max-w-2xl flex-col items-center justify-center py-12 text-center">
                  <div className="relative mb-5">
                    <div className="absolute inset-0 rounded-full bg-cyan-400/20 blur-2xl" />
                    <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl border border-cyan-400/25 bg-gradient-to-br from-blue-500/20 to-cyan-400/10 text-cyan-200">
                      <Sparkles size={27} />
                    </div>
                  </div>
                  <h2 className="text-xl font-semibold text-white">What outcome should AIAT drive?</h2>
                  <p className="mt-2 max-w-lg text-sm leading-6 text-slate-400">
                    Speak naturally. The CEO will inspect live state, coordinate teams, and only ask for details when a safe decision truly needs them.
                  </p>
                  <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
                    {QUICK_COMMANDS.map((command) => (
                      <button key={command.label} type="button" onClick={() => void handleSend(command.prompt)} disabled={isBusy} className="rounded-xl border border-slate-700/70 bg-slate-900/65 px-4 py-3 text-left text-sm text-slate-200 transition hover:border-cyan-500/35 hover:bg-cyan-500/5 disabled:cursor-not-allowed disabled:opacity-50">
                        <span className="font-medium">{command.label}</span>
                        <span className="mt-1 block text-xs leading-5 text-slate-500">{command.prompt}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mx-auto w-full max-w-4xl space-y-6">
                  {groupedEntries.map(([dateLabel, dayEntries]) => (
                    <div key={dateLabel} className="space-y-4">
                      <div className="sticky top-0 z-10 flex items-center gap-3 py-1">
                        <div className="h-px flex-1 bg-slate-800/80" />
                        <span className="rounded-full border border-slate-800 bg-slate-950/90 px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500 backdrop-blur">{dateLabel}</span>
                        <div className="h-px flex-1 bg-slate-800/80" />
                      </div>
                      {dayEntries.map((entry, index) => {
                        if (isProgressEntry(entry)) {
                          const stage = String(entry.parsed?.payload?.stage ?? "Working on it");
                          const detail = progressText(entry.parsed?.payload) ?? "Processing your request…";
                          return (
                            <div key={entryKey(entry, index)} className="flex items-start gap-3 pl-1 sm:pl-12" data-testid="ceo-thinking">
                              <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full border border-cyan-400/25 bg-cyan-400/10 text-cyan-300">
                                <Loader2 size={13} className="animate-spin" />
                              </div>
                              <div className="min-w-0 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] px-3 py-2.5">
                                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                                  <span className="font-semibold text-cyan-200">{stage}</span>
                                  <span className="text-slate-600">•</span>
                                  <span className="text-slate-500">live progress</span>
                                </div>
                                <p className="mt-1 text-xs leading-5 text-slate-400">{detail}</p>
                              </div>
                            </div>
                          );
                        }

                        const user = isUserEntry(entry);
                        const text = user ? userText(entry) : payloadText(entry.parsed?.payload) ?? "";
                        if (!text) return null;
                        const action = !user && entry.parsed?.payload?.action && typeof entry.parsed.payload.action === "object"
                          ? entry.parsed.payload.action as Record<string, unknown>
                          : null;
                        const needsConfirmation = action?.requires_confirmation === true;
                        const entryContext = entry.parsed?.payload?.context;
                        const entryConfirmationToken = entryContext && typeof entryContext === "object"
                          ? (entryContext as Record<string, unknown>).confirmation_token
                          : null;
                        const confirmationIsPending = needsConfirmation
                          && typeof entryConfirmationToken === "string"
                          && entryConfirmationToken === pendingConfirmationToken;
                        const evidence = !user ? parseCeoEvidence(entry.parsed?.payload?.evidence) : null;
                        const failed = Boolean(messageId(entry) && failedRequestIds.has(messageId(entry)!));
                        return (
                          <article key={entryKey(entry, index)} className={clsx("flex items-start gap-3", user && "flex-row-reverse")}>
                            <div className={clsx("mt-1 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl border text-[10px] font-bold shadow-lg", user ? "border-violet-400/30 bg-violet-500/15 text-violet-200 shadow-violet-950/30" : "border-cyan-400/25 bg-gradient-to-br from-blue-500/25 to-cyan-400/10 text-cyan-100 shadow-cyan-950/30")}>
                              {user ? "YOU" : <Bot size={15} />}
                            </div>
                            <div className={clsx("min-w-0", user ? "max-w-[82%] sm:max-w-[72%]" : "max-w-[calc(100%-2.75rem)] flex-1")}>
                              <div className={clsx("rounded-2xl border px-4 py-3 text-sm leading-6 shadow-sm", user ? "rounded-tr-md border-violet-400/25 bg-violet-500/15 text-violet-50" : "rounded-tl-md border-slate-700/70 bg-slate-900/75 text-slate-200")}>
                                {user ? <p className="whitespace-pre-wrap break-words">{text}</p> : <CeoMarkdown>{text}</CeoMarkdown>}
                                {action && (
                                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-700/60 pt-2.5 text-[11px]">
                                    {needsConfirmation ? (
                                      <Clock3 size={13} className="text-amber-300" />
                                    ) : (
                                      <CheckCircle2 size={13} className="text-emerald-300" />
                                    )}
                                    <span className={clsx("font-medium", needsConfirmation ? "text-amber-200" : "text-emerald-200")}>
                                      {needsConfirmation ? "Confirmation required" : "Action completed"}
                                    </span>
                                    {action.type != null && <span className="rounded bg-slate-950/70 px-1.5 py-0.5 text-slate-400">{String(action.type).replaceAll("_", " ")}</span>}
                                    {action.status != null && <span className="text-slate-500">{String(action.status)}</span>}
                                    {confirmationIsPending && (
                                      <>
                                        <button type="button" onClick={() => void handleSend("confirm")} disabled={isBusy} className="ml-auto rounded-lg border border-amber-400/30 bg-amber-400/10 px-2.5 py-1 font-semibold text-amber-100 transition hover:bg-amber-400/20 disabled:opacity-50">
                                          Confirm
                                        </button>
                                        <button type="button" onClick={() => void handleSend("cancel")} disabled={isBusy} className="rounded-lg border border-slate-600/70 bg-slate-800/70 px-2.5 py-1 font-medium text-slate-300 transition hover:bg-slate-700 disabled:opacity-50">
                                          Cancel
                                        </button>
                                      </>
                                    )}
                                  </div>
                                )}
                                {evidence && (
                                  <div className="mt-3 border-t border-slate-700/60 pt-2.5 text-[11px]" data-testid="ceo-evidence">
                                    <div className="font-medium text-cyan-200" data-testid="ceo-evidence-status">
                                      {evidence.status === "unverified" ? "Unverified citation" : "Canonical evidence"}
                                    </div>
                                    {evidence.refs.length > 0 && (
                                      <div className="mt-1 flex flex-wrap gap-1.5">
                                        {evidence.refs.map((ref) => (
                                          <span key={`${ref.kind}:${ref.id}`} className="rounded bg-slate-950/70 px-1.5 py-0.5 text-slate-400">
                                            {ceoEvidenceHref(ref) ? (
                                              <>
                                                <Link
                                                  href={ceoEvidenceHref(ref)!}
                                                  className="text-cyan-300 underline decoration-cyan-500/40 underline-offset-2 hover:text-cyan-200"
                                                  data-testid="ceo-evidence-link"
                                                  aria-label={`Open ${ref.kind} evidence ${ref.id}`}
                                                >
                                                  {ref.kind} `{ref.id}`
                                                </Link>
                                                <Link
                                                  href={ceoEvidenceRecordHref(ref)}
                                                  className="ml-1 text-slate-500 underline decoration-slate-600 underline-offset-2 hover:text-slate-300"
                                                  data-testid="ceo-evidence-record-link"
                                                  aria-label={`Open dedicated ${ref.kind} evidence record ${ref.id}`}
                                                >
                                                  (record)
                                                </Link>
                                              </>
                                            ) : (
                                              `${ref.kind} \`${ref.id}\``
                                            )}
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                    {evidence.trace.length > 0 && (
                                      <div className="mt-1 text-slate-500">trace: {evidence.trace.join(" → ")}</div>
                                    )}
                                  </div>
                                )}
                              </div>
                              <div className={clsx("mt-1.5 flex items-center gap-2 px-1 text-[10px] text-slate-600", user && "justify-end")}>
                                <span>{user ? "You" : "CEO"}</span>
                                <span>•</span>
                                <span>{formatInTz(entry.ts, "HH:mm")}</span>
                                {failed && <span className="font-medium text-rose-400">Not delivered</span>}
                              </div>
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  ))}

                  {activeRequest && !visibleEntries.some((entry) => isProgressEntry(entry) && correlationId(entry) === activeRequest.id) && (
                    <div className="flex items-start gap-3 pl-1 sm:pl-12" data-testid="ceo-thinking">
                      <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full border border-cyan-400/25 bg-cyan-400/10 text-cyan-300">
                        <Loader2 size={13} className="animate-spin" />
                      </div>
                      <div className="min-w-0 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] px-3 py-2.5">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                          <span className="font-semibold text-cyan-200">{activeRequest.stage}</span>
                          <span className="inline-flex items-center gap-1 text-slate-500"><Clock3 size={11} /> {elapsedSeconds}s</span>
                        </div>
                        <p className="mt-1 text-xs leading-5 text-slate-400">{activeRequest.detail}</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <div ref={bottomRef} className="h-1" />
            </div>

            {showJumpButton && (
              <button type="button" onClick={() => scrollToBottom()} className="absolute bottom-32 left-1/2 z-20 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900/95 px-3 py-1.5 text-xs text-slate-300 shadow-xl shadow-black/40 hover:bg-slate-800">
                <ArrowDown size={13} /> Latest
              </button>
            )}

            <div className="border-t border-slate-800/90 bg-slate-950/90 p-3 backdrop-blur-xl sm:p-4">
              {error && (
                <div className="mb-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200" role="alert">{error}</div>
              )}
              <div className={clsx("rounded-2xl border bg-slate-900/80 p-2 shadow-inner transition", isBusy ? "border-cyan-500/25" : "border-slate-700/80 focus-within:border-cyan-500/45")}>
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={isBusy}
                  placeholder={isBusy ? "The CEO is working on your request…" : "Tell the CEO what outcome you want…"}
                  rows={2}
                  aria-label="Message to CEO"
                  className="block max-h-36 min-h-[3.5rem] w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 text-slate-100 outline-none placeholder:text-slate-500 disabled:cursor-wait disabled:opacity-70"
                />
                <div className="flex items-center justify-between gap-3 px-1 pt-1">
                  <div className="min-w-0 truncate text-[10px] text-slate-500">
                    {isBusy ? `Working for ${elapsedSeconds}s · live updates appear above` : "Enter to send · Shift+Enter for a new line"}
                  </div>
                  <button type="button" onClick={() => void handleSend()} disabled={!input.trim() || isBusy || sending} aria-label="Send message" className={clsx("inline-flex h-9 flex-shrink-0 items-center gap-2 rounded-xl px-3.5 text-xs font-semibold transition", input.trim() && !isBusy ? "bg-gradient-to-r from-blue-500 to-cyan-500 text-white shadow-lg shadow-cyan-950/40 hover:from-blue-400 hover:to-cyan-400" : "cursor-not-allowed bg-slate-800 text-slate-500")}>
                    {isBusy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                    <span className="hidden sm:inline">{isBusy ? "Working" : "Send"}</span>
                  </button>
                </div>
              </div>
            </div>
          </section>

          <aside className="hidden min-h-0 flex-col gap-3 overflow-y-auto xl:flex">
            <div className="rounded-2xl border border-cyan-500/20 bg-gradient-to-b from-cyan-500/[0.07] to-slate-950/50 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-white"><Sparkles size={15} className="text-cyan-300" /> Ask for outcomes</div>
              <p className="mt-2 text-xs leading-5 text-slate-400">You do not need to choose teams, tools, or routes. The CEO resolves those details and reports what changed.</p>
              <div className="mt-3 flex items-center gap-2 rounded-lg border border-slate-700/60 bg-slate-950/50 px-3 py-2 text-[11px] text-slate-400">
                {connected ? <CheckCircle2 size={13} className="text-emerald-300" /> : streamStale ? <WifiOff size={13} className="text-amber-300" /> : <Loader2 size={13} className="animate-spin text-amber-300" />}
                {connected ? "Real-time control stream connected" : streamStale ? "Last known conversation retained" : "Restoring the control stream"}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-800/90 bg-slate-950/45 p-4">
              <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Quick commands</div>
              <div className="mt-3 space-y-2">
                {QUICK_COMMANDS.map((command) => (
                  <button key={command.label} type="button" onClick={() => void handleSend(command.prompt)} disabled={isBusy} className="group w-full rounded-xl border border-slate-800 bg-slate-900/55 px-3 py-2.5 text-left transition hover:border-cyan-500/30 hover:bg-cyan-500/[0.04] disabled:cursor-not-allowed disabled:opacity-50">
                    <span className="block text-xs font-medium text-slate-200 group-hover:text-cyan-100">{command.label}</span>
                    <span className="mt-0.5 block line-clamp-2 text-[10px] leading-4 text-slate-500">{command.prompt}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-800/90 bg-slate-950/45 p-4 text-xs leading-5 text-slate-500">
              <div className="flex items-center gap-2 font-medium text-slate-300"><Bot size={14} /> Progress, not private reasoning</div>
              <p className="mt-2">Live updates show what the CEO is checking or changing. Hidden model reasoning is never exposed.</p>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
