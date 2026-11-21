"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type Props = { resource: string; title: string; description: string };

const SENSITIVE = /(password|secret|token|api[_-]?key|credential|cookie|refresh|totp|recovery|body|content_ref)/i;

const TERMINAL_STATE: Record<string, true> = { ARCHIVED: true, CLOSED: true, REVOKED: true, REJECTED: true, DELETED: true };

const EMPTY_COPY: Record<string, { title: string; description: string }> = {
  identities: {
    title: "No identities",
    description: "No worker email identities exist yet. Identities appear here once workers are provisioned with mailboxes.",
  },
  "mail-domains": {
    title: "No mail domains",
    description: "No mail domains are configured. Add a domain to begin verification.",
  },
  mailboxes: {
    title: "No mailboxes",
    description: "No mailboxes exist yet. Mailboxes appear here once identities are provisioned.",
  },
  "outbound-mail": {
    title: "No outbound mail",
    description: "No approval-governed outbound messages have been submitted yet.",
  },
  "mail-relay": {
    title: "No relay records",
    description: "No inbound edge health or delivery correlation records are available.",
  },
  "external-accounts": {
    title: "No external accounts",
    description: "No governed worker-owned external service accounts exist yet.",
  },
  "auth-sessions": {
    title: "No auth sessions",
    description: "No local browser sessions are recorded. Sessions appear here after operators sign in.",
  },
  "identity-approvals": {
    title: "No approvals",
    description: "No outbound or external-account approvals are pending. New requests will appear here for review.",
  },
  "identity-audit": {
    title: "No audit events",
    description: "No sanitized identity or mailbox audit events have been recorded yet.",
  },
};

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
  const [actionNotice, setActionNotice] = useState<{
    message: string;
    approvalPending: boolean;
  } | null>(null);
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
    setActionNotice(null);
    setItems([]);
    itemsRef.current = [];
    setError("");
    setStale(false);
    setAccessDenied(false);
    void load();
    return () => abortRef.current?.abort();
  }, [load]);

  const columns = Array.from(new Set(items.flatMap((item) => Object.keys(item).filter((key) => !SENSITIVE.test(key)))));

  function itemActions(item: Record<string, unknown>): { action: string; label: string }[] {
    const state = String(item.state ?? item.status ?? "").toUpperCase();
    if (resource === "identity-approvals" && String(item.state) === "PENDING") return [
      { action: "approval.approve", label: "Approve" }, { action: "approval.reject", label: "Reject" },
    ];
    if ((resource === "identities" || resource === "mailboxes") && item.worker_id && !TERMINAL_STATE[state]) {
      const actions = [];
      if (state !== "SUSPENDED") actions.push({ action: "identity.suspend", label: "Suspend" });
      actions.push({ action: "identity.archive", label: "Archive" });
      return actions;
    }
    if (resource === "external-accounts") {
      if (TERMINAL_STATE[state]) return [];
      return [
        { action: "external.rotate_credentials", label: "Rotate" },
        ...(state === "SUSPENDED" ? [] : [{ action: "external.suspend", label: "Suspend" }]),
        { action: "external.close", label: "Close" },
      ];
    }
    if (resource === "auth-sessions" && String(item.state) === "ACTIVE") return [{ action: "session.revoke", label: "Revoke" }];
    return [];
  }

  const hasActions = items.some((item) => itemActions(item).length > 0);
  const statusLabel = resource === "external-accounts" ? "External account action status" : "Identity action status";
  const emptyCopy = EMPTY_COPY[resource] ?? { title: "No records", description: "No identity records are available for this view." };

  async function performAction(item: Record<string, unknown>, action: string, label: string) {
    const confirmation = action === "external.rotate_credentials"
      ? "Request credential rotation for this external account? A human approval is required."
      : action === "external.close"
        ? "Request closure of this external account? A human approval is required."
        : `${label} this governed identity record?`;
    if (!window.confirm(confirmation)) return;
    const actionKey = `${String(item.id)}:${action}`;
    setActing(actionKey);
    setError("");
    setActionNotice(null);
    try {
      const response = await fetch(`/api/identity/${resource}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, id: item.id, worker_id: item.worker_id, service: item.service, service_category: item.service_category }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "Identity action failed");
      if (resource === "external-accounts") {
        if (action === "external.rotate_credentials" && data.rotation === "PENDING_APPROVAL") {
          setActionNotice({
            message: "Credential rotation request is awaiting human approval.",
            approvalPending: true,
          });
        } else if (action === "external.close" && data.state === "PENDING_APPROVAL") {
          setActionNotice({
            message: "Account closure request is awaiting human approval.",
            approvalPending: true,
          });
        } else if (action === "external.close" && data.state === "CLOSED") {
          setActionNotice({
            message: "This external account is already closed.",
            approvalPending: false,
          });
        } else if (action === "external.suspend" && data.state === "SUSPENDED") {
          setActionNotice({
            message: "External account suspended.",
            approvalPending: false,
          });
        } else {
          setActionNotice({ message: `${label} request completed for ${String(item.id ?? "record")}.`, approvalPending: false });
        }
      } else {
        setActionNotice({ message: `${label} request completed for ${String(item.id ?? "record")}.`, approvalPending: false });
      }
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
      {actionNotice && (
        <section
          role="status"
          aria-live="polite"
          aria-label={statusLabel}
          className="rounded-xl border border-blue-500/30 bg-blue-500/5 px-4 py-3 text-sm text-blue-100"
        >
          <p>{actionNotice.message}</p>
          {actionNotice.approvalPending && (
            <Link
              href="/identity-approvals"
              className="mt-2 inline-flex min-h-11 items-center rounded-md border border-blue-400/40 px-3 py-2 text-sm font-medium text-blue-100 transition-colors hover:bg-blue-400/10 focus-visible:ring-2 focus-visible:ring-blue-300/70"
            >
              Review identity approvals
            </Link>
          )}
        </section>
      )}
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
      {loading && items.length === 0 && !accessDenied && (
        <section role="status" aria-label={`Loading ${title} records`} className="rounded-xl border border-slate-800 bg-slate-950/50 px-5 py-6">
          <p className="flex items-center gap-2 text-sm text-slate-300">
            <Loader2 size={16} aria-hidden="true" className="animate-spin" />
            Loading {title.toLowerCase()} records…
          </p>
          <div aria-hidden="true" className="mt-4 space-y-2">
            {[0, 1, 2].map((row) => (
              <div key={row} className="h-8 animate-pulse rounded-md bg-slate-800/60" />
            ))}
          </div>
        </section>
      )}
      {!loading && !error && items.length === 0 && !accessDenied && <EmptyState title={emptyCopy.title} description={emptyCopy.description} />}
      {items.length > 0 && <section className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/50" aria-label={`${title} records`} aria-busy={loading}>
        <table className="min-w-full text-left text-sm" aria-label={`${title} records`}>
          <caption className="sr-only">{title} records</caption>
          <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500"><tr>{columns.map((column) => <th key={column} scope="col" className="px-4 py-3">{column.replaceAll("_", " ")}</th>)}{hasActions && <th scope="col" className="px-4 py-3">Actions</th>}</tr></thead>
          <tbody>{items.map((item, index) => <tr key={String(item.id ?? index)} className="border-b border-slate-900 text-slate-300 last:border-0">{columns.map((column) => <td key={column} className="max-w-xs truncate px-4 py-3 font-mono text-xs">{displayValue(item[column])}</td>)}{hasActions && <td className="px-4 py-3"><div className="flex flex-wrap gap-2">{itemActions(item).map(({ action, label }) => {
            const pending = acting === `${String(item.id)}:${action}`;
            return <button key={action} type="button" onClick={() => void performAction(item, action, label)} disabled={acting !== ""} aria-busy={pending} aria-label={`${label} ${String(item.id ?? "record")}`} className="inline-flex min-h-11 items-center gap-1.5 rounded border border-slate-700 px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-50">{pending && <Loader2 size={13} aria-hidden="true" className="animate-spin" />}{pending ? `${label}…` : label}</button>;
          })}</div></td>}</tr>)}</tbody>
        </table>
      </section>}
    </main>
  );
}
