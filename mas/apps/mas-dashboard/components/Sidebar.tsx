"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clsx } from "clsx";
import {
  LayoutDashboard,
  FolderKanban,
  Radio,
  Brain,
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
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/",             label: "Overview",     icon: LayoutDashboard },
  { href: "/projects",     label: "Projects",     icon: FolderKanban },
  { href: "/flows",        label: "Flows",        icon: GitBranch },
  { href: "/system-viz",   label: "System Viz",   icon: Network },
  { href: "/streams",      label: "Streams",      icon: Radio },
  { href: "/ceo",          label: "CEO Feed",     icon: Brain },
  { href: "/workers",      label: "Workers",      icon: Users },
  { href: "/credentials",  label: "Credentials",  icon: Lock },
  { href: "/metrics",      label: "Metrics",      icon: BarChart3 },
  { href: "/dlq",          label: "Dead Letters", icon: Inbox },
  { href: "/logs",         label: "Logs",         icon: ScrollText },
  { href: "/system",       label: "System",       icon: Settings },
  { href: "/tools",        label: "Tools",        icon: Wrench },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  return (
    <aside className="w-56 flex-shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 bg-blue-600 rounded-md flex items-center justify-center">
            <span className="text-white text-xs font-bold">M</span>
          </div>
          <div>
            <div className="text-sm font-semibold text-white leading-none">MAS</div>
            <div className="text-xxs text-gray-500 leading-none mt-0.5">Dashboard</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
                active
                  ? "bg-blue-600/20 text-blue-400 font-medium"
                  : "text-gray-400 hover:text-gray-100 hover:bg-gray-800"
              )}
            >
              <Icon size={16} className="flex-shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Logout */}
      <div className="px-3 py-4 border-t border-gray-800">
        <button
          onClick={handleLogout}
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm
                     text-gray-400 hover:text-gray-100 hover:bg-gray-800
                     transition-colors w-full"
        >
          <LogOut size={16} />
          Sign out
        </button>
      </div>
    </aside>
  );
}
