"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { FeedEntry, RecentStreamEntry } from "./types";
import { entryFromRaw } from "./parsing";

type FilterPredicate = ((entry: FeedEntry) => boolean) | null;

function entriesEqual(a: FeedEntry, b: FeedEntry): boolean {
  if (a.raw === b.raw) return true;
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

export function useCeoStream(teamId: string, filter: FilterPredicate, limit = 50) {
  const [entries, setEntries] = useState<FeedEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const cancelledRef = useRef(false);
  const filterRef = useRef(filter);
  filterRef.current = filter;

  useEffect(() => {
    cancelledRef.current = false;

    fetch(
      `/api/streams/${encodeURIComponent(teamId)}?history=1&limit=${limit}`,
      { cache: "no-store" },
    )
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { entries?: RecentStreamEntry[] } | null) => {
        if (cancelledRef.current || !data?.entries) return;
        const parsed = data.entries.map((e) => entryFromRaw(e.envelope));
        const filtered = filterRef.current ? parsed.filter(filterRef.current) : parsed;
        setEntries((prev) => {
          const merged = [...filtered];
          // History and SSE start concurrently. Preserve every live or optimistic
          // entry that arrived before the history request completed.
          for (const existing of prev) {
            if (!merged.some((m) => entriesEqual(m, existing))) {
              merged.push(existing);
            }
          }
          return merged.sort((a, b) => a.ts - b.ts).slice(-300);
        });
      })
      .catch(() => {});

    const es = new EventSource(`/api/streams/${encodeURIComponent(teamId)}`);
    es.addEventListener("connected", () => setConnected(true));
    es.addEventListener("error", () => setConnected(false));
    es.onmessage = (e) => {
      const entry = entryFromRaw(e.data);
      if (!filterRef.current || filterRef.current(entry)) {
        setEntries((prev) => {
          if (prev.some((existing) => entriesEqual(existing, entry))) {
            return prev;
          }
          return [...prev.slice(-300), entry];
        });
      }
    };

    return () => {
      cancelledRef.current = true;
      es.close();
    };
  }, [teamId, limit]);

  const clear = useCallback(() => setEntries([]), []);

  const append = useCallback((entry: FeedEntry) => {
    setEntries((prev) => {
      if (prev.some((existing) => entriesEqual(existing, entry))) {
        return prev;
      }
      return [...prev.slice(-299), entry];
    });
  }, []);

  return { entries, connected, clear, append };
}
