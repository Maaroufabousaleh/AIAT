"use client";

import { useEffect, useState } from "react";
import { formatInTz } from "@/lib/datetime";

export interface OutageEntry {
  at: string;
  sources: string[];
}

const STORAGE_KEY = "aiat-overview-outages";
const MAX_ENTRIES = 10;

function readHistory(): OutageEntry[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is OutageEntry =>
        !!e && typeof e === "object" && typeof (e as OutageEntry).at === "string" &&
        !Number.isNaN(Date.parse((e as OutageEntry).at)) &&
        Array.isArray((e as OutageEntry).sources) &&
        (e as OutageEntry).sources.every((s) => typeof s === "string"),
    );
  } catch {
    return [];
  }
}

/**
 * Operator-local record of overview source outages. The overview page is a
 * server component, so cross-load history lives in localStorage: every load
 * that reports failed sources appends one entry (deduplicated against the
 * most recent). Healthy loads still render the history so the operator can
 * see flapping sources.
 */
export default function SourceOutageHistory({ failed, at }: { failed: string[]; at: string }) {
  const [history, setHistory] = useState<OutageEntry[] | null>(null);

  useEffect(() => {
    const previous = readHistory();
    const last = previous[previous.length - 1];
    const sameAsLast =
      !!last && last.sources.length === failed.length && last.sources.every((s) => failed.includes(s));
    const next = failed.length > 0 && !sameAsLast ? [...previous, { at, sources: [...failed] }].slice(-MAX_ENTRIES) : previous;
    if (next !== previous) {
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        // Storage unavailable — still show the in-memory entry below.
      }
    }
    setHistory(next);
  }, [failed, at]);

  if (!history || history.length === 0) return null;

  return (
    <section
      className="rounded-xl border border-slate-800 bg-slate-950/45 px-4 py-3"
      aria-label="Recent overview source outages"
    >
      <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        Recent source outages
      </h2>
      <ul className="mt-2 space-y-1">
        {history.map((entry, i) => (
          <li key={`${entry.at}-${i}`} className="flex flex-wrap items-baseline gap-x-2 text-xs">
            <span className="text-slate-500">{formatInTz(entry.at, "yyyy-MM-dd HH:mm")}</span>
            <span className="text-slate-300">{entry.sources.join(", ")}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
