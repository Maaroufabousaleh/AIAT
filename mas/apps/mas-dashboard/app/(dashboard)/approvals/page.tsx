"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, RefreshCw, ShieldCheck, X } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type ApprovalRequest = {
  id: string;
  action: string;
  actor_id: string;
  actor_role: string;
  privilege_level: string;
  risk_level: string;
  payload_json?: Record<string, unknown> | null;
  requested_at?: string | null;
  decision?: string;
};

type ApprovalAuditRow = {
  id: string;
  action: string;
  actor_id: string;
  actor_role: string;
  privilege_level?: string;
  risk_level?: string;
  decision: string;
  decided_by?: string | null;
  decided_at?: string | null;
  reason?: string | null;
  requested_at?: string | null;
};

type HttpError = Error & { status?: number };

const SENSITIVE_FIELD = /secret|token|password|credential|api[_-]?key|authorization|private[_-]?key/i;

function sanitizePayload(value: unknown, depth = 0): unknown {
  if (depth >= 5) return "[nested data omitted]";
  if (typeof value === "string") return value.slice(0, 500);
  if (Array.isArray(value)) return value.slice(0, 25).map((item) => sanitizePayload(item, depth + 1));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).slice(0, 40).map(([key, item]) => [
        key,
        SENSITIVE_FIELD.test(key) ? "[redacted]" : sanitizePayload(item, depth + 1),
      ]),
    );
  }
  return value;
}

function formatDate(value?: string | null): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, cache: "no-store" });
  if (!response.ok) {
    const error = new Error(`Request failed (HTTP ${response.status})`) as HttpError;
    error.status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}

export default function ApprovalsPage() {
  const [pending, setPending] = useState<ApprovalRequest[]>([]);
  const [audit, setAudit] = useState<ApprovalAuditRow[]>([]);
  const [selected, setSelected] = useState<ApprovalRequest | null>(null);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [pendingRows, auditRows] = await Promise.all([
        requestJson<ApprovalRequest[]>("/api/ceo/privileged-actions/pending"),
        requestJson<ApprovalAuditRow[]>("/api/ceo/privileged-actions/audit"),
      ]);
      setPending(Array.isArray(pendingRows) ? pendingRows : []);
      setAudit(Array.isArray(auditRows) ? auditRows : []);
      setAccessDenied(false);
    } catch (cause) {
      const status = cause instanceof Error ? (cause as HttpError).status : undefined;
      if (status === 401 || status === 403) {
        setPending([]);
        setAudit([]);
        setSelected(null);
        setAccessDenied(true);
        setError("This operator identity is not authorized to inspect or decide CEO privileged-action requests.");
      } else {
        setError(cause instanceof Error ? cause.message : "Approval data is unavailable.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const selectedAudit = useMemo(
    () => selected ? audit.find((row) => row.id === selected.id) ?? null : null,
    [audit, selected],
  );

  const decide = async (approved: boolean) => {
    if (!selected || !confirmed || !reason.trim()) return;
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      await requestJson(`/api/ceo/privileged-actions/${encodeURIComponent(selected.id)}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, reason: reason.trim() }),
      });
      setNotice(`Decision recorded: ${approved ? "approved" : "rejected"}. The governed action was not dispatched from this page.`);
      setReason("");
      setConfirmed(false);
      await refresh();
    } catch (cause) {
      const status = cause instanceof Error ? (cause as HttpError).status : undefined;
      if (status === 401 || status === 403) {
        setAccessDenied(true);
        setPending([]);
        setAudit([]);
        setSelected(null);
        setError("This operator identity is not authorized to decide privileged actions.");
      } else {
        setError(cause instanceof Error ? cause.message : "The decision could not be recorded.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const selectedIsPending = selected ? pending.some((item) => item.id === selected.id) : false;

  return (
    <main className="dashboard-page min-h-full p-6 lg:p-8" aria-label="Privileged action approvals">
      <PageHeader
        icon="shield-check"
        title="Privileged action approvals"
        description="Review CEO requests and record an audited operator decision."
        actions={(
          <button type="button" onClick={() => void refresh()} disabled={loading} aria-label="Refresh approvals" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        )}
      />

      {accessDenied ? (
        <section role="region" aria-label="Approval access status" className="mt-6 rounded-xl border border-amber-500/30 bg-amber-500/5 px-5 py-6">
          <h2 className="font-semibold text-amber-100">Approval access denied</h2>
          <p className="mt-2 text-sm text-amber-200/80">{error}</p>
        </section>
      ) : (
        <>
          {error && <div className="mt-6"><ErrorBanner tone="error" title="Approvals unavailable" action={<button type="button" onClick={() => void refresh()} disabled={loading} className="min-h-11 rounded border border-current px-3 text-xs font-medium hover:bg-white/10 disabled:opacity-50">Retry</button>}>{error}</ErrorBanner></div>}
          {notice && <p role="status" className="mt-6 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">{notice}</p>}

          <div className="mt-6 grid gap-6 xl:grid-cols-2">
            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="pending-heading" aria-busy={loading}>
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 id="pending-heading" className="flex items-center gap-2 text-sm font-semibold text-white"><ShieldCheck size={16} className="text-amber-300" /> Pending requests</h2>
                <span className="text-xs text-slate-400">{pending.length} awaiting decision</span>
              </div>
              {loading && pending.length === 0 ? <p className="text-sm text-slate-400">Loading pending requests…</p> : pending.length === 0 ? <p className="rounded-lg border border-dashed border-slate-700 p-4 text-sm text-slate-400">No privileged-action requests are waiting for review.</p> : (
                <ul className="space-y-3" aria-label="Pending privileged action requests">
                  {pending.map((item) => (
                    <li key={item.id} className={`rounded-lg border p-3 ${selected?.id === item.id ? "border-blue-400/60 bg-blue-500/5" : "border-slate-800 bg-slate-950/50"}`}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="break-all text-sm font-medium text-white">{item.action}</h3>
                          <p className="mt-1 text-xs text-slate-400">Requested by {item.actor_id} ({item.actor_role}) · {item.risk_level} risk</p>
                          <p className="mt-1 text-xs text-slate-500">{formatDate(item.requested_at)}</p>
                        </div>
                        <button type="button" onClick={() => { setSelected(item); setReason(""); setConfirmed(false); setNotice(""); }} aria-pressed={selected?.id === item.id} className="min-h-11 rounded border border-slate-700 px-3 py-2 text-xs font-medium text-blue-200 hover:bg-slate-800">View details</button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="audit-heading" aria-busy={loading}>
              <div className="mb-4 flex items-center justify-between gap-3"><h2 id="audit-heading" className="text-sm font-semibold text-white">Recent decision audit</h2><span className="text-xs text-slate-400">latest 100 records</span></div>
              {loading && audit.length === 0 ? <p className="text-sm text-slate-400">Loading audit records…</p> : audit.length === 0 ? <p className="rounded-lg border border-dashed border-slate-700 p-4 text-sm text-slate-400">No privileged-action audit records are available.</p> : (
                <div className="max-h-[34rem] space-y-2 overflow-y-auto" role="list" aria-label="Privileged action audit records">
                  {audit.map((row) => (
                    <article key={row.id} role="listitem" className="rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-xs">
                      <div className="flex flex-wrap items-center justify-between gap-2"><span className="break-all font-medium text-slate-200">{row.action}</span><span className={row.decision === "approved" ? "text-emerald-300" : row.decision === "rejected" ? "text-rose-300" : "text-amber-300"}>{row.decision.replace(/_/g, " ")}</span></div>
                      <p className="mt-1 text-slate-400">Requested by {row.actor_id} · decided by {row.decided_by || "not recorded"}</p>
                      <p className="mt-1 text-slate-500">Requested {formatDate(row.requested_at)} · decided {formatDate(row.decided_at)}</p>
                      {row.reason && <p className="mt-2 break-words text-slate-300">Reason: {row.reason}</p>}
                      <p className="mt-1 break-all font-mono text-[10px] text-slate-600">{row.id}</p>
                    </article>
                  ))}
                </div>
              )}
            </section>

            {selected && (
              <section className="rounded-xl border border-blue-400/30 bg-slate-900/80 p-5 xl:col-span-2" aria-labelledby="request-details-heading">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div><h2 id="request-details-heading" className="text-base font-semibold text-white">Request details</h2><p className="mt-1 break-all font-mono text-xs text-slate-500">{selected.id}</p></div>
                  <button type="button" onClick={() => setSelected(null)} className="min-h-11 rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800">Close details</button>
                </div>
                <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div><dt className="text-xs text-slate-500">Action</dt><dd className="mt-1 break-all text-slate-100">{selected.action}</dd></div>
                  <div><dt className="text-xs text-slate-500">Requested by</dt><dd className="mt-1 text-slate-100">{selected.actor_id} · {selected.actor_role}</dd></div>
                  <div><dt className="text-xs text-slate-500">Privilege / risk</dt><dd className="mt-1 text-slate-100">{selected.privilege_level} · {selected.risk_level}</dd></div>
                  <div><dt className="text-xs text-slate-500">Requested</dt><dd className="mt-1 text-slate-100">{formatDate(selected.requested_at)}</dd></div>
                </dl>
                <div className="mt-4"><h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Request payload</h3><pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">{JSON.stringify(sanitizePayload(selected.payload_json ?? {}), null, 2)}</pre></div>
                {selectedAudit && <p role="status" className="mt-4 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm text-slate-200">Current audit decision: {selectedAudit.decision} · {selectedAudit.decided_by || "no decider recorded"} · {selectedAudit.reason || "no reason recorded"}</p>}
                {selectedIsPending ? (
                  <div className="mt-5 border-t border-slate-800 pt-5">
                    <p className="text-sm text-amber-200">This page records an audited decision only. It does not dispatch the governed action.</p>
                    <label htmlFor="approval-reason" className="mt-4 block text-xs font-medium text-slate-300">Decision reason (required)</label>
                    <textarea id="approval-reason" value={reason} onChange={(event) => setReason(event.target.value.slice(0, 1000))} rows={3} maxLength={1000} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400" placeholder="Record why this request should be approved or rejected." />
                    <label className="mt-3 flex min-h-11 items-center gap-3 text-sm text-slate-300"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="size-4 accent-blue-500" />I reviewed this request and intend to record a decision.</label>
                    <div className="mt-4 flex flex-wrap gap-3">
                      <button type="button" onClick={() => void decide(true)} disabled={!confirmed || !reason.trim() || submitting} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-50"><Check size={15} /> Approve request</button>
                      <button type="button" onClick={() => void decide(false)} disabled={!confirmed || !reason.trim() || submitting} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-rose-500/50 px-4 py-2 text-sm font-medium text-rose-200 hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-50"><X size={15} /> Reject request</button>
                    </div>
                  </div>
                ) : <p className="mt-5 border-t border-slate-800 pt-4 text-sm text-slate-400">This request is no longer pending. Its latest recorded state appears in the audit.</p>}
              </section>
            )}
          </div>
        </>
      )}
    </main>
  );
}
