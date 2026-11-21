"use client";

import { useEffect, useState } from "react";
import { clsx } from "clsx";

type PlaneState = "online" | "degraded" | "offline";

/**
 * Live control-plane indicator. Polls the dashboard health endpoint so the
 * sidebar never claims "online" while the backend is unreachable.
 */
export function useControlPlaneHealth(pollMs = 30_000): PlaneState {
  const [state, setState] = useState<PlaneState>("online");

  useEffect(() => {
    let cancelled = false;
    async function probe() {
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        if (cancelled) return;
        setState(res.ok ? "online" : "degraded");
      } catch {
        if (!cancelled) setState("offline");
      }
    }
    void probe();
    const timer = window.setInterval(probe, pollMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pollMs]);

  return state;
}

const DOT: Record<PlaneState, string> = {
  online: "bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.8)]",
  degraded: "bg-amber-400 shadow-[0_0_12px_rgba(251,191,36,0.8)]",
  offline: "bg-rose-400 shadow-[0_0_12px_rgba(251,113,133,0.8)]",
};

const LABEL: Record<PlaneState, string> = {
  online: "Control plane online",
  degraded: "Control plane degraded",
  offline: "Control plane unreachable",
};
const TEXT: Record<PlaneState, string> = {
  online: "text-emerald-200",
  degraded: "text-amber-200",
  offline: "text-rose-200",
};

export default function ControlPlaneStatus({ compact = false }: { compact?: boolean }) {
  const state = useControlPlaneHealth();
  return (
    <div
      className={clsx("flex items-center gap-2 text-xs font-medium", compact ? "text-[var(--aiat-status-online)]" : TEXT[state])}
      role="status"
      aria-live="polite"
      aria-label={LABEL[state]}
    >
      <span className={clsx("h-2 w-2 rounded-full", DOT[state])} aria-hidden="true" />
      {!compact && <span>{LABEL[state]}</span>}
      {compact && <span className="sr-only">{LABEL[state]}</span>}
    </div>
  );
}
