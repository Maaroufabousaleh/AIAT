"use client";

import { useEffect, useRef, useState } from "react";
import { Menu, X } from "lucide-react";
import Sidebar from "@/components/Sidebar";

export default function DashboardShell({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuWasOpen = useRef(false);

  useEffect(() => {
    if (menuOpen) {
      const firstFocusable = sidebarRef.current?.querySelector<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
      );
      firstFocusable?.focus();
    } else if (menuWasOpen.current) {
      menuButtonRef.current?.focus();
    }
    menuWasOpen.current = menuOpen;
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [menuOpen]);

  return (
    <div className="flex h-dvh overflow-hidden bg-[var(--aiat-bg)] text-slate-100">
      <a
        href="#dashboard-main"
        className="sr-only fixed left-3 top-3 z-[100] rounded-lg border border-blue-300/60 bg-slate-950 px-3 py-2 text-sm font-semibold text-blue-100 shadow-xl focus:not-sr-only focus:outline-none focus:ring-2 focus:ring-blue-300 focus:ring-offset-2 focus:ring-offset-slate-950"
      >
        Skip to main content
      </a>
      {menuOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          tabIndex={-1}
          onClick={() => setMenuOpen(false)}
          className="fixed inset-0 z-40 cursor-default bg-black/65 backdrop-blur-sm lg:hidden"
        />
      )}
      <Sidebar
        sidebarRef={sidebarRef}
        mobileOpen={menuOpen}
        onNavigate={() => setMenuOpen(false)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-slate-800/90 bg-slate-950/90 px-3 backdrop-blur-xl lg:hidden">
          <button
            type="button"
            ref={menuButtonRef}
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            aria-controls="primary-navigation"
            className="inline-flex h-11 w-11 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-slate-200"
          >
            {menuOpen ? <X size={17} aria-hidden="true" /> : <Menu size={17} aria-hidden="true" />}
          </button>
          <div className="text-sm font-semibold tracking-tight text-white">AIAT Control Plane</div>
          <div className="flex items-center gap-2 text-xs text-emerald-200" role="status" aria-live="polite">
            <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.75)]" aria-hidden="true" />
            <span className="sr-only">Control plane online</span>
          </div>
        </header>

        <main
          id="dashboard-main"
          tabIndex={-1}
          className="relative min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
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
