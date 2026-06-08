"use client";

import { clsx } from "clsx";
import type { ReactNode } from "react";

export interface FilterChipProps<T extends string> {
  /** Whether this chip is currently selected. */
  active: boolean;
  /** Click handler. */
  onClick: () => void;
  /** Label or content inside the chip. */
  children: ReactNode;
  /** Optional count badge shown to the right of the label (e.g. "12"). */
  count?: number;
  /** Optional tone for the active state. Defaults to neutral gray. */
  activeTone?: "blue" | "gray" | "indigo" | "emerald" | "amber";
  className?: string;
}

const ACTIVE_TONES: Record<"blue" | "gray" | "indigo" | "emerald" | "amber", string> = {
  blue: "bg-blue-500/20 text-blue-100 border-blue-400/45 shadow-sm shadow-blue-950/20",
  gray: "bg-slate-600/25 text-white border-slate-500/60",
  indigo: "bg-indigo-500/20 text-indigo-100 border-indigo-400/45 shadow-sm shadow-indigo-950/20",
  emerald: "bg-emerald-500/20 text-emerald-100 border-emerald-400/45 shadow-sm shadow-emerald-950/20",
  amber: "bg-amber-500/20 text-amber-100 border-amber-400/45 shadow-sm shadow-amber-950/20",
};

/**
 * Pill-shaped filter chip used on the workers status bar, projects state
 * filter, and the flows active/inactive filter. Centralised so all chip
 * styling stays consistent.
 */
export function FilterChip<T extends string>({
  active,
  onClick,
  children,
  count,
  activeTone = "gray",
  className,
}: FilterChipProps<T>) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "px-2.5 py-1 rounded-full text-xs font-semibold border transition-colors",
        active
          ? ACTIVE_TONES[activeTone]
          : "bg-slate-950/55 text-slate-400 border-slate-700 hover:bg-slate-900 hover:text-slate-200",
        className
      )}
    >
      {children}
      {typeof count === "number" && (
        <span
          className={clsx(
            "ml-1.5 px-1.5 py-0.5 rounded-full text-xxs",
            active ? "bg-black/20" : "bg-gray-800 text-gray-500"
          )}
        >
          {count}
        </span>
      )}
    </button>
  );
}
