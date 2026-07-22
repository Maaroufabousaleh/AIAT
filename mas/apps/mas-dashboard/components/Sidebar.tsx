"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clsx } from "clsx";
import {
  LayoutDashboard,
  FolderKanban,
  Radio,
  BarChart3,
  Inbox,
  ScrollText,
  Settings,
  Wrench,
  LogOut,
  GitBranch,
  Network,
  Lock,
  Users,
  Send,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
};

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Operate",
    items: [
      { href: "/ceo/chat",    label: "CEO Command",  icon: Send },
      { href: "/",            label: "Overview",     icon: LayoutDashboard },
      { href: "/projects",    label: "Projects",     icon: FolderKanban },
      { href: "/workers",     label: "Workers",      icon: Users },
      { href: "/credentials", label: "Credentials",  icon: Lock },
    ],
  },
  {
    title: "Orchestrate",
    items: [
      { href: "/flows",      label: "Flows",      icon: GitBranch },
      { href: "/system-viz", label: "System Viz", icon: Network },
      { href: "/streams",    label: "Streams",    icon: Radio },
    ],
  },
  {
    title: "Observe",
    items: [
      { href: "/analytics/litellm",   label: "LiteLLM Analytics", icon: BarChart3 },
      { href: "/analytics/omniroute", label: "OmniRoute Analytics", icon: Network },
      { href: "/metrics",             label: "Platform Metrics", icon: BarChart3 },
      { href: "/dlq",                 label: "Dead Letters", icon: Inbox },
      { href: "/logs",                label: "Logs", icon: ScrollText },
    ],
  },
  {
    title: "Admin",
    items: [
      { href: "/system", label: "System", icon: Settings },
      { href: "/tools",  label: "Tools",  icon: Wrench },
      { href: "/governance", label: "Governance", icon: ShieldCheck },
    ],
  },
];

export default function Sidebar({
  mobileOpen = false,
  onNavigate,
}: {
  mobileOpen?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  return (
    <aside className={clsx(
      "fixed inset-y-0 left-0 z-50 flex w-72 flex-shrink-0 flex-col border-r border-slate-800/90 bg-slate-950 shadow-2xl shadow-black/40 transition-transform duration-200 lg:static lg:z-auto lg:w-64 lg:translate-x-0 lg:bg-slate-950/80",
      mobileOpen ? "translate-x-0" : "-translate-x-full",
    )}>
      {/* Logo */}
      <div className="px-5 py-5 border-b border-slate-800/80">
        <Link href="/" prefetch={false} onClick={onNavigate} className="flex items-center gap-3 group">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 via-cyan-500 to-emerald-400 flex items-center justify-center shadow-lg shadow-blue-500/20">
            <span className="text-white text-sm font-bold">M</span>
          </div>
          <div className="leading-tight">
            <div className="text-base font-semibold text-white tracking-tight">
              AIAT
            </div>
            <div className="text-xxs text-slate-400">MAS operator console</div>
          </div>
        </Link>
        <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
          <div className="flex items-center gap-2 text-xs font-medium text-emerald-200">
            <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.8)]" />
            Control plane online
          </div>
          <div className="mt-1 text-xxs text-slate-400">
            Projects, workers, streams and tools stay governed here.
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.title} className="mb-5 last:mb-0">
            <div className="px-2 mb-2 text-xxs font-semibold text-slate-500 uppercase tracking-wider">
              {group.title}
            </div>
            <div className="space-y-0.5">
              {group.items.map(({ href, label, icon: Icon }) => {
                const active = href.startsWith("/analytics/") || href === "/ceo/chat"
                  ? pathname === href
                  : href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    prefetch={false}
                    onClick={onNavigate}
                    className={clsx(
                      "group flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors border",
                      active
                        ? "border-blue-500/40 bg-blue-500/15 text-blue-100 font-medium shadow-sm shadow-blue-500/10"
                        : "border-transparent text-slate-400 hover:text-slate-100 hover:bg-slate-900/80 hover:border-slate-800"
                    )}
                  >
                    <Icon
                      size={15}
                      className={clsx(
                        "flex-shrink-0 transition-colors",
                        active ? "text-blue-300" : "text-slate-500 group-hover:text-slate-300"
                      )}
                    />
                    <span className="truncate">{label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-slate-800/80 space-y-2">
        <div className="px-2 flex items-center gap-2 text-xxs text-slate-500">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
          <span>Operator session</span>
        </div>
        <button
          onClick={handleLogout}
          className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm
                     text-slate-400 hover:text-slate-100 hover:bg-slate-900/80
                     transition-colors w-full"
        >
          <LogOut size={15} className="text-slate-500" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
