"use client";

import { clsx } from "clsx";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Bell,
  Brain,
  CheckCircle,
  CheckCircle2,
  Circle,
  Clock,
  Cpu,
  Database,
  FileText,
  Folder,
  FolderKanban,
  GitBranch,
  Inbox,
  Layers,
  ListChecks,
  Lock,
  type LucideIcon,
  Network,
  Package,
  Rocket,
  ScrollText,
  Server,
  Settings,
  Shield,
  TrendingUp,
  Users,
  Wallet,
  Wrench,
  XCircle,
  Zap,
} from "lucide-react";
import type { ReactNode } from "react";

export type KpiTone = "neutral" | "positive" | "warning" | "negative" | "info";

export type KpiIconName =
  | "activity" | "alert" | "alert-triangle" | "arrow-right" | "bar-chart" | "bell" | "brain"
  | "check" | "check-circle" | "circle" | "clock" | "cpu" | "database" | "file-text"
  | "folder" | "folder-kanban" | "git-branch" | "inbox" | "layers" | "list-checks"
  | "lock" | "network" | "package" | "rocket" | "scroll" | "server" | "settings"
  | "shield" | "trending-up" | "users" | "wallet" | "wrench" | "x-circle" | "zap";

const ICON_MAP: Record<KpiIconName, LucideIcon> = {
  activity: Activity,
  alert: AlertCircle,
  "alert-triangle": AlertTriangle,
  "arrow-right": ArrowRight,
  "bar-chart": BarChart3,
  bell: Bell,
  brain: Brain,
  check: CheckCircle,
  "check-circle": CheckCircle2,
  circle: Circle,
  clock: Clock,
  cpu: Cpu,
  database: Database,
  "file-text": FileText,
  folder: Folder,
  "folder-kanban": FolderKanban,
  "git-branch": GitBranch,
  inbox: Inbox,
  layers: Layers,
  "list-checks": ListChecks,
  lock: Lock,
  network: Network,
  package: Package,
  rocket: Rocket,
  scroll: ScrollText,
  server: Server,
  settings: Settings,
  shield: Shield,
  "trending-up": TrendingUp,
  users: Users,
  wallet: Wallet,
  wrench: Wrench,
  "x-circle": XCircle,
  zap: Zap,
};

const TONE_STYLES: Record<KpiTone, { iconWrap: string; icon: string; value: string }> = {
  neutral: {
    iconWrap: "bg-slate-800/80 border-slate-700/80",
    icon: "text-slate-300",
    value: "text-white",
  },
  positive: {
    iconWrap: "bg-emerald-500/10 border-emerald-500/25",
    icon: "text-emerald-400",
    value: "text-emerald-300",
  },
  warning: {
    iconWrap: "bg-amber-500/10 border-amber-500/25",
    icon: "text-amber-400",
    value: "text-amber-300",
  },
  negative: {
    iconWrap: "bg-rose-500/10 border-rose-500/25",
    icon: "text-rose-400",
    value: "text-rose-300",
  },
  info: {
    iconWrap: "bg-blue-500/10 border-blue-500/25",
    icon: "text-blue-400",
    value: "text-blue-300",
  },
};

export interface KpiCardProps {
  /** Big label above the value (e.g. "Active Projects"). */
  label: string;
  /** Primary value — typically a number or short string. */
  value: ReactNode;
  /** Optional sub-text under the value (e.g. "5-min rate", "3 of 12 total"). */
  hint?: ReactNode;
  /** Icon name shown in the colored badge. */
  icon?: KpiIconName;
  /** Tone drives the icon badge color. */
  tone?: KpiTone;
  /** Optional className for the wrapping element. */
  className?: string;
}

/**
 * Reusable KPI / metric card. Renders a label, a value, an optional hint, and
 * a colored icon badge. The {@link icon} prop is a string name so this card
 * can be used from server components (which can't pass function refs to
 * client components).
 */
export function KpiCard({
  label,
  value,
  hint,
  icon,
  tone = "neutral",
  className,
}: KpiCardProps) {
  const cfg = TONE_STYLES[tone];
  const Icon = icon ? ICON_MAP[icon] : undefined;
  return (
    <div
      className={clsx(
        "group relative overflow-hidden bg-slate-900/80 border border-slate-800 rounded-xl p-4 flex items-start gap-3 shadow-sm shadow-black/10 transition-colors hover:border-slate-700",
        className
      )}
    >
      <div className={clsx(
        "absolute inset-x-0 top-0 h-0.5 opacity-70",
        tone === "positive" && "bg-emerald-400",
        tone === "warning" && "bg-amber-400",
        tone === "negative" && "bg-rose-400",
        tone === "info" && "bg-blue-400",
        tone === "neutral" && "bg-slate-500"
      )} />
      {Icon && (
        <div
          className={clsx(
            "flex-shrink-0 w-10 h-10 rounded-lg border flex items-center justify-center",
            cfg.iconWrap
          )}
        >
          <Icon size={18} className={cfg.icon} />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-xs text-slate-500 uppercase tracking-wider font-semibold">
          {label}
        </div>
        <div className={clsx("text-2xl font-bold mt-0.5 leading-none", cfg.value)}>
          {value}
        </div>
        {hint && (
          <div className="text-xs text-slate-500 mt-1.5">{hint}</div>
        )}
      </div>
    </div>
  );
}
