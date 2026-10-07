"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, Check, FolderKanban, RefreshCw, ShieldCheck, X } from "lucide-react";
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

type ProjectDecision = {
  id: string;
  project_id: string;
  project_name: string;
  project_state: string;
  company_id: string;
  gate_type: string;
  status: string;
  created_at: string;
};

type ProjectDecisionPage = {
  items: ProjectDecision[];
  next_cursor: string | null;
};

type InboxRow =
  | { kind: "ceo"; id: string; created_at?: string | null; item: ApprovalRequest }
  | { kind: "project"; id: string; created_at: string; item: ProjectDecision };

type HttpError = Error & { status?: number };

const PAGE_SIZE = 50;
const SENSITIVE_FIELD = /secret|token|password|credential|api[_-]?key|authorization|private[_-]?key/i;
const CEO_REQUEST_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

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

function rowTimestamp(row: InboxRow): number {
  const value = row.created_at ? Date.parse(row.created_at) : 0;
  return Number.isFinite(value) ? value : 0;
}

export default function ApprovalsPage() {
  return (
    <Suspense fallback={<main className="dashboard-page min-h-full p-6 lg:p-8" aria-label="Privileged action approvals"><p role="status">Loading approvals…</p></main>}>
      <ApprovalsContent />
    </Suspense>
  );
}

function ApprovalsContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryString = searchParams.toString();
  const requestedId = new URLSearchParams(queryString).get("request_id");
  const invalidRequestedId = requestedId !== null && !CEO_REQUEST_ID_PATTERN.test(requestedId);
  const requestedRequestId = requestedId && !invalidRequestedId ? requestedId : null;
  const [pending, setPending] = useState<ApprovalRequest[]>([]);
  const [projectDecisions, setProjectDecisions] = useState<ProjectDecision[]>([]);
  const [projectCursor, setProjectCursor] = useState<string | null>(null);
  const [audit, setAudit] = useState<ApprovalAuditRow[]>([]);
  const [selected, setSelected] = useState<ApprovalRequest | null>(null);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [staleRequestId, setStaleRequestId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const submittingRef = useRef(false);
  const pagingRef = useRef(false);
  const requestDeepLinkedIdRef = useRef<string | null>(null);
  const requestDeepLinkFocusRef = useRef<string | null>(null);
  const requestDetailsRef = useRef<HTMLElement | null>(null);
  const refreshGenerationRef = useRef(0);
  const refreshFailedRef = useRef(false);
  const preserveNoticeOnRefreshRef = useRef(false);

  const updateRequestQuery = useCallback((requestId: string | null) => {
    const nextParams = new URLSearchParams(queryString);
    if (requestId) nextParams.set("request_id", requestId);
    else nextParams.delete("request_id");
    const query = nextParams.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [pathname, queryString, router]);

  const refresh = useCallback(async () => {
    const linkedRequestId = requestedRequestId;
    const refreshGeneration = ++refreshGenerationRef.current;
    setLoading(true);
    setError("");
    if (!preserveNoticeOnRefreshRef.current) setNotice("");
    try {
      const projectQuery = new URLSearchParams({ limit: String(PAGE_SIZE) });
      const [pendingRows, auditRows, projectPage] = await Promise.all([
        requestJson<ApprovalRequest[]>("/api/ceo/privileged-actions/pending"),
        requestJson<ApprovalAuditRow[]>("/api/ceo/privileged-actions/audit"),
        requestJson<ProjectDecisionPage>(`/api/approvals/project-decisions?${projectQuery.toString()}`),
      ]);
      if (refreshGeneration !== refreshGenerationRef.current) return;
      if (refreshFailedRef.current) {
        setReason("");
        setConfirmed(false);
        refreshFailedRef.current = false;
      }
      const pendingList = Array.isArray(pendingRows) ? pendingRows.slice(0, 100) : [];
      setPending(pendingList);
      setAudit(Array.isArray(auditRows) ? auditRows : []);
      setProjectDecisions(Array.isArray(projectPage.items) ? projectPage.items : []);
      setProjectCursor(typeof projectPage.next_cursor === "string" ? projectPage.next_cursor : null);
      if (invalidRequestedId) {
        requestDeepLinkedIdRef.current = null;
        setSelected(null);
        setNotice("The linked CEO request identifier is invalid. Refresh the inbox to inspect the current queue.");
      } else if (linkedRequestId) {
        const linkedRequest = pendingList.find((item) => item.id === linkedRequestId);
        if (linkedRequest) {
          if (requestDeepLinkedIdRef.current !== linkedRequestId) {
            setReason("");
            setConfirmed(false);
            requestDeepLinkFocusRef.current = linkedRequestId;
          }
          requestDeepLinkedIdRef.current = linkedRequestId;
          setSelected(linkedRequest);
        } else {
          requestDeepLinkedIdRef.current = linkedRequestId;
          setSelected(null);
          setNotice("The linked CEO request is not currently pending. Refresh the inbox to inspect the current queue.");
        }
      } else {
        requestDeepLinkedIdRef.current = null;
        setSelected(null);
      }
      setStaleRequestId(null);
      setAccessDenied(false);
    } catch (cause) {
      if (refreshGeneration !== refreshGenerationRef.current) return;
      refreshFailedRef.current = true;
      const status = cause instanceof Error ? (cause as HttpError).status : undefined;
      if (status === 401 || status === 403) {
        setPending([]);
        setAudit([]);
        setProjectDecisions([]);
        setProjectCursor(null);
        requestDeepLinkedIdRef.current = null;
        setSelected(null);
        setAccessDenied(true);
        setError("This operator identity is not authorized to inspect or decide approvals.");
      } else {
        setError(cause instanceof Error ? cause.message : "Approval data is unavailable.");
      }
    } finally {
      if (refreshGeneration === refreshGenerationRef.current) {
        setLoading(false);
        preserveNoticeOnRefreshRef.current = false;
      }
    }
  }, [invalidRequestedId, requestedRequestId]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!selected || requestDeepLinkFocusRef.current !== selected.id) return;
    const details = requestDetailsRef.current;
    if (!details) return;
    requestDeepLinkFocusRef.current = null;
    details.scrollIntoView({ block: "start" });
    details.focus({ preventScroll: true });
  }, [selected]);

  const loadMoreProjectDecisions = async () => {
    if (!projectCursor || pagingRef.current) return;
    pagingRef.current = true;
    setLoadingMore(true);
    setError("");
    try {
      const query = new URLSearchParams({ limit: String(PAGE_SIZE), cursor: projectCursor });
      const page = await requestJson<ProjectDecisionPage>(`/api/approvals/project-decisions?${query.toString()}`);
      const rows = Array.isArray(page.items) ? page.items : [];
      setProjectDecisions((current) => {
        const known = new Set(current.map((item) => item.id));
        return [...current, ...rows.filter((item) => !known.has(item.id))];
      });
      setProjectCursor(typeof page.next_cursor === "string" ? page.next_cursor : null);
    } catch (cause) {
      const status = cause instanceof Error ? (cause as HttpError).status : undefined;
      setError(status === 400 || status === 409
        ? "The project decision page changed. Refresh the inbox to load the current queue."
        : cause instanceof Error ? cause.message : "More project decisions are unavailable.");
    } finally {
      pagingRef.current = false;
      setLoadingMore(false);
    }
  };

  const selectedAudit = useMemo(
    () => selected ? audit.find((row) => row.id === selected.id) ?? null : null,
    [audit, selected],
  );

  const inboxRows = useMemo<InboxRow[]>(() => [
    ...pending.map((item) => ({ kind: "ceo" as const, id: item.id, created_at: item.requested_at, item })),
    ...projectDecisions.map((item) => ({ kind: "project" as const, id: item.id, created_at: item.created_at, item })),
  ].sort((a, b) => rowTimestamp(b) - rowTimestamp(a) || a.kind.localeCompare(b.kind) || a.id.localeCompare(b.id)), [pending, projectDecisions]);

  const decide = async (approved: boolean) => {
    if (!selected || !confirmed || !reason.trim() || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    setNotice("");
    setStaleRequestId(null);
    try {
      await requestJson(`/api/ceo/privileged-actions/${encodeURIComponent(selected.id)}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, reason: reason.trim() }),
      });
      setReason("");
      setConfirmed(false);
      requestDeepLinkedIdRef.current = null;
      setSelected(null);
      setPending((current) => current.filter((item) => item.id !== selected.id));
      const decisionNotice = `Decision recorded: ${approved ? "approved" : "rejected"}. The governed action was not dispatched from this inbox.`;
      preserveNoticeOnRefreshRef.current = true;
      setNotice(decisionNotice);
      updateRequestQuery(null);
    } catch (cause) {
      const status = cause instanceof Error ? (cause as HttpError).status : undefined;
      if (status === 401 || status === 403) {
        setAccessDenied(true);
        setPending([]);
        setAudit([]);
        setProjectDecisions([]);
        setProjectCursor(null);
        requestDeepLinkedIdRef.current = null;
        setSelected(null);
        setError("This operator identity is not authorized to decide privileged actions.");
      } else if (status === 404 || status === 409) {
        setStaleRequestId(selected.id);
        setNotice("This CEO request is no longer pending. Refresh the inbox to see its current status.");
      } else {
        setError(cause instanceof Error ? cause.message : "The decision could not be recorded.");
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const selectedIsPending = selected
    ? !loading && !error && requestedRequestId === selected.id && pending.some((item) => item.id === selected.id) && staleRequestId !== selected.id
    : false;

  return (
    <main className="dashboard-page min-h-full p-6 lg:p-8" aria-label="Privileged action approvals">
      <PageHeader
        icon="shield-check"
        title="Approval inbox"
        description="Review CEO requests here and open project decisions in their canonical project workspace."
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
            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 xl:col-span-2" aria-labelledby="pending-heading" aria-busy={loading}>
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 id="pending-heading" className="flex items-center gap-2 text-sm font-semibold text-white"><ShieldCheck size={16} className="text-amber-300" /> Pending requests</h2>
                <span className="text-xs text-slate-400">{inboxRows.length} loaded · CEO and project queues</span>
              </div>
              {loading && inboxRows.length === 0 ? <p className="text-sm text-slate-400">Loading approvals…</p> : inboxRows.length === 0 ? (
                <div className="rounded-lg border border-dashed border-slate-700 p-4 text-sm text-slate-400">
                  <p>No approvals are currently waiting for review.</p>
                  <p className="mt-1">No privileged-action requests are waiting for review.</p>
                  <p className="mt-1">No project decisions are waiting for review.</p>
                </div>
              ) : (
                <ul className="space-y-3" aria-label="Unified approval inbox">
                  {inboxRows.map((row) => row.kind === "ceo" ? (
                    <li key={`ceo:${row.id}`} className={`rounded-lg border p-3 ${selected?.id === row.id ? "border-blue-400/60 bg-blue-500/5" : "border-slate-800 bg-slate-950/50"}`}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="rounded border border-amber-500/40 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-200">CEO privileged</span>
                            <span className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300">Pending · {row.item.risk_level} risk</span>
                          </div>
                          <h3 className="mt-2 break-all text-sm font-medium text-white">{row.item.action}</h3>
                          <p className="mt-1 text-xs text-slate-400">Requested by {row.item.actor_id} ({row.item.actor_role})</p>
                          <p className="mt-1 text-xs text-slate-500">{formatDate(row.created_at)}</p>
                        </div>
                        <button type="button" onClick={() => { setSelected(row.item); setReason(""); setConfirmed(false); setStaleRequestId(null); setNotice(""); updateRequestQuery(row.id); }} aria-pressed={selected?.id === row.id} className="min-h-11 rounded border border-slate-700 px-3 py-2 text-xs font-medium text-blue-200 hover:bg-slate-800">View details</button>
                      </div>
                    </li>
                  ) : (
                    <li key={`project:${row.id}`} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="rounded border border-blue-500/40 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-blue-200">Project decision</span>
                            <span className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300">{row.item.status.replace(/_/g, " ")}</span>
                          </div>
                          <h3 className="mt-2 text-sm font-medium text-white">{row.item.project_name}</h3>
                          <p className="mt-1 text-xs text-slate-400">{row.item.gate_type.replace(/_/g, " ")} · project state {row.item.project_state.replace(/_/g, " ")}</p>
                          <p className="mt-1 break-all text-xs text-slate-500">Company {row.item.company_id} · {formatDate(row.created_at)}</p>
                        </div>
                        <Link href={`/projects/${encodeURIComponent(row.item.project_id)}`} aria-label={`Open ${row.item.project_name} project decision context`} className="inline-flex min-h-11 items-center gap-2 rounded border border-slate-700 px-3 py-2 text-xs font-medium text-blue-200 hover:bg-slate-800">
                          <FolderKanban size={14} /> Open project context <ArrowUpRight size={13} />
                        </Link>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {projectCursor && (
                <div className="mt-4 flex justify-center">
                  <button type="button" onClick={() => void loadMoreProjectDecisions()} disabled={loadingMore || loading} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
                    {loadingMore ? "Loading…" : "Load more project decisions"}
                  </button>
                </div>
              )}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="audit-heading" aria-busy={loading}>
              <div className="mb-4 flex items-center justify-between gap-3"><h2 id="audit-heading" className="text-sm font-semibold text-white">Recent CEO decision audit</h2><span className="text-xs text-slate-400">latest 100 records</span></div>
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
              <section ref={requestDetailsRef} tabIndex={-1} className="rounded-xl border border-blue-400/30 bg-slate-900/80 p-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300" aria-labelledby="request-details-heading">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div><h2 id="request-details-heading" className="text-base font-semibold text-white">Request details</h2><p className="mt-1 break-all font-mono text-xs text-slate-500">{selected.id}</p></div>
                  <button type="button" onClick={() => { requestDeepLinkedIdRef.current = null; setSelected(null); updateRequestQuery(null); }} className="min-h-11 rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800">Close details</button>
                </div>
                <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                  <div><dt className="text-xs text-slate-500">Action</dt><dd className="mt-1 break-all text-slate-100">{selected.action}</dd></div>
                  <div><dt className="text-xs text-slate-500">Requested by</dt><dd className="mt-1 text-slate-100">{selected.actor_id} · {selected.actor_role}</dd></div>
                  <div><dt className="text-xs text-slate-500">Privilege / risk</dt><dd className="mt-1 text-slate-100">{selected.privilege_level} · {selected.risk_level}</dd></div>
                  <div><dt className="text-xs text-slate-500">Requested</dt><dd className="mt-1 text-slate-100">{formatDate(selected.requested_at)}</dd></div>
                </dl>
                <div className="mt-4"><h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Request payload</h3><pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">{JSON.stringify(sanitizePayload(selected.payload_json ?? {}), null, 2)}</pre></div>
                {selectedAudit && <p role="status" className="mt-4 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm text-slate-200">Current audit decision: {selectedAudit.decision} · {selectedAudit.decided_by || "no decider recorded"} · {selectedAudit.reason || "no reason recorded"}</p>}
                {selectedIsPending ? (
                  <div className="mt-5 border-t border-slate-800 pt-5">
                    <p className="text-sm text-amber-200">This page records an audited CEO decision only. It does not dispatch the governed action.</p>
                    <label htmlFor="approval-reason" className="mt-4 block text-xs font-medium text-slate-300">Decision reason (required)</label>
                    <textarea id="approval-reason" value={reason} onChange={(event) => setReason(event.target.value.slice(0, 1000))} rows={3} maxLength={1000} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400" placeholder="Record why this request should be approved or rejected." />
                    <label className="mt-3 flex min-h-11 items-center gap-3 text-sm text-slate-300"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="size-4 accent-blue-500" />I reviewed this request and intend to record a decision.</label>
                    <div className="mt-4 flex flex-wrap gap-3">
                      <button type="button" onClick={() => void decide(true)} disabled={!confirmed || !reason.trim() || submitting} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-50"><Check size={15} /> Approve request</button>
                      <button type="button" onClick={() => void decide(false)} disabled={!confirmed || !reason.trim() || submitting} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-rose-500/50 px-4 py-2 text-sm font-medium text-rose-200 hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-50"><X size={15} /> Reject request</button>
                    </div>
                  </div>
                ) : <p role="status" className="mt-5 border-t border-slate-800 pt-4 text-sm text-slate-400">{loading || error || invalidRequestedId || requestedRequestId !== selected.id ? "Current pending status could not be verified. Refresh the inbox before recording a decision." : "This request is no longer pending. Refresh the inbox to see its current status."}</p>}
              </section>
            )}
          </div>
        </>
      )}
    </main>
  );
}
