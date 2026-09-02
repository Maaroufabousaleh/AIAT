"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type IntegrationData = {
  connections: Array<Record<string, unknown>>;
  conflicts: Array<Record<string, unknown>>;
  outbox: Array<Record<string, unknown>>;
  runs: Array<Record<string, unknown>>;
  lifecyclePlans: Array<Record<string, unknown>>;
};

export default function IntegrationsPage() {
  const [data, setData] = useState<IntegrationData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const [bindingId, setBindingId] = useState("");
  const [reviewedDigest, setReviewedDigest] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [hasReadContext, setHasReadContext] = useState(false);
  const hasData = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/integrations/pm", { cache: "no-store" });
      if (response.status === 401 || response.status === 403) {
        setAccessDenied(true);
        setError("This operator identity is not authorized to read or change PM integrations.");
        setStale(hasData.current);
        return;
      }
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Integration API failed");
      setData(payload);
      hasData.current = true;
      setStale(false);
      setAccessDenied(false);
      setHasReadContext(true);
    } catch (reason) {
      setAccessDenied(false);
      setError(reason instanceof Error ? reason.message : "Integration API failed");
      setStale(hasData.current);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <main className="p-6 space-y-6" aria-label="PM integrations" aria-busy={loading}>
      <PageHeader
        title="PM integrations"
        description="Provider connections, synchronization health, reconciliation runs, and conflicts."
        actions={!accessDenied ? (
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            aria-busy={loading}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} aria-hidden="true" />
            Refresh
          </button>
        ) : undefined}
      />
      {accessDenied && (
        <section
          role="region"
          aria-label="PM integrations access status"
          className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-5 py-6 shadow-sm shadow-amber-950/10"
        >
          <h2 className="text-base font-semibold text-amber-100">PM integrations access denied</h2>
          <p className="mt-2 max-w-2xl text-sm text-amber-200/80">
            {hasReadContext
              ? "The current operator identity can no longer read or change PM integrations. Last-known reconciliation context remains visible, but Refresh, Retry, and lifecycle mutations are hidden until authorization is restored."
              : "The current operator identity is not authorized to read or change PM integrations. No live integration state is being inferred or displayed."}
          </p>
          <Link
            href="/"
            className="mt-5 inline-flex min-h-11 items-center rounded-md border border-amber-400/40 px-3 py-2 text-sm font-medium text-amber-100 transition-colors hover:bg-amber-400/10 focus-visible:ring-2 focus-visible:ring-amber-300/70"
          >
            Return to dashboard
          </Link>
        </section>
      )}
      {error && !accessDenied && (
        <ErrorBanner
          tone={stale && data ? "warning" : "error"}
          title={stale && data ? "Showing last known integration state" : "Integration data unavailable"}
          action={(
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              aria-busy={loading}
              className="inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs font-medium text-slate-100 transition-colors hover:bg-slate-800 disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} aria-hidden="true" />
              Retry
            </button>
          )}
        >
          {stale && data
            ? `The latest integration refresh failed (${error}). Conflict and reconciliation data may be out of date.`
            : error}
        </ErrorBanner>
      )}
      {!data && !error && <p className="text-sm text-slate-400" role="status" aria-live="polite">Loading integration state…</p>}
      {data && (
        <>
          <div className="grid gap-4 md:grid-cols-4" role="region" aria-label="Integration summary">
            {[
              ["Connections", data.connections.length],
              ["Open conflicts", data.conflicts.length],
              ["Pending outbox", data.outbox.length],
              ["Reconciliation runs", data.runs.length],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
                <div className="mt-2 text-2xl font-semibold text-white">{value}</div>
              </div>
            ))}
          </div>
          <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4" aria-labelledby="integration-connections-heading">
            <h2 id="integration-connections-heading" className="text-sm font-semibold text-white">Connections</h2>
            <div className="mt-3 divide-y divide-slate-800">
              {data.connections.map((connection) => (
                <div key={String(connection.id)} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                  <div>
                    <div className="font-medium text-slate-100">{String(connection.display_name || connection.id)}</div>
                    <div className="text-xs text-slate-500">{String(connection.provider_kind)} · {String(connection.base_url)}</div>
                  </div>
                  <span className="rounded-full border border-slate-700 px-2 py-1 text-xs text-slate-300">{String(connection.status)}</span>
                </div>
              ))}
              {!data.connections.length && <p className="py-3 text-sm text-slate-500">No provider connections configured.</p>}
            </div>
          </section>
          <section className="grid gap-4 lg:grid-cols-2" aria-label="Reconciliation state">
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4" aria-labelledby="integration-conflicts-heading">
              <h2 id="integration-conflicts-heading" className="text-sm font-semibold text-amber-100">Open conflicts</h2>
              <pre aria-label="Open integration conflicts" className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-amber-200/80">{JSON.stringify(data.conflicts, null, 2)}</pre>
            </div>
            <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4" aria-labelledby="integration-reconciliation-heading">
              <h2 id="integration-reconciliation-heading" className="text-sm font-semibold text-blue-100">Outbox and reconciliation</h2>
              <pre aria-label="Integration outbox and reconciliation" className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-blue-200/80">{JSON.stringify({ outbox: data.outbox, runs: data.runs }, null, 2)}</pre>
            </div>
          </section>
          <section className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-4 space-y-4" aria-labelledby="integration-lifecycle-heading">
            <div>
              <h2 id="integration-lifecycle-heading" className="text-sm font-semibold text-cyan-100">Governed lifecycle plans</h2>
              <p className="mt-1 text-xs text-cyan-200/70">Plans are generated and persisted by the control plane. This dashboard only submits explicit operator actions.</p>
            </div>
            {!accessDenied && <div className="grid gap-2 md:grid-cols-3">
              <input aria-label="Connection UUID" className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200" placeholder="Connection UUID" value={connectionId} onChange={(event) => setConnectionId(event.target.value)} />
              <input aria-label="Binding UUID" className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200" placeholder="Binding UUID" value={bindingId} onChange={(event) => setBindingId(event.target.value)} />
              <button
                type="button"
                className="min-h-11 rounded bg-cyan-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                disabled={busy || !connectionId || !bindingId}
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    const response = await fetch("/api/integrations/pm/lifecycle-plans", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ target_type: "pm_binding", connection_id: connectionId, binding_id: bindingId, desired_binding_status: "READ_ONLY" }),
                    });
                    const payload = await response.json();
                    if (!response.ok) throw new Error(payload.detail || "Lifecycle plan generation failed");
                    setReviewedDigest(payload.plan_digest || "");
                    refresh();
                  } catch (reason) {
                    setError(reason instanceof Error ? reason.message : "Lifecycle plan generation failed");
                  } finally {
                    setBusy(false);
                  }
                }}
              >Generate READ_ONLY plan</button>
            </div>}
            {accessDenied && <p className="text-sm text-amber-200/80" role="status">Lifecycle mutation controls are hidden until authorization is restored.</p>}
            <div className="space-y-3">
              {data.lifecyclePlans.map((entry) => {
                const plan = (entry.plan || {}) as Record<string, unknown>;
                const digest = String(entry.plan_digest || "");
                const planId = String(plan.plan_id || entry.id || "");
                const status = String(entry.status || plan.status || "");
                return (
                  <div key={planId} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-slate-100">{planId}</span>
                      <span className="rounded-full border border-slate-700 px-2 py-1">{status}</span>
                    </div>
                    <div className="mt-2 break-all font-mono text-cyan-200">Digest: {digest}</div>
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-slate-400">{JSON.stringify({ operations: plan.operations, rollback_operations: plan.rollback_operations, blockers: plan.blockers, expires_at: plan.expires_at }, null, 2)}</pre>
                    {!accessDenied && <label className="mt-2 flex items-center gap-2 text-slate-400">
                      <input type="checkbox" checked={reviewedDigest === digest} onChange={(event) => setReviewedDigest(event.target.checked ? digest : "")} />
                      I reviewed this exact digest
                    </label>}
                    {!accessDenied && <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        className="min-h-11 rounded border border-emerald-700 px-2 py-1 text-emerald-300 disabled:opacity-50"
                        disabled={busy || reviewedDigest !== digest || status !== "PLANNED"}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            const response = await fetch(`/api/integrations/pm/lifecycle-plans/${planId}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan_digest: digest, reason: "Dashboard operator approval" }) });
                            const payload = await response.json();
                            if (!response.ok) throw new Error(payload.detail || "Lifecycle plan approval failed");
                            refresh();
                          } catch (reason) { setError(reason instanceof Error ? reason.message : "Lifecycle plan approval failed"); }
                          finally { setBusy(false); }
                        }}
                      >Approve</button>
                      <button
                        type="button"
                        className="min-h-11 rounded border border-amber-700 px-2 py-1 text-amber-300 disabled:opacity-50"
                        disabled={busy || reviewedDigest !== digest || status !== "APPROVED"}
                        onClick={async () => {
                          if (!window.confirm("Apply only this exact persisted lifecycle plan?")) return;
                          setBusy(true);
                          try {
                            const response = await fetch(`/api/integrations/pm/lifecycle-plans/${planId}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan_digest: digest, confirm: true }) });
                            const payload = await response.json();
                            if (!response.ok) throw new Error(payload.detail || "Lifecycle plan apply failed");
                            refresh();
                          } catch (reason) { setError(reason instanceof Error ? reason.message : "Lifecycle plan apply failed"); }
                          finally { setBusy(false); }
                        }}
                      >Apply exact plan</button>
                    </div>}
                  </div>
                );
              })}
              {!data.lifecyclePlans.length && <p className="text-xs text-slate-500">No persisted lifecycle plans.</p>}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
