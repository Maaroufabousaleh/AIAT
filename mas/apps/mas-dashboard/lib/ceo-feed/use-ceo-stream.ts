"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { FeedEntry, RecentStreamEntry } from "./types";
import { entryFromRaw } from "./parsing";

type FilterPredicate = ((entry: FeedEntry) => boolean) | null;

function entriesEqual(a: FeedEntry, b: FeedEntry): boolean {
  if (a.raw === b.raw) return true;
  const aId = a.parsed?.message_id ?? a.parsed?.envelope?.message_id;
  const bId = b.parsed?.message_id ?? b.parsed?.envelope?.message_id;
  if (aId && bId && aId === bId) return true;
  const aText =
    a.parsed?.payload?.instruction ??
    a.parsed?.payload?.message ??
    a.parsed?.payload?.response ??
    a.parsed?.payload?.report ??
    "";
  const bText =
    b.parsed?.payload?.instruction ??
    b.parsed?.payload?.message ??
    b.parsed?.payload?.response ??
    b.parsed?.payload?.report ??
    "";
  return aText && bText && aText === bText && Math.abs(a.ts - b.ts) < 5000;
}

export function useCeoStream(
  teamId: string,
  filter: FilterPredicate,
  limit = 50,
) {
  const [entries, setEntries] = useState<FeedEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const [retryAttempt, setRetryAttempt] = useState(0);
  const cancelledRef = useRef(false);
  const filterRef = useRef(filter);
  const entriesRef = useRef<FeedEntry[]>([]);
  const connectionIdRef = useRef(0);

  useEffect(() => {
    filterRef.current = filter;
  }, [filter]);

  useEffect(() => {
    cancelledRef.current = false;
    const connectionId = ++connectionIdRef.current;
    const controller = new AbortController();
    let historyError: string | null = null;
    let streamError: string | null = null;
    let accessDeniedForConnection = false;
    let eventSource: EventSource | null = null;

    const isCurrent = () =>
      !cancelledRef.current && connectionIdRef.current === connectionId;

    const describeError = (cause: unknown, fallback: string): string => {
      if (cause instanceof Error && cause.message) return cause.message;
      return fallback;
    };

    const refreshFailureState = () => {
      if (!isCurrent()) return;
      const nextError = streamError ?? historyError;
      setError(nextError);
      setStale(Boolean(nextError) && entriesRef.current.length > 0);
    };

    const markAccessDenied = (detail: string) => {
      if (!isCurrent()) return;
      accessDeniedForConnection = true;
      historyError = detail;
      streamError = null;
      setAccessDenied(true);
      setConnected(false);
      setError(detail);
      setStale(entriesRef.current.length > 0);
      eventSource?.close();
    };

    setConnected(false);
    setError(null);
    setStale(false);
    setAccessDenied(false);

    fetch(
      `/api/streams/${encodeURIComponent(teamId)}?history=1&limit=${limit}`,
      { cache: "no-store", signal: controller.signal },
    )
      .then(async (res) => {
        if (!res.ok) {
          let detail = `CEO conversation history failed with HTTP ${res.status}`;
          try {
            const body = (await res.json()) as { error?: unknown; detail?: unknown };
            const message = body.error ?? body.detail;
            if (typeof message === "string" && message.trim()) detail = message;
          } catch {
            // Keep the bounded HTTP fallback when the error body is not JSON.
          }
          if (res.status === 401 || res.status === 403) {
            markAccessDenied(detail);
            return null;
          }
          throw new Error(detail);
        }
        return res.json();
      })
      .then((data: { entries?: RecentStreamEntry[] } | null) => {
        if (!isCurrent() || data === null) return;
        if (!data || !Array.isArray(data.entries)) {
          throw new Error("CEO conversation history returned an invalid response");
        }
        const parsed = data.entries.map((e) => entryFromRaw(e.envelope));
        const filtered = filterRef.current
          ? parsed.filter(filterRef.current)
          : parsed;
        setEntries((prev) => {
          const merged = [...filtered];
          // History and SSE start concurrently. Preserve every live or optimistic
          // entry that arrived before the history request completed.
          for (const existing of prev) {
            if (!merged.some((m) => entriesEqual(m, existing))) {
              merged.push(existing);
            }
          }
          const next = merged.sort((a, b) => a.ts - b.ts).slice(-300);
          entriesRef.current = next;
          return next;
        });
        historyError = null;
        refreshFailureState();
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || !isCurrent()) return;
        historyError = describeError(cause, "CEO conversation history is unavailable");
        refreshFailureState();
      });

    eventSource = new EventSource(`/api/streams/${encodeURIComponent(teamId)}`);
    eventSource.addEventListener("connected", () => {
      if (!isCurrent() || accessDeniedForConnection) return;
      streamError = null;
      setConnected(true);
      refreshFailureState();
    });
    eventSource.addEventListener("error", (event) => {
      const raw = event instanceof MessageEvent ? event.data : undefined;
      let detail = "CEO live conversation stream disconnected";
      let status: number | null = null;
      if (typeof raw === "string" && raw.trim()) {
        try {
          const payload = JSON.parse(raw) as { error?: unknown; detail?: unknown; status?: unknown };
          if (typeof payload.status === "number") status = payload.status;
          const message = payload.error ?? payload.detail;
          if (typeof message === "string" && message.trim()) detail = message;
        } catch {
          // EventSource error payloads are often empty or non-JSON.
        }
      }
      if (!isCurrent() || accessDeniedForConnection) return;
      if (status === 401 || status === 403) {
        markAccessDenied(detail);
        return;
      }
      streamError = detail;
      setConnected(false);
      refreshFailureState();
    });
    eventSource.onmessage = (e) => {
      if (!isCurrent() || accessDeniedForConnection) return;
      const entry = entryFromRaw(e.data);
      if (!filterRef.current || filterRef.current(entry)) {
        setEntries((prev) => {
          if (prev.some((existing) => entriesEqual(existing, entry))) {
            return prev;
          }
          const next = [...prev.slice(-300), entry];
          entriesRef.current = next;
          return next;
        });
      }
    };

    return () => {
      cancelledRef.current = true;
      controller.abort();
      eventSource?.close();
    };
  }, [retryAttempt, teamId, limit]);

  const retry = useCallback(() => {
    setRetryAttempt((attempt) => attempt + 1);
  }, []);

  const clear = useCallback(() => {
    entriesRef.current = [];
    setEntries([]);
  }, []);

  const append = useCallback((entry: FeedEntry) => {
    setEntries((prev) => {
      if (prev.some((existing) => entriesEqual(existing, entry))) {
        return prev;
      }
      const next = [...prev.slice(-299), entry];
      entriesRef.current = next;
      return next;
    });
  }, []);

  return { entries, connected, stale, error, accessDenied, retry, clear, append };
}
