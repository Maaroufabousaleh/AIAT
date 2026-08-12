"use client";

import { clsx } from "clsx";
import {
  Activity,
  AlertCircle,
  Circle,
  GitBranch,
  FolderKanban,
  type LucideIcon,
  Key,
  Layers,
  Inbox,
  Package,
  Plus,
  Radio,
  ScrollText,
  Sparkles,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";

export type EmptyStateIconName =
  | "inbox" | "layers" | "alert" | "activity" | "key" | "package"
  | "plus" | "radio" | "scroll" | "sparkles" | "users" | "circle"
  | "git-branch" | "folder-kanban";

const ICON_MAP: Record<EmptyStateIconName, LucideIcon> = {
  inbox: Inbox,
  layers: Layers,
  alert: AlertCircle,
  activity: Activity,
  "folder-kanban": FolderKanban,
  "git-branch": GitBranch,
  key: Key,
  package: Package,
  plus: Plus,
  radio: Radio,
  scroll: ScrollText,
  sparkles: Sparkles,
  users: Users,
  circle: Circle,
};

export interface EmptyStateProps {
  /** Big icon name shown in a soft circular badge above the title. */
  icon?: EmptyStateIconName;
  /** Bold heading line. */
  title: string;
  /** Optional supporting copy. */
  description?: string;
  /** Optional primary CTA (e.g. a "New Project" button). */
  action?: ReactNode;
  /** Optional secondary action (e.g. "Learn more" link). */
  secondaryAction?: ReactNode;
  /** Visual tone — defaults to neutral, can be set to "positive" for "all good" states. */
  tone?: "neutral" | "positive" | "muted";
  /** Override the wrapping element. Defaults to a div. */
  className?: string;
}

const TONE_STYLES: Record<NonNullable<EmptyStateProps["tone"]>, string> = {
  neutral: "text-gray-500",
  positive: "text-emerald-400",
  muted: "text-gray-600",
};

/**
 * Consistent empty / zero-data state used across the dashboard. Combines a
 * circular icon badge, a title, optional description, and call-to-action slots.
 * The {@link icon} prop accepts a string name so this can be used from server
 * components.
 */
export function EmptyState({
  icon = "inbox",
  title,
  description,
  action,
  secondaryAction,
  tone = "neutral",
  className,
}: EmptyStateProps) {
  const Icon = ICON_MAP[icon];
  return (
    <div
      className={clsx(
        "flex flex-col items-center justify-center text-center px-6 py-12 rounded-xl border border-dashed border-slate-700/80 bg-slate-950/45",
        TONE_STYLES[tone],
        className
      )}
    >
      <div
        className={clsx(
          "w-12 h-12 rounded-full flex items-center justify-center mb-3",
          tone === "positive"
            ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
            : "bg-slate-800/80 text-slate-400 border border-slate-700/80"
        )}
      >
        <Icon size={22} aria-hidden="true" />
      </div>
      <div className="text-sm font-semibold text-slate-200">{title}</div>
      {description && (
        <p className="text-xs text-slate-500 mt-1 max-w-md leading-5">{description}</p>
      )}
      {(action || secondaryAction) && (
        <div className="mt-4 flex items-center gap-3">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}
