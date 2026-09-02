"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type Props = { resource: string; title: string; description: string };

const SENSITIVE = /(password|secret|token|api[_-]?key|credential|cookie|refresh|totp|recovery|body|content_ref)/i;

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function IdentityResourcePage({ resource, title, description }: Props) {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState("");
  const [stale, setStale] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const itemsRef = useRef<Record<string, unknown>[]>([]);
  const requestIdRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError((previous) => (itemsRef.current.length > 0 ? previous : ""));
    try {
      const response = await fetch(`/api/identity/${resource}`, { cache: "no-store", signal: controller.signal });
      if (response.status === 401 || response.status === 403) {
        if (requestId !== requestIdRef.current) return;
        setAccessDenied(true);
        setError("This operator identity is not authorized to read this identity resource.");
        setStale(itemsRef.current.length > 0);
        return;
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "Identity data is unavailable");
      if (requestId !== requestIdRef.current) return;
      const nextItems = Array.isArray(data.items) ? data.items : [];
      itemsRef.current = nextItems;
      setItems(nextItems);
      setError("");
      setStale(false);
      setAccessDenied(false);
    } catch (cause) {
      if (controller.signal.aborted || requestId !== requestIdRef.current) return;
      setAccessDenied(false);
      setError(cause instanceof Error ? cause.message : "Identity data is unavailable");
      setStale(true);
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [resource]);

  useEffect(() => {
    void load();
    return () => {
      requestIdRef.current += 1;
      abortRef.current?.abort();
    };
  }, [load]);
  const columns = Array.from(new Set(items.flatMap((item) => Object.keys(item).filter((key) => !SENSITIVE.test(key))))).slice(0, 10);

  function itemActions(item: Record<string, unknown>): { action: string; label: string }[] {
    if (resource === "identity-approvals" && String(item.state) === "PENDING") return [
      { action: "approval.approve", label: "Approve" }, { action: "approval.reject", label: "Reject" },
    ];
    if ((resource === "identities" || resource === "mailboxes") && item.worker_id) return [
      { action: "identity.suspend", label: "Suspend" }, { action: "identity.archive", label: "Archive" },
    ];
    if (resource === "external-accounts") return [
      { action: "external.rotate_credentials", label: "Rotate" },
      { action: "external.suspend", label: "Suspend" }, { action: "external.close", label: "Close" },
    ];
    if (resource === "auth-sessions" && String(item.state) === "ACTIVE") return [{ action: "session.revoke", label: "Revoke" }];
    return [];
  }

  async function performAction(item: Record<string, unknown>, action: string, label: string) {
    if (!window.confirm(`${label} this governed identity record?`)) return;
    const actionKey = `${String(item.id)}:${action}`;
    setActing(actionKey);
    setError("");
    try {
      const response = await fetch(`/api/identity/${resource}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, id: item.id, worker_id: item.worker_id, service: item.service, service_category: item.service_category }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "Identity action failed");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Identity action failed");
    } finally {
      setActing("");
    }
  }

  return (
    <main className="space-y-6" aria-label={title} aria-busy={loading}>
      <div role="status" aria-live="polite" className="sr-only">
        {loading
          ? `Loading ${title.toLowerCase()} records`
          : accessDenied
            ? `${title} access denied`
            : `${title} records loaded`}
      </div>
      <PageHeader title={title} description={description} actions={
        !accessDenied ? (
          <button type="button" onClick={() => void load()} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800" disabled={loading} aria-busy={loading}>
            <RefreshCw size={15} aria-hidden="true" className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        ) : undefined
      } />
      <section className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-100 flex gap-2" aria-label="Identity resource notice">
        <ShieldCheck size={17} aria-hidden="true" className="mt-0.5 flex-none" />
        Metadata only: credential values, cookies, tokens, and message bodies are never displayed.
      </section>
      {accessDenied && (
        <section
          role="region"
          aria-label="Identity access status"
          className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-5 py-6 shadow-sm shadow-amber-950/10"
        >
          <h2 className="text-base font-semibold text-amber-100">Identity access denied</h2>
          <p className="mt-2 max-w-2xl text-sm text-amber-200/80">
            {items.length > 0
              ? "The current operator identity can no longer read this resource. Showing the last known metadata-only records; no new identity state is being inferred."
              : "The current operator identity is not authorized to read this resource. No identity records are being inferred or displayed."}
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
          tone={stale && items.length > 0 ? "warning" : "error"}
          title={stale && items.length > 0 ? "Showing last known records" : "Identity data unavailable"}
          action={
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              aria-busy={loading}
              className="inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs font-medium text-slate-100 transition-colors hover:bg-slate-800 disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} aria-hidden="true" />
              Retry
            </button>
          }
        >
          {stale && items.length > 0
            ? `The latest refresh failed (${error}). The table remains usable but may be out of date.`
            : error}
        </ErrorBanner>
      )}
      {!loading && !error && items.length === 0 && <EmptyState title="No records" description="No identity records are available for this view." />}
      {items.length > 0 && <section className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/50" aria-label={`${title} records`} aria-busy={loading}>
        <table className="min-w-full text-left text-sm">
          <caption className="sr-only">{title} records</caption>
          <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500"><tr>{columns.map((column) => <th key={column} scope="col" className="px-4 py-3">{column.replaceAll("_", " ")}</th>)}<th scope="col" className="px-4 py-3">Actions</th></tr></thead>
          <tbody>{items.map((item, index) => <tr key={String(item.id ?? index)} className="border-b border-slate-900 text-slate-300 last:border-0">{columns.map((column) => <td key={column} className="max-w-xs truncate px-4 py-3 font-mono text-xs">{displayValue(item[column])}</td>)}<td className="px-4 py-3"><div className="flex flex-wrap gap-2">{itemActions(item).map(({ action, label }) => <button key={action} type="button" onClick={() => void performAction(item, action, label)} disabled={acting === `${String(item.id)}:${action}`} aria-label={`${label} ${String(item.id ?? "record")}`} className="inline-flex min-h-11 items-center rounded border border-slate-700 px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-50">{label}</button>)}</div></td></tr>)}</tbody>
        </table>
      </section>}
    </main>
  );
}
