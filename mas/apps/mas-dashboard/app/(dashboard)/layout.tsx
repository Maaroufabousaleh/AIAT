import Sidebar from "@/components/Sidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-[var(--aiat-bg)] text-slate-100">
      <Sidebar />
      <main className="relative min-w-0 flex-1 overflow-y-auto">
        <div className="pointer-events-none fixed inset-y-0 left-64 right-0 opacity-80">
          <div className="absolute inset-x-0 top-0 h-56 bg-[radial-gradient(circle_at_30%_0%,rgba(37,99,235,0.16),transparent_38rem)]" />
          <div className="absolute right-0 top-0 h-72 w-96 bg-[radial-gradient(circle_at_100%_0%,rgba(45,212,191,0.1),transparent_26rem)]" />
        </div>
        <div className="relative min-h-full w-full">
          {children}
        </div>
      </main>
    </div>
  );
}
