"use client";

import { useEffect, useState } from "react";
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

  const refresh = () => {
    fetch("/api/integrations/pm", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Integration API failed");
        setData(payload);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Integration API failed"));
  };

  useEffect(() => {
    let cancelled = false;
    fetch("/api/integrations/pm", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Integration API failed");
        if (!cancelled) setData(payload);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Integration API failed");
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <main className="p-6 space-y-6">
      <PageHeader
        title="PM integrations"
        description="Provider connections, synchronization health, reconciliation runs, and conflicts."
      />
      {error && <ErrorBanner>{error}</ErrorBanner>}
      {!data && !error && <p className="text-sm text-slate-400">Loading integration state…</p>}
      {data && (
        <>
          <div className="grid gap-4 md:grid-cols-4">
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
          <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
            <h2 className="text-sm font-semibold text-white">Connections</h2>
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
          <section className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
              <h2 className="text-sm font-semibold text-amber-100">Open conflicts</h2>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-amber-200/80">{JSON.stringify(data.conflicts, null, 2)}</pre>
            </div>
            <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4">
              <h2 className="text-sm font-semibold text-blue-100">Outbox and reconciliation</h2>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-blue-200/80">{JSON.stringify({ outbox: data.outbox, runs: data.runs }, null, 2)}</pre>
            </div>
          </section>
          <section className="rounded-xl border border-violet-500/20 bg-violet-500/5 p-4 space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-violet-100">Governed lifecycle plans</h2>
              <p className="mt-1 text-xs text-violet-200/70">Plans are generated and persisted by the control plane. This dashboard only submits explicit operator actions.</p>
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              <input className="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200" placeholder="Connection UUID" value={connectionId} onChange={(event) => setConnectionId(event.target.value)} />
              <input className="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200" placeholder="Binding UUID" value={bindingId} onChange={(event) => setBindingId(event.target.value)} />
              <button
                className="rounded bg-violet-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
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
            </div>
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
                    <div className="mt-2 break-all font-mono text-violet-200">Digest: {digest}</div>
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-slate-400">{JSON.stringify({ operations: plan.operations, rollback_operations: plan.rollback_operations, blockers: plan.blockers, expires_at: plan.expires_at }, null, 2)}</pre>
                    <label className="mt-2 flex items-center gap-2 text-slate-400">
                      <input type="checkbox" checked={reviewedDigest === digest} onChange={(event) => setReviewedDigest(event.target.checked ? digest : "")} />
                      I reviewed this exact digest
                    </label>
                    <div className="mt-2 flex gap-2">
                      <button
                        className="rounded border border-emerald-700 px-2 py-1 text-emerald-300 disabled:opacity-50"
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
                        className="rounded border border-amber-700 px-2 py-1 text-amber-300 disabled:opacity-50"
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
                    </div>
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
