"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { RefObject } from "react";
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
import ThemeToggle from "@/components/ThemeToggle";

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
    title: "Identity",
    items: [
      { href: "/identities",         label: "Identities", icon: Users },
      { href: "/mail-domains",       label: "Mail Domains", icon: Network },
      { href: "/mailboxes",          label: "Mailboxes", icon: Inbox },
      { href: "/outbound-mail",      label: "Outbound Mail", icon: Send },
      { href: "/mail-relay",         label: "Mail Relay", icon: Radio },
      { href: "/external-accounts",  label: "External Accounts", icon: Lock },
      { href: "/auth-sessions",      label: "Auth Sessions", icon: ShieldCheck },
      { href: "/identity-approvals", label: "Identity Approvals", icon: ShieldCheck },
      { href: "/identity-audit",     label: "Identity Audit", icon: ScrollText },
    ],
  },
  {
    title: "Admin",
    items: [
      { href: "/system", label: "System", icon: Settings },
      { href: "/tools",  label: "Tools",  icon: Wrench },
      { href: "/governance", label: "Governance", icon: ShieldCheck },
      { href: "/integrations", label: "PM Integrations", icon: Network },
    ],
  },
];

type SidebarProps = {
  mobileOpen?: boolean;
  onNavigate?: () => void;
  sidebarRef?: RefObject<HTMLElement | null>;
};

export default function Sidebar({
  mobileOpen = false,
  onNavigate,
  sidebarRef,
}: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  return (
    <aside
      ref={sidebarRef}
      aria-label="Primary navigation"
      className={clsx(
        "fixed inset-y-0 left-0 z-50 flex w-72 flex-shrink-0 flex-col border-r border-[var(--aiat-border)] bg-[var(--aiat-bg-deep)] shadow-2xl shadow-black/20 transition-transform duration-200 lg:static lg:z-auto lg:w-64 lg:translate-x-0 lg:bg-[var(--aiat-bg-deep)]",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
      )}
    >
      {/* Logo */}
      <div className="border-b border-[var(--aiat-border)] px-5 py-5">
        <Link href="/" prefetch={false} onClick={onNavigate} className="flex items-center gap-3 group">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 via-cyan-500 to-emerald-400 flex items-center justify-center shadow-lg shadow-blue-500/20">
            <span className="text-white text-sm font-bold">M</span>
          </div>
          <div className="leading-tight">
            <div className="text-base font-semibold tracking-tight text-[var(--aiat-text)]">
              AIAT
            </div>
            <div className="text-xxs text-[var(--aiat-text-muted)]">MAS operator console</div>
          </div>
        </Link>
        <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
          <div className="flex items-center gap-2 text-xs font-medium text-emerald-200" role="status" aria-live="polite">
            <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.8)]" aria-hidden="true" />
            <span>Control plane online</span>
          </div>
          <div className="mt-1 text-xxs text-[var(--aiat-text-muted)]">
            Projects, workers, streams and tools stay governed here.
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav id="primary-navigation" aria-label="Primary" className="flex-1 px-3 py-4 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.title} className="mb-5 last:mb-0">
            <div className="mb-2 px-2 text-xxs font-semibold uppercase tracking-wider text-[var(--aiat-text-subtle)]">
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
                      "group flex min-h-11 items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors border",
                      active
                        ? "border-blue-500/40 bg-blue-500/15 text-blue-100 font-medium shadow-sm shadow-blue-500/10"
                        : "border-transparent text-[var(--aiat-text-muted)] hover:bg-[var(--aiat-surface)] hover:text-[var(--aiat-text)]"
                    )}
                  >
                    <Icon
                      size={15}
                      className={clsx(
                        "flex-shrink-0 transition-colors",
                        active ? "text-blue-500" : "text-[var(--aiat-text-subtle)] group-hover:text-[var(--aiat-text)]"
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
      <div className="space-y-2 border-t border-[var(--aiat-border)] px-3 py-3">
        <ThemeToggle />
        <div className="flex items-center gap-2 px-2 text-xxs text-[var(--aiat-text-subtle)]">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
          <span>Operator session</span>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          className="flex min-h-11 items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm
                     text-[var(--aiat-text-muted)] hover:bg-[var(--aiat-surface)] hover:text-[var(--aiat-text)]
                     transition-colors w-full"
        >
          <LogOut size={15} className="text-slate-500" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
