"use client";

import Link from "next/link";
import { clsx } from "clsx";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  Brain,
  FolderKanban,
  GitBranch,
  Inbox,
  type LucideIcon,
  Lock,
  Network,
  ScrollText,
  Settings,
  Users,
  Wrench,
} from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  activity: Activity,
  "arrow-up-right": ArrowUpRight,
  "bar-chart": BarChart3,
  brain: Brain,
  "folder-kanban": FolderKanban,
  "git-branch": GitBranch,
  inbox: Inbox,
  lock: Lock,
  network: Network,
  scroll: ScrollText,
  settings: Settings,
  users: Users,
  wrench: Wrench,
};

const TONE: Record<string, string> = {
  blue: "bg-blue-500/10 text-blue-400 border-blue-500/20 group-hover:border-blue-500/50",
  indigo: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20 group-hover:border-indigo-500/50",
  emerald: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20 group-hover:border-emerald-500/50",
  amber: "bg-amber-500/10 text-amber-400 border-amber-500/20 group-hover:border-amber-500/50",
  rose: "bg-rose-500/10 text-rose-400 border-rose-500/20 group-hover:border-rose-500/50",
  zinc: "bg-zinc-500/10 text-zinc-300 border-zinc-500/20 group-hover:border-zinc-500/50",
};

export interface QuickLinkCardProps {
  href: string;
  label: string;
  desc: string;
  icon: string;
  tone: keyof typeof TONE;
}

/**
 * Card-style link used on the system overview page. Renders a colored icon
 * badge, label, and short description, with a small "open" affordance on
 * hover. Resolves the icon by name so it can be rendered from a server
 * component parent.
 */
export function QuickLinkCard({ href, label, desc, icon, tone }: QuickLinkCardProps) {
  const Icon = ICON_MAP[icon] ?? Activity;
  return (
    <Link
      href={href}
      prefetch={false}
      className={clsx(
        "group flex items-start gap-3 rounded-xl border border-slate-800 bg-slate-950/45 p-3 transition-all",
        "hover:border-slate-700 hover:bg-slate-900/90 hover:shadow-sm hover:shadow-black/20"
      )}
    >
      <div className={clsx("flex-shrink-0 w-9 h-9 rounded-md border flex items-center justify-center transition-colors", TONE[tone])}>
        <Icon size={16} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1 text-sm text-gray-100 group-hover:text-white">
          <span className="truncate">{label}</span>
          <ArrowUpRight size={12} className="opacity-0 -ml-3 group-hover:opacity-100 group-hover:ml-0 transition-all text-gray-500" />
        </div>
        <div className="text-xs text-slate-500 mt-1 line-clamp-2 leading-5">{desc}</div>
      </div>
    </Link>
  );
}
