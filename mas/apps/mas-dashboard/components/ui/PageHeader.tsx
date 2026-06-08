"use client";

import { clsx } from "clsx";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Brain,
  Database,
  FileText,
  FolderKanban,
  GitBranch,
  Inbox,
  Key,
  type LucideIcon,
  Lock,
  Network,
  Radio,
  Rocket,
  ScrollText,
  Settings,
  ShieldCheck,
  Users,
  Wrench,
} from "lucide-react";
import type { ReactNode } from "react";

export type PageIconName =
  | "activity" | "alert" | "arrow-up-right" | "bar-chart" | "brain" | "database" | "file-text"
  | "folder-kanban" | "git-branch" | "inbox" | "key" | "lock" | "network" | "radio" | "rocket"
  | "scroll" | "settings" | "shield-check" | "users" | "wrench";

const ICON_MAP: Record<PageIconName, LucideIcon> = {
  activity: Activity,
  alert: AlertTriangle,
  "arrow-up-right": ArrowUpRight,
  "bar-chart": BarChart3,
  brain: Brain,
  database: Database,
  "file-text": FileText,
  "folder-kanban": FolderKanban,
  "git-branch": GitBranch,
  inbox: Inbox,
  key: Key,
  lock: Lock,
  network: Network,
  radio: Radio,
  rocket: Rocket,
  scroll: ScrollText,
  settings: Settings,
  "shield-check": ShieldCheck,
  users: Users,
  wrench: Wrench,
};

export interface PageHeaderProps {
  /** Optional icon name displayed in a small square badge next to the title. */
  icon?: PageIconName;
  /** Main title. */
  title: string;
  /** Smaller line under the title — used for "X total", a subtitle, or a status. */
  description?: ReactNode;
  /** Action area (buttons) — pushed to the right. */
  actions?: ReactNode;
  /** Optional className for the wrapper. */
  className?: string;
}

/**
 * Consistent page header used at the top of every dashboard page. The {@link
 * icon} prop accepts a string name (not a function) so this header can be
 * rendered from server components.
 */
export function PageHeader({
  icon,
  title,
  description,
  actions,
  className,
}: PageHeaderProps) {
  const Icon = icon ? ICON_MAP[icon] : undefined;
  return (
    <header
      className={clsx(
        "flex flex-wrap items-start justify-between gap-4 rounded-2xl border border-slate-800/80 bg-slate-950/35 px-4 py-4 shadow-sm shadow-black/10",
        className
      )}
    >
      <div className="flex items-start gap-3 min-w-0">
        {Icon && (
          <div className="flex-shrink-0 w-11 h-11 rounded-xl bg-blue-500/10 border border-blue-400/25 flex items-center justify-center text-blue-300 shadow-sm shadow-blue-950/40">
            <Icon size={18} />
          </div>
        )}
        <div className="min-w-0">
          <div className="text-xxs font-semibold uppercase tracking-wider text-slate-500">
            AIAT Control Plane
          </div>
          <h1 className="mt-0.5 text-2xl font-semibold text-white tracking-tight truncate">
            {title}
          </h1>
          {description && (
            <div className="text-sm text-slate-400 mt-1">{description}</div>
          )}
        </div>
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      )}
    </header>
  );
}
