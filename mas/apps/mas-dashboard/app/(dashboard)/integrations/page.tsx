"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { RefreshCw, Search } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type IntegrationData = {
  connections: Array<Record<string, unknown>>;
  conflicts: Array<Record<string, unknown>>;
  outbox: Array<Record<string, unknown>>;
  runs: Array<Record<string, unknown>>;
  lifecyclePlans: Array<Record<string, unknown>>;
};

type CanaryPlanDetails = Record<string, unknown> & {
  plan?: Record<string, unknown>;
};

class IntegrationApiError extends Error {
  constructor(readonly status: number) {
    super(`Integration request failed (HTTP ${status}).`);
    this.name = "IntegrationApiError";
  }
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.headers ?? {}),
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!response.ok) throw new IntegrationApiError(response.status);
  return (await response.json()) as T;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : fallback;
}

function dateTime(value: unknown): string {
  if (typeof value !== "string") return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function trustedActorMappings(entry: Record<string, unknown>): Array<Record<string, unknown>> {
  const plan = record(entry.plan);
  const gateResults = record(plan.gate_results);
  const activeScope = record(gateResults.active_binding_scope);
  return Array.isArray(activeScope.trusted_actor_mappings)
    ? activeScope.trusted_actor_mappings.map(record)
    : [];
}

export default function IntegrationsPage() {
  const [data, setData] = useState<IntegrationData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const [bindingId, setBindingId] = useState("");
  const [connectionFilterId, setConnectionFilterId] = useState("");
  const [resolutionNotes, setResolutionNotes] = useState<Record<string, string>>({});
  const [reviewedDigest, setReviewedDigest] = useState("");
  const [canaryPlanId, setCanaryPlanId] = useState("");
  const [canaryPlan, setCanaryPlan] = useState<CanaryPlanDetails | null>(null);
  const [canaryError, setCanaryError] = useState<string | null>(null);
  const [canaryLoading, setCanaryLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [hasReadContext, setHasReadContext] = useState(false);
  const hasData = useRef(false);

  const refresh = useCallback(async (filterConnectionId = "") => {
    setLoading(true);
    setError(null);
    setNotice(null);
    const query = filterConnectionId
      ? `?connection_id=${encodeURIComponent(filterConnectionId)}`
      : "";
    try {
      const payload = await requestJson<IntegrationData>(`/api/integrations/pm${query}`);
      setData(payload);
      hasData.current = true;
      setStale(false);
      setAccessDenied(false);
      setHasReadContext(true);
    } catch (reason) {
      if (reason instanceof IntegrationApiError && [401, 403].includes(reason.status)) {
        setAccessDenied(true);
        setError("The current operator identity is not authorized to read or change PM integrations.");
        setStale(hasData.current);
        return;
      }
      setAccessDenied(false);
      setError(reason instanceof IntegrationApiError
        ? reason.status === 503
          ? "The PM integration service is unavailable."
          : reason.status === 409
            ? "Integration state changed while loading. Refresh before taking an action."
            : `Integration data could not be loaded (HTTP ${reason.status}).`
        : "Integration data could not be loaded.");
      setStale(hasData.current);
    } finally {
      setLoading(false);
    }
  }, []);

  const reportActionFailure = useCallback((reason: unknown, action: string) => {
    if (reason instanceof IntegrationApiError && [401, 403].includes(reason.status)) {
      setAccessDenied(true);
      setStale(hasData.current);
      setError("The current operator identity is no longer authorized. Refresh and mutation controls are hidden.");
      return;
    }
    if (reason instanceof IntegrationApiError && reason.status === 409) {
      setStale(true);
      setError(`${action} was rejected because the integration state changed. Refresh before trying again.`);
      return;
    }
    setError(reason instanceof IntegrationApiError
      ? `${action} failed (HTTP ${reason.status}).`
      : `${action} failed.`);
  }, []);

  const submitMutation = async (
    path: string,
    payload: Record<string, unknown>,
    action: string,
    onSuccess?: (result: Record<string, unknown>) => void,
  ) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await requestJson<Record<string, unknown>>(path, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      onSuccess?.(result);
      await refresh(connectionFilterId);
      setNotice(`${action} completed.`);
    } catch (reason) {
      reportActionFailure(reason, action);
    } finally {
      setBusy(false);
    }
  };

  const resolveConflict = async (conflictId: string, status: "RESOLVED" | "IGNORED") => {
    const reason = (resolutionNotes[conflictId] ?? "").trim();
    if (!reason || accessDenied || stale || loading) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await requestJson<Record<string, unknown>>(
        `/api/integrations/pm/conflicts/${encodeURIComponent(conflictId)}/resolve`,
        {
          method: "POST",
          body: JSON.stringify({ status, resolution: { operator_note: reason } }),
        },
      );
      setData((current) => current ? {
        ...current,
        conflicts: current.conflicts.map((conflict) =>
          conflict.id === conflictId ? { ...conflict, ...updated } : conflict,
        ),
      } : current);
      setResolutionNotes((current) => ({ ...current, [conflictId]: "" }));
      await refresh(connectionFilterId);
      setNotice(`Conflict disposition recorded as ${status}.`);
    } catch (reason) {
      reportActionFailure(reason, "Conflict disposition");
    } finally {
      setBusy(false);
    }
  };

  const lookupCanary = async () => {
    const planId = canaryPlanId.trim();
    if (!UUID_PATTERN.test(planId) || accessDenied || canaryLoading) return;
    setCanaryLoading(true);
    setCanaryError(null);
    setCanaryPlan(null);
    try {
      const result = await requestJson<CanaryPlanDetails>(
        `/api/integrations/pm/inbound-canaries/${encodeURIComponent(planId)}`,
      );
      setCanaryPlan(result);
    } catch (reason) {
      if (reason instanceof IntegrationApiError && [401, 403].includes(reason.status)) {
        setAccessDenied(true);
        setStale(hasData.current);
        setCanaryError("The current operator identity is no longer authorized to read canary plans.");
      } else if (reason instanceof IntegrationApiError && reason.status === 404) {
        setCanaryError("No canary plan was found for that ID.");
      } else {
        setCanaryError(reason instanceof IntegrationApiError
          ? `Canary details could not be loaded (HTTP ${reason.status}).`
          : "Canary details could not be loaded.");
      }
    } finally {
      setCanaryLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, [refresh]);

  const mutationBlocked = accessDenied || stale || loading || busy;

  return (
    <main className="space-y-6 p-6" aria-label="PM integrations" aria-busy={loading}>
      <PageHeader
        title="PM integrations"
        description="Provider connections, mappings, canary evidence, synchronization health, and conflict disposition."
        actions={!accessDenied ? (
          <button
            type="button"
            onClick={() => void refresh(connectionFilterId)}
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
              ? "The current operator identity can no longer read or change PM integrations. Last-known reconciliation context remains visible, but Refresh, Retry, and mutation controls are hidden until authorization is restored."
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
              onClick={() => void refresh(connectionFilterId)}
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
            ? `The latest integration refresh failed (${error}). Disposition and lifecycle actions are disabled until a fresh read succeeds.`
            : error}
        </ErrorBanner>
      )}
      {notice && <p role="status" className="rounded border border-emerald-700/50 bg-emerald-900/20 px-3 py-2 text-sm text-emerald-200">{notice}</p>}
      {!data && !error && <p className="text-sm text-slate-400" role="status" aria-live="polite">Loading integration state…</p>}
      {data && (
        <>
          <section className="grid gap-4 md:grid-cols-4" role="region" aria-label="Integration summary">
            {[
              ["Connections", data.connections.length],
              ["Open conflicts", data.conflicts.filter((item) => text(item.status).toUpperCase() === "OPEN").length],
              ["Due pending outbox", data.outbox.length],
              ["Reconciliation runs", data.runs.length],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
                <div className="mt-2 text-2xl font-semibold text-white">{value}</div>
              </div>
            ))}
          </section>

          <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4" aria-labelledby="integration-connections-heading">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 id="integration-connections-heading" className="text-sm font-semibold text-white">Connections</h2>
                <p className="mt-1 text-xs text-slate-400">Select a connection to narrow its conflicts, pending outbox, reconciliation runs, and plans.</p>
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                Connection filter
                <select
                  aria-label="Filter by connection"
                  className="min-h-11 min-w-56 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200"
                  value={connectionFilterId}
                  disabled={loading || accessDenied}
                  onChange={(event) => {
                    const value = event.target.value;
                    setConnectionFilterId(value);
                    void refresh(value);
                  }}
                >
                  <option value="">All connections</option>
                  {data.connections.map((connection) => (
                    <option key={text(connection.id)} value={text(connection.id, "")}>{text(connection.display_name || connection.id)}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="mt-3 divide-y divide-slate-800">
              {data.connections.map((connection) => (
                <div key={text(connection.id)} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                  <div>
                    <div className="font-medium text-slate-100">{text(connection.display_name || connection.id)}</div>
                    <div className="text-xs text-slate-500">{text(connection.provider_kind)} · {text(connection.base_url)}</div>
                    <div className="text-xs text-slate-500">Connection ID: <span className="font-mono">{text(connection.id)}</span> · revision {text(connection.revision)}</div>
                  </div>
                  <span className="rounded-full border border-slate-700 px-2 py-1 text-xs text-slate-300">{text(connection.status)}</span>
                </div>
              ))}
              {!data.connections.length && <p className="py-3 text-sm text-slate-500">No provider connections configured.</p>}
            </div>
          </section>

          <section className="grid gap-4 xl:grid-cols-2" aria-label="Reconciliation state">
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4" aria-labelledby="integration-conflicts-heading">
              <h2 id="integration-conflicts-heading" className="text-sm font-semibold text-amber-100">Open conflicts and disposition</h2>
              <div className="mt-3 space-y-3">
                {data.conflicts.map((conflict) => {
                  const conflictId = text(conflict.id, "unknown-conflict");
                  const resolutionNote = resolutionNotes[conflictId] ?? "";
                  return (
                    <article key={conflictId} aria-label={`Conflict ${conflictId}`} className="rounded border border-amber-500/20 bg-slate-950/60 p-3 text-xs text-amber-100/90">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-mono font-medium">{conflictId}</span>
                        <span className="rounded border border-amber-500/30 px-2 py-1">{text(conflict.status)}</span>
                      </div>
                      <p className="mt-2 text-sm text-amber-50">{text(conflict.reason, "No conflict reason recorded.")}</p>
                      <p className="mt-1 text-slate-400">{text(conflict.object_type)} · canonical {text(conflict.aiat_object_id)} · external {text(conflict.external_id)}</p>
                      {Boolean(conflict.canonical_snapshot || conflict.external_snapshot) && (
                        <details className="mt-2 rounded border border-slate-800 p-2">
                          <summary className="cursor-pointer text-slate-300">Compare retained snapshots</summary>
                          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-slate-400">{JSON.stringify({ canonical: conflict.canonical_snapshot, external: conflict.external_snapshot }, null, 2)}</pre>
                        </details>
                      )}
                      {!accessDenied && (
                        <div className="mt-3 space-y-2">
                          <label className="block text-slate-300" htmlFor={`conflict-note-${conflictId}`}>Operator disposition note</label>
                          <textarea
                            id={`conflict-note-${conflictId}`}
                            aria-label={`Disposition note for ${conflictId}`}
                            className="min-h-20 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-100 disabled:opacity-50"
                            maxLength={1000}
                            placeholder="Record the reason for this decision"
                            value={resolutionNote}
                            disabled={mutationBlocked || text(conflict.status).toUpperCase() !== "OPEN"}
                            onChange={(event) => setResolutionNotes((current) => ({ ...current, [conflictId]: event.target.value }))}
                          />
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              className="min-h-11 rounded border border-emerald-700 px-3 py-2 text-emerald-300 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={mutationBlocked || !resolutionNote.trim() || text(conflict.status).toUpperCase() !== "OPEN"}
                              onClick={() => void resolveConflict(conflictId, "RESOLVED")}
                            >Mark resolved</button>
                            <button
                              type="button"
                              className="min-h-11 rounded border border-slate-600 px-3 py-2 text-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={mutationBlocked || !resolutionNote.trim() || text(conflict.status).toUpperCase() !== "OPEN"}
                              onClick={() => void resolveConflict(conflictId, "IGNORED")}
                            >Ignore conflict</button>
                          </div>
                        </div>
                      )}
                    </article>
                  );
                })}
                {!data.conflicts.length && <p className="py-3 text-sm text-slate-500">No open conflicts for this connection filter.</p>}
                {stale && data.conflicts.length > 0 && <p role="status" className="text-xs text-amber-200">Refresh before recording a disposition.</p>}
              </div>
            </div>

            <div className="space-y-4 rounded-xl border border-blue-500/20 bg-blue-500/5 p-4" aria-labelledby="integration-reconciliation-heading">
              <div>
                <h2 id="integration-reconciliation-heading" className="text-sm font-semibold text-blue-100">Outbox and reconciliation</h2>
                <p className="mt-1 text-xs text-blue-200/70">The current read API lists due PENDING outbox rows. It does not list DEAD_LETTER rows, so this view does not offer an unverified disposition action.</p>
              </div>
              <div className="space-y-2" aria-label="Pending integration outbox">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Due pending outbox</h3>
                {data.outbox.map((entry) => {
                  const outboxId = text(entry.id, "unknown-outbox-event");
                  return (
                    <article key={outboxId} aria-label={`Outbox event ${outboxId}`} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-mono">{outboxId}</span>
                        <span className="rounded border border-blue-500/30 px-2 py-1">{text(entry.status, "PENDING")}</span>
                      </div>
                      <p className="mt-2 text-sm text-slate-100">{text(entry.operation)} · {text(entry.aggregate_type)} {text(entry.aggregate_id)}</p>
                      <p className="mt-1 text-slate-400">Attempt {text(entry.attempts, "0")} · created {dateTime(entry.created_at)}</p>
                      <p className="text-slate-400">Next attempt {dateTime(entry.next_attempt_at)}</p>
                    </article>
                  );
                })}
                {!data.outbox.length && <p className="text-sm text-slate-500">No due pending outbox events.</p>}
              </div>
              <div className="space-y-2" aria-label="Integration reconciliation runs">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Reconciliation runs</h3>
                {data.runs.map((run) => {
                  const runId = text(run.id, "unknown-reconciliation-run");
                  const counts = record(run.counts);
                  return (
                    <article key={runId} aria-label={`Reconciliation run ${runId}`} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-mono">{runId}</span>
                        <span className="rounded border border-slate-700 px-2 py-1">{text(run.status)}</span>
                      </div>
                      <p className="mt-2 text-slate-400">Connection {text(run.connection_id)} · binding {text(run.binding_id)}</p>
                      <p className="mt-1">Started {dateTime(run.created_at)} · finished {dateTime(run.completed_at)}</p>
                      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-slate-400 sm:grid-cols-4">
                        {(["created", "updated", "drift", "conflicts"] as const).map((name) => (
                          <div key={name}><dt className="capitalize">{name}</dt><dd className="font-semibold text-slate-200">{text(counts[name], "0")}</dd></div>
                        ))}
                      </dl>
                    </article>
                  );
                })}
                {!data.runs.length && <p className="text-sm text-slate-500">No reconciliation runs recorded.</p>}
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-violet-500/20 bg-violet-500/5 p-4 space-y-4" aria-labelledby="integration-canary-heading">
            <div>
              <h2 id="integration-canary-heading" className="text-sm font-semibold text-violet-100">Inbound canary inspection</h2>
              <p className="mt-1 text-xs text-violet-200/70">Read an existing canary plan by ID. This view is read-only and does not contact a provider.</p>
            </div>
            {!accessDenied && (
              <div className="flex flex-wrap gap-2">
                <label className="sr-only" htmlFor="canary-plan-id">Canary plan UUID</label>
                <input
                  id="canary-plan-id"
                  aria-label="Canary plan UUID"
                  className="min-h-11 min-w-72 flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200"
                  placeholder="Canary plan UUID"
                  value={canaryPlanId}
                  onChange={(event) => setCanaryPlanId(event.target.value)}
                />
                <button
                  type="button"
                  className="inline-flex min-h-11 items-center gap-2 rounded border border-violet-700 px-3 py-2 text-xs font-semibold text-violet-200 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={!UUID_PATTERN.test(canaryPlanId.trim()) || canaryLoading}
                  aria-busy={canaryLoading}
                  onClick={() => void lookupCanary()}
                >
                  <Search size={14} aria-hidden="true" />
                  {canaryLoading ? "Loading…" : "Inspect canary"}
                </button>
              </div>
            )}
            {canaryError && <p role="status" className="text-sm text-amber-200">{canaryError}</p>}
            {canaryPlan && (() => {
              const plan = record(canaryPlan.plan);
              return (
                <article aria-label="Canary plan details" className="rounded border border-violet-500/20 bg-slate-950/60 p-3 text-xs text-slate-300">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-slate-100">{text(plan.plan_id || canaryPlanId)}</span>
                    <span className="rounded border border-violet-500/30 px-2 py-1">{text(canaryPlan.status)}</span>
                  </div>
                  <p className="mt-2">Digest valid: {canaryPlan.digest_valid === true ? "yes" : canaryPlan.digest_valid === false ? "no" : "unknown"} · accepted commands {text(canaryPlan.accepted_command_count, "0")}</p>
                  <dl className="mt-2 grid gap-1 sm:grid-cols-2">
                    <div><dt className="text-slate-500">Connection</dt><dd className="font-mono">{text(plan.connection_id)}</dd></div>
                    <div><dt className="text-slate-500">Binding</dt><dd className="font-mono">{text(plan.binding_id)}</dd></div>
                    <div><dt className="text-slate-500">Priority change</dt><dd>{text(plan.current_priority)} → {text(plan.target_priority)}</dd></div>
                    <div><dt className="text-slate-500">Expires</dt><dd>{dateTime(plan.expires_at)}</dd></div>
                  </dl>
                  <details className="mt-3 rounded border border-slate-800 p-2">
                    <summary className="cursor-pointer text-slate-300">Inspect persisted gates and operations</summary>
                    <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-slate-400">{JSON.stringify({ gate_results: plan.gate_results, operations: plan.operations, rollback_operations: plan.rollback_operations }, null, 2)}</pre>
                  </details>
                </article>
              );
            })()}
            {!accessDenied && <p className="text-xs text-slate-500">The current API has no canary-plan collection endpoint; enter a known plan ID to inspect it.</p>}
          </section>

          <section className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-4 space-y-4" aria-labelledby="integration-lifecycle-heading">
            <div>
              <h2 id="integration-lifecycle-heading" className="text-sm font-semibold text-cyan-100">Governed lifecycle plans</h2>
              <p className="mt-1 text-xs text-cyan-200/70">Plans are generated and persisted by the control plane. Mutations require a fresh read, an exact digest review, and operator approval.</p>
            </div>
            {!accessDenied && <div className="grid gap-2 md:grid-cols-3">
              <input aria-label="Connection UUID" className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200 disabled:opacity-50" placeholder="Connection UUID" value={connectionId} disabled={mutationBlocked} onChange={(event) => setConnectionId(event.target.value)} />
              <input aria-label="Binding UUID" className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-200 disabled:opacity-50" placeholder="Binding UUID" value={bindingId} disabled={mutationBlocked} onChange={(event) => setBindingId(event.target.value)} />
              <button
                type="button"
                className="min-h-11 rounded bg-cyan-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                disabled={mutationBlocked || !UUID_PATTERN.test(connectionId) || !UUID_PATTERN.test(bindingId)}
                onClick={() => void submitMutation(
                  "/api/integrations/pm/lifecycle-plans",
                  { target_type: "pm_binding", connection_id: connectionId, binding_id: bindingId, desired_binding_status: "READ_ONLY" },
                  "Lifecycle plan generation",
                  (result) => setReviewedDigest(text(result.plan_digest, "")),
                )}
              >Generate READ_ONLY plan</button>
            </div>}
            {accessDenied && <p className="text-sm text-amber-200/80" role="status">Lifecycle mutation controls are hidden until authorization is restored.</p>}
            <div className="space-y-3">
              {data.lifecyclePlans.map((entry) => {
                const plan = record(entry.plan);
                const digest = text(entry.plan_digest, "");
                const planId = text(plan.plan_id || entry.id, "unknown-plan");
                const status = text(entry.status || plan.status, "unknown");
                const actorMappings = trustedActorMappings(entry);
                return (
                  <article key={planId} aria-label={`Lifecycle plan ${planId}`} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-slate-100">{planId}</span>
                      <span className="rounded-full border border-slate-700 px-2 py-1">{status}</span>
                    </div>
                    <div className="mt-2 break-all font-mono text-cyan-200">Digest: {digest}</div>
                    <p className="mt-1 text-slate-400">Target {text(plan.target_type)} · connection {text(plan.connection_id)} · binding {text(plan.binding_id)}</p>
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-slate-400">{JSON.stringify({ operations: plan.operations, rollback_operations: plan.rollback_operations, blockers: plan.blockers, expires_at: plan.expires_at }, null, 2)}</pre>
                    {actorMappings.length > 0 && (
                      <div role="region" aria-label="Trusted actor mapping snapshot" className="mt-3 rounded border border-slate-800 p-2">
                        <h3 className="font-semibold text-slate-200">Trusted actor mapping snapshot</h3>
                        <p className="mt-1 text-slate-500">Immutable scope captured when this lifecycle plan was created; it is not a live mapping list.</p>
                        <ul className="mt-2 space-y-1">
                          {actorMappings.map((mapping) => (
                            <li key={text(mapping.mapping_id)} className="font-mono">
                              {text(mapping.mapping_id)} · {text(mapping.status)} · scopes {Array.isArray(mapping.authorized_scopes) ? mapping.authorized_scopes.map(String).join(", ") || "none" : "unknown"}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {!accessDenied && <label className="mt-2 flex items-center gap-2 text-slate-400">
                      <input type="checkbox" checked={reviewedDigest === digest} disabled={mutationBlocked || status !== "PLANNED"} onChange={(event) => setReviewedDigest(event.target.checked ? digest : "")} />
                      I reviewed this exact digest
                    </label>}
                    {!accessDenied && <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        className="min-h-11 rounded border border-emerald-700 px-3 py-2 text-emerald-300 disabled:opacity-50"
                        disabled={mutationBlocked || reviewedDigest !== digest || status !== "PLANNED"}
                        onClick={() => void submitMutation(
                          `/api/integrations/pm/lifecycle-plans/${encodeURIComponent(planId)}/approve`,
                          { plan_digest: digest, reason: "Dashboard operator approval" },
                          "Lifecycle plan approval",
                        )}
                      >Approve</button>
                      <button
                        type="button"
                        className="min-h-11 rounded border border-amber-700 px-3 py-2 text-amber-300 disabled:opacity-50"
                        disabled={mutationBlocked || reviewedDigest !== digest || status !== "APPROVED"}
                        onClick={() => {
                          if (!window.confirm("Apply only this exact persisted lifecycle plan?")) return;
                          void submitMutation(
                            `/api/integrations/pm/lifecycle-plans/${encodeURIComponent(planId)}/apply`,
                            { plan_digest: digest, confirm: true },
                            "Lifecycle plan application",
                          );
                        }}
                      >Apply exact plan</button>
                    </div>}
                  </article>
                );
              })}
              {!data.lifecyclePlans.length && <p className="text-xs text-slate-500">No persisted lifecycle plans.</p>}
            </div>
            {!accessDenied && <p className="text-xs text-slate-500">The current API does not expose a collection of current actor mappings. This page shows trusted mapping scope only when it is present in a persisted lifecycle-plan snapshot.</p>}
          </section>
        </>
      )}
    </main>
  );
}
