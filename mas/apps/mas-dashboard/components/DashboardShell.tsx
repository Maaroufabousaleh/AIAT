"use client";

import { useState } from "react";
import { Menu, X } from "lucide-react";
import Sidebar from "@/components/Sidebar";

export default function DashboardShell({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="flex h-dvh overflow-hidden bg-[var(--aiat-bg)] text-slate-100">
      {menuOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
          className="fixed inset-0 z-40 cursor-default bg-black/65 backdrop-blur-sm lg:hidden"
        />
      )}
      <Sidebar mobileOpen={menuOpen} onNavigate={() => setMenuOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 flex-shrink-0 items-center justify-between border-b border-slate-800/90 bg-slate-950/90 px-3 backdrop-blur-xl lg:hidden">
          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-slate-200"
          >
            {menuOpen ? <X size={17} /> : <Menu size={17} />}
          </button>
          <div className="text-sm font-semibold tracking-tight text-white">AIAT Control Plane</div>
          <div className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.75)]" aria-label="Control plane online" />
        </div>

        <main className="relative min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="pointer-events-none fixed inset-y-0 left-0 right-0 opacity-80 lg:left-64">
            <div className="absolute inset-x-0 top-0 h-56 bg-[radial-gradient(circle_at_30%_0%,rgba(37,99,235,0.16),transparent_38rem)]" />
            <div className="absolute right-0 top-0 h-72 w-96 bg-[radial-gradient(circle_at_100%_0%,rgba(45,212,191,0.1),transparent_26rem)]" />
          </div>
          <div className="relative h-full min-h-0 w-full">{children}</div>
        </main>
      </div>
    </div>
  );
}
