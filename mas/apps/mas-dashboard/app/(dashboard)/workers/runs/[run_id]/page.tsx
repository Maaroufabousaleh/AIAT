"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Pause, Play, RefreshCw, Square } from "lucide-react";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { PageHeader } from "@/components/ui/PageHeader";

type JsonRecord = Record<string, unknown>;
type RunEvidence = {
  run: JsonRecord;
  events: JsonRecord[];
  transitions: JsonRecord[];
  artifacts: JsonRecord[];
  usage: JsonRecord[];
  checkpoints: JsonRecord[];
};
type EvidenceKey = "events" | "transitions" | "artifacts" | "usage" | "checkpoints";
type RunAction = "cancel" | "pause" | "resume" | "retry";
type RetryTarget = { id: string; state: string };

class RunApiError extends Error {
  constructor(readonly status: number, readonly payload: unknown) {
    super("Worker Run request failed.");
    this.name = "RunApiError";
  }
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TERMINAL_STATES = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]);

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}

function asRecords(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

function display(value: unknown, fallback = "—"): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "—";
  } catch {
    return String(value);
  }
}

function dateTime(value: unknown): string {
  if (typeof value !== "string") return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function apiMessage(payload: unknown, status: number): string {
  let current: unknown = payload;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (typeof current !== "string") break;
    try {
      current = JSON.parse(current) as unknown;
    } catch {
      break;
    }
  }
  const root = asRecord(current);
  let detail: unknown = root.detail ?? root;
  if (typeof detail === "string") {
    try {
      detail = JSON.parse(detail) as unknown;
    } catch {
      // Keep the backend's plain-text explanation.
    }
  }
  const detailRecord = asRecord(detail);
  const code = display(detailRecord.code, "");
  const message = display(detailRecord.message ?? detail, "");
  if (code === "CHECKPOINT_RESTART_ONLY") {
    return message || "This adapter supports restart from a safe point, not in-place pause or resume.";
  }
  if (code === "UNSUPPORTED_CAPABILITY") {
    return message || "The certified adapter does not support this action.";
  }
  if (code === "RUN_NOT_PAUSABLE" || code === "RUN_NOT_RESUMABLE" || code === "RUN_STATE_CONFLICT") {
    return message || "The run state changed. Refresh the run before trying again.";
  }
  if (code === "RUN_NOT_RETRYABLE" || code === "RETRY_IDEMPOTENCY_CONFLICT" || code === "RUN_RETRY_ALREADY_CREATED") {
    return message || "This Worker Run cannot be retried with the current request.";
  }
  if (code === "RUN_RETRY_REQUIRES_FLOW_CONTROLLER" || code === "RUN_RETRY_EFFECTS_REVIEW_REQUIRED") {
    return message || "Review the flow or mediated-tool effects before retrying this Worker Run.";
  }
  if (message && message !== "[object Object]") return code ? message + " (" + code + ")." : message;
  return "The request failed (HTTP " + status + "). Refresh the run and try again.";
}

async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) throw new RunApiError(response.status, payload);
  return payload as T;
}

function stateTone(state: string): string {
  if (state === "SUCCEEDED") return "border-emerald-700/70 bg-emerald-950/40 text-emerald-200";
  if (state === "FAILED" || state === "TIMED_OUT") return "border-rose-700/70 bg-rose-950/30 text-rose-200";
  if (state === "CANCELLED" || state === "PAUSED" || state === "PAUSING" || state === "RESUMING") return "border-amber-700/70 bg-amber-950/30 text-amber-100";
  if (state === "RUNNING" || state === "DISPATCHING") return "border-cyan-700/70 bg-cyan-950/30 text-cyan-100";
  return "border-slate-700 bg-slate-950/60 text-slate-200";
}

function stateLabel(run: JsonRecord): string {
  return display(run.state, "UNKNOWN").toUpperCase();
}

function recordSummary(kind: EvidenceKey, row: JsonRecord, index: number): string {
  const when = dateTime(row.created_at ?? row.occurred_at ?? row.timestamp);
  if (kind === "events") {
    const label = display(row.event_type ?? row.type ?? row.state, "Worker event");
    return when === "—" ? label : label + " · " + when;
  }
  if (kind === "transitions") {
    return display(row.from_state, "—") + " → " + display(row.to_state, "—") + " · " + display(row.actor, "control plane");
  }
  if (kind === "artifacts") {
    const metadata = asRecord(row.metadata);
    return display(
      row.name ?? metadata.artifact_name ?? row.artifact_type ?? row.path,
      "Artifact " + (index + 1),
    );
  }
  if (kind === "usage") {
    const model = display(row.exact_model_id ?? row.provider ?? row.model, "Usage record");
    const tokens = row.total_tokens == null ? "" : " · " + display(row.total_tokens) + " tokens";
    const cost = row.cost_usd == null ? "" : " · $" + display(row.cost_usd);
    return model + tokens + cost;
  }
  return "Checkpoint " + display(row.sequence, String(index + 1)) + " · " + (row.resumable === true ? "resumable" : "not resumable");
}

function EvidenceSection({
  kind,
  title,
  rows,
  emptyText,
}: {
  kind: EvidenceKey;
  title: string;
  rows: JsonRecord[];
  emptyText: string;
}) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-4" aria-labelledby={"run-" + kind + "-heading"} data-testid={"run-" + kind}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={"run-" + kind + "-heading"} className="text-sm font-semibold text-white">{title}</h2>
        <span className="text-xs tabular-nums text-slate-500">{rows.length} record{rows.length === 1 ? "" : "s"}</span>
      </div>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">{emptyText}</p>
      ) : (
        <ol className="mt-3 divide-y divide-slate-800" aria-label={title + " records"}>
          {rows.map((row, index) => (
            <li key={display(row.id, kind + "-" + index)} className="py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="break-all text-sm text-slate-200">{recordSummary(kind, row, index)}</p>
                <span className="font-mono text-[11px] text-slate-500">{display(row.id, "")}</span>
              </div>
              {kind === "checkpoints" && (
                <p className="mt-1 text-xs text-slate-400">
                  {row.resumable === true ? "Can be selected for resume." : "This checkpoint is not marked resumable."}
                  {row.created_at ? " Saved " + dateTime(row.created_at) + "." : ""}
                </p>
              )}
              <details className="mt-2 rounded-lg border border-slate-800/80 bg-slate-950/60 px-3 py-2">
                <summary className="min-h-11 cursor-pointer py-3 text-xs font-medium text-cyan-200 underline decoration-cyan-700/50 underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300">
                  Inspect durable record
                </summary>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-[11px] leading-5 text-slate-300">{pretty(row)}</pre>
              </details>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function WorkerRunDetailPage() {
  const params = useParams<{ run_id: string }>();
  const runId = typeof params.run_id === "string" ? params.run_id : "";
  return <WorkerRunDetail key={runId} runId={runId} />;
}

function WorkerRunDetail({ runId }: { runId: string }) {
  const validRunId = UUID_PATTERN.test(runId);
  const [data, setData] = useState<RunEvidence | null>(null);
  const dataRef = useRef<RunEvidence | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const [busyAction, setBusyAction] = useState<RunAction | null>(null);
  const [actionError, setActionError] = useState("");
  const [actionNotice, setActionNotice] = useState("");
  const [cancelReason, setCancelReason] = useState("Operator requested cancellation");
  const [pauseReason, setPauseReason] = useState("Operator requested pause");
  const [retryReason, setRetryReason] = useState("Operator requested retry");
  const [retryRequestKey, setRetryRequestKey] = useState("");
  const [retryTarget, setRetryTarget] = useState<RetryTarget | null>(null);
  const [resumeCheckpointId, setResumeCheckpointId] = useState("");

  const loadRun = useCallback(async (quiet = false): Promise<RunEvidence | null> => {
    if (!validRunId) {
      setLoadError("This Worker Run link does not contain a valid run ID.");
      setLoading(false);
      return null;
    }
    if (!quiet) setLoading(true);
    setLoadError("");
    try {
      const base = "/api/governance/runs/" + encodeURIComponent(runId);
      const [run, events, transitions, artifacts, usage, checkpoints] = await Promise.all([
        readJson<unknown>(base),
        readJson<unknown>(base + "/events?limit=200&offset=0"),
        readJson<unknown>(base + "/transitions?limit=200"),
        readJson<unknown>(base + "/artifacts?limit=200"),
        readJson<unknown>(base + "/usage?limit=200"),
        readJson<unknown>(base + "/checkpoints?limit=100"),
      ]);
      const next: RunEvidence = {
        run: asRecord(run),
        events: asRecords(events),
        transitions: asRecords(transitions),
        artifacts: asRecords(artifacts),
        usage: asRecords(usage),
        checkpoints: asRecords(checkpoints),
      };
      dataRef.current = next;
      setData(next);
      setStale(false);
      setAccessDenied(false);
      setLoadError("");
      setResumeCheckpointId((current) => {
        if (current && next.checkpoints.some((checkpoint) => display(checkpoint.id) === current && checkpoint.resumable === true)) return current;
        const first = next.checkpoints.find((checkpoint) => checkpoint.resumable === true);
        return first ? display(first.id, "") : "";
      });
      setRetryTarget(typeof next.run.retry_run_id === "string"
        ? { id: next.run.retry_run_id, state: display(next.run.retry_run_state, "UNKNOWN") }
        : null);
      return next;
    } catch (cause) {
      if (cause instanceof RunApiError && [401, 403].includes(cause.status)) {
        setAccessDenied(true);
        setLoadError("The current operator identity is not authorized to read or change this Worker Run.");
        dataRef.current = null;
        setData(null);
        setStale(false);
        setResumeCheckpointId("");
        setRetryTarget(null);
        setActionNotice("");
        setActionError("");
      } else {
        setAccessDenied(false);
        setLoadError(cause instanceof RunApiError
          ? apiMessage(cause.payload, cause.status)
          : "Worker Run data could not be loaded.");
        setStale(Boolean(dataRef.current));
      }
      return dataRef.current;
    } finally {
      setLoading(false);
    }
  }, [runId, validRunId]);

  useEffect(() => {
    void loadRun();
  }, [loadRun]);

  const run = data?.run ?? {};
  const state = stateLabel(run);
  const terminal = TERMINAL_STATES.has(state);
  const retryableState = state === "FAILED" || state === "TIMED_OUT";
  const flowBound = [run.flow_id, run.flow_instance_id, run.flow_node_execution_id]
    .some((value) => typeof value === "string" && value.trim().length > 0);
  const retryable = retryableState && !flowBound;
  const adapterPinned = Boolean(run.adapter_id);
  const cancellable = Boolean(data) && !terminal;
  const runTitle = run.task_type ? display(run.task_type) + " · Worker Run" : "Worker Run";

  async function performAction(action: RunAction) {
    if (!data || accessDenied || busyAction) return;
    if (action === "cancel") {
      const reason = cancelReason.trim();
      if (!reason) {
        setActionError("Enter a reason before requesting cancellation.");
        return;
      }
      if (!window.confirm("Request cancellation for this Worker Run?")) return;
    }
    if (action === "retry") {
      if (!retryReason.trim()) {
        setActionError("Enter a reason before requesting a retry.");
        return;
      }
      if (!window.confirm("Create a new Worker Run using current authority, model, and budget policy?")) return;
    }
    if (action === "pause" && !pauseReason.trim()) {
      setActionError("Enter a reason before requesting pause.");
      return;
    }

    const reason = action === "cancel" ? cancelReason.trim() : action === "retry" ? retryReason.trim() : pauseReason.trim();
    const idempotencyKey = action === "retry"
      ? retryRequestKey || window.crypto.randomUUID()
      : "";
    if (action === "retry" && !retryRequestKey) setRetryRequestKey(idempotencyKey);
    const payload: JsonRecord = action === "resume"
      ? (resumeCheckpointId ? { checkpoint_id: resumeCheckpointId } : {})
      : action === "retry"
        ? { reason, idempotency_key: idempotencyKey }
        : { reason, ...(action === "cancel" ? { force: false } : {}) };
    setBusyAction(action);
    setActionError("");
    setActionNotice("");
    try {
      const response = await fetch("/api/governance/runs/" + encodeURIComponent(runId) + "/" + action, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => null) as unknown;
      if (!response.ok) throw new RunApiError(response.status, result);
      const actionRun = asRecord(result);
      if (action === "retry") {
        const newRunId = display(actionRun.run_id, "");
        const newRunState = display(actionRun.state, "UNKNOWN");
        if (!UUID_PATTERN.test(newRunId)) throw new Error("Retry response did not contain a valid Worker Run ID.");
        setRetryTarget({ id: newRunId, state: newRunState });
        setActionNotice("Retry created as " + newRunState + ". The original run and its evidence remain unchanged.");
        await loadRun(true);
        return;
      }
      const refreshed = await loadRun(true);
      const latestRun = refreshed?.run ?? { ...run, ...actionRun };
      const latestState = stateLabel(latestRun);
      if (action === "cancel") {
        setActionNotice(TERMINAL_STATES.has(latestState)
          ? "Cancellation confirmed. Worker Run state is " + latestState + "."
          : "Cancellation was accepted, but runtime termination is not confirmed. Current Worker Run state: " + latestState + ".");
      } else if (action === "pause") {
        setActionNotice(latestState === "PAUSED"
          ? "Pause confirmed. Worker Run state is PAUSED."
          : "Pause was accepted. Current Worker Run state: " + latestState + ".");
      } else {
        setActionNotice(latestState === "RUNNING"
          ? "Resume confirmed. Worker Run state is RUNNING."
          : "Resume was accepted. Current Worker Run state: " + latestState + ".");
      }
    } catch (cause) {
      if (cause instanceof RunApiError && [401, 403].includes(cause.status)) {
        setAccessDenied(true);
        setActionError("The current operator identity is no longer authorized. Run controls are hidden.");
        dataRef.current = null;
        setData(null);
        setStale(false);
        setResumeCheckpointId("");
        setRetryTarget(null);
        setActionNotice("");
      } else {
        setActionError(cause instanceof RunApiError
          ? apiMessage(cause.payload, cause.status)
          : cause instanceof Error && cause.message.startsWith("Retry response")
            ? cause.message
            : "The action could not be sent. Check the connection and retry.");
        if (cause instanceof RunApiError && cause.status === 409) void loadRun(true);
      }
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <main className="dashboard-page min-h-full space-y-5 p-6 lg:p-8" aria-label="Worker Run detail" aria-busy={loading || busyAction !== null}>
      <PageHeader
        icon="activity"
        title={runTitle}
        description={validRunId ? "Inspect durable execution records and request supported operator actions." : "The run identity could not be validated."}
        actions={
          <>
            <Link href="/governance" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300">
              <ArrowLeft size={14} aria-hidden="true" /> Governance
            </Link>
            {!accessDenied && validRunId && (
              <button type="button" onClick={() => void loadRun()} disabled={loading} aria-busy={loading} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:cursor-wait disabled:opacity-50">
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} aria-hidden="true" /> Refresh
              </button>
            )}
          </>
        }
      />

      {accessDenied && (
        <section role="region" aria-label="Worker Run access status" className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-5 py-4">
          <h2 className="text-sm font-semibold text-amber-100">Worker Run access denied</h2>
          <p className="mt-1 text-sm text-amber-200/80">
            Run data and operator actions are hidden until access is restored.
          </p>
        </section>
      )}

      {loadError && !accessDenied && (
        <ErrorBanner
          tone={stale && data ? "warning" : "error"}
          title={stale && data ? "Showing last known Worker Run data" : "Worker Run unavailable"}
          action={validRunId ? (
            <button type="button" onClick={() => void loadRun()} disabled={loading} className="min-h-11 rounded border border-current px-3 py-2 text-xs font-medium disabled:opacity-50">
              Retry
            </button>
          ) : undefined}
        >
          {stale && data ? loadError + " Last successfully loaded records remain visible." : loadError}
        </ErrorBanner>
      )}

      {actionError && !accessDenied && <p role="alert" className="rounded-lg border border-rose-700/60 bg-rose-950/30 px-4 py-3 text-sm text-rose-200">{actionError}</p>}
      {actionNotice && <p role="status" aria-live="polite" className="rounded-lg border border-emerald-700/60 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-200">{actionNotice}</p>}
      {loading && !data && (
        <div role="status" aria-live="polite" className="space-y-3" aria-label="Loading Worker Run">
          <div className="h-32 animate-pulse rounded-xl border border-slate-800 bg-slate-900/50" />
          <div className="grid gap-4 lg:grid-cols-2"><div className="h-44 animate-pulse rounded-xl border border-slate-800 bg-slate-900/50" /><div className="h-44 animate-pulse rounded-xl border border-slate-800 bg-slate-900/50" /></div>
          <span className="sr-only">Loading durable Worker Run records…</span>
        </div>
      )}
      {!loading && !data && !loadError && <p role="status" className="text-sm text-slate-400">No Worker Run data is available.</p>}
      {data && (
        <>
          <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4" aria-labelledby="run-identity-heading" data-testid="run-identity">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 id="run-identity-heading" className="text-sm font-semibold text-white">Run identity</h2>
                <p className="mt-2 break-all font-mono text-xs text-cyan-200">{display(run.id, runId)}</p>
              </div>
              <span className={"inline-flex min-h-9 items-center rounded-full border px-3 py-1 text-xs font-semibold " + stateTone(state)}>State · {state}</span>
            </div>
            <dl className="mt-4 grid gap-x-6 gap-y-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
              {([
                ["Worker", run.worker_id],
                ["Task type", run.task_type],
                ["Project", run.project_id],
                ["Flow instance", run.flow_instance_id],
                ["Attempt", run.attempt_count],
                ["Created", dateTime(run.created_at)],
                ["Started", dateTime(run.started_at)],
                ["Completed", dateTime(run.completed_at)],
                ["Adapter", run.adapter_id],
                ["Model snapshot", run.model_resolution_snapshot_id],
                ["Cancellation requested", dateTime(run.cancel_requested_at)],
                ["Recovery reason", run.recovery_reason],
              ] as Array<[string, unknown]>).map(([label, value]) => (
                <div key={String(label)} className="min-w-0">
                  <dt className="text-slate-500">{label}</dt>
                  <dd className="mt-1 break-all text-slate-200">{display(value)}</dd>
                </div>
              ))}
            </dl>
            {(typeof run.retry_of_run_id === "string" || retryTarget) && (
              <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs" aria-label="Retry lineage">
                {typeof run.retry_of_run_id === "string" && (
                  <p className="text-slate-400">Retry of <Link className="text-cyan-200 underline underline-offset-4" href={"/workers/runs/" + encodeURIComponent(run.retry_of_run_id)}>{run.retry_of_run_id}</Link></p>
                )}
                {retryTarget && (
                  <p className="text-slate-400">Retry run · {retryTarget.state} · <Link className="text-cyan-200 underline underline-offset-4" href={"/workers/runs/" + encodeURIComponent(retryTarget.id)}>{retryTarget.id}</Link></p>
                )}
              </div>
            )}
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {[
                ["Submitted request", run.request_json],
                ["Result", run.result_json],
                ["Error", run.error_json],
                ["Negotiation and replay metadata", { negotiation: run.negotiation_json, replay: run.replay_metadata }],
              ].filter(([, value]) => value != null).map(([label, value]) => (
                <details key={String(label)} className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2">
                  <summary className="min-h-11 cursor-pointer py-3 text-xs font-medium text-cyan-200 underline decoration-cyan-700/50 underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300">
                    {String(label)}
                  </summary>
                  <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-[11px] leading-5 text-slate-300">{pretty(value)}</pre>
                </details>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4" aria-labelledby="run-actions-heading" data-testid="run-actions">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="run-actions-heading" className="text-sm font-semibold text-white">Operator actions</h2>
              <span className="text-xs text-slate-500">AIAT validates run state and adapter capability before changing execution.</span>
            </div>
            {accessDenied ? (
              <p className="mt-3 text-sm text-amber-200">Actions are hidden while Worker Run access is denied.</p>
            ) : terminal && !retryableState ? (
              <p className="mt-3 text-sm text-slate-400">This run is terminal. No retry or execution control action is available.</p>
            ) : retryableState && flowBound ? (
              <p className="mt-3 text-sm text-slate-300">This run belongs to a flow. Retry it from the flow controls so the workflow state and this run stay aligned.</p>
            ) : retryable ? (
              <div className="mt-4 max-w-2xl space-y-3">
                {retryTarget ? (
                  <p className="text-sm text-slate-300">A retry is already linked to this run. Open it to inspect or retry that run if it later fails.</p>
                ) : (
                  <>
                    <p className="text-sm text-slate-300">Retry creates a new Worker Run. AIAT rechecks current worker authority, model policy, and budget; this run and its evidence remain unchanged.</p>
                    <label htmlFor="retry-reason" className="block text-xs font-medium text-slate-300">Retry reason</label>
                    <textarea id="retry-reason" rows={2} maxLength={4000} value={retryReason} disabled={loading || busyAction !== null} onChange={(event) => setRetryReason(event.target.value)} className="min-h-20 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:opacity-50" />
                    <button type="button" onClick={() => void performAction("retry")} disabled={loading || busyAction !== null || accessDenied || !retryReason.trim()} aria-busy={busyAction === "retry"} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-cyan-700/70 px-3 py-2 text-sm font-medium text-cyan-100 hover:bg-cyan-950/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50">
                      {busyAction === "retry" ? <RefreshCw size={14} className="animate-spin" aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
                      {busyAction === "retry" ? "Creating retry…" : "Retry Worker Run"}
                    </button>
                  </>
                )}
              </div>
            ) : (
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <div className="space-y-2">
                  <label htmlFor="cancel-reason" className="block text-xs font-medium text-slate-300">Cancellation reason</label>
                  <textarea id="cancel-reason" rows={2} maxLength={4000} value={cancelReason} disabled={loading || busyAction !== null || !cancellable} onChange={(event) => setCancelReason(event.target.value)} className="min-h-20 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:opacity-50" />
                  <button type="button" onClick={() => void performAction("cancel")} disabled={!cancellable || loading || busyAction !== null || accessDenied} aria-busy={busyAction === "cancel"} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-rose-700/70 px-3 py-2 text-sm font-medium text-rose-200 hover:bg-rose-950/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300 disabled:cursor-not-allowed disabled:opacity-50">
                    {busyAction === "cancel" ? <RefreshCw size={14} className="animate-spin" aria-hidden="true" /> : <Square size={13} aria-hidden="true" />}
                    {busyAction === "cancel" ? "Requesting…" : "Cancel Worker Run"}
                  </button>
                </div>
                <div className="space-y-4">
                  {state === "RUNNING" && adapterPinned && (
                    <div className="space-y-2">
                      <label htmlFor="pause-reason" className="block text-xs font-medium text-slate-300">Pause reason</label>
                      <textarea id="pause-reason" rows={2} maxLength={4000} value={pauseReason} disabled={loading || busyAction !== null} onChange={(event) => setPauseReason(event.target.value)} className="min-h-20 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:opacity-50" />
                      <button type="button" onClick={() => void performAction("pause")} disabled={loading || busyAction !== null || !pauseReason.trim()} aria-busy={busyAction === "pause"} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-amber-700/70 px-3 py-2 text-sm font-medium text-amber-100 hover:bg-amber-950/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 disabled:cursor-not-allowed disabled:opacity-50">
                        {busyAction === "pause" ? <RefreshCw size={14} className="animate-spin" aria-hidden="true" /> : <Pause size={14} aria-hidden="true" />}
                        {busyAction === "pause" ? "Pausing…" : "Pause Worker Run"}
                      </button>
                    </div>
                  )}
                  {state === "PAUSED" && adapterPinned && (
                    <div className="space-y-2">
                      <label htmlFor="resume-checkpoint" className="block text-xs font-medium text-slate-300">Resume checkpoint (optional)</label>
                      <select id="resume-checkpoint" value={resumeCheckpointId} disabled={loading || busyAction !== null} onChange={(event) => setResumeCheckpointId(event.target.value)} className="min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:opacity-50">
                        <option value="">Resume without selecting a checkpoint</option>
                        {data.checkpoints.filter((checkpoint) => checkpoint.resumable === true).map((checkpoint) => (
                          <option key={display(checkpoint.id)} value={display(checkpoint.id)}>{display(checkpoint.sequence, "Checkpoint")} · {display(checkpoint.id)}</option>
                        ))}
                      </select>
                      <button type="button" onClick={() => void performAction("resume")} disabled={loading || busyAction !== null} aria-busy={busyAction === "resume"} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-cyan-700/70 px-3 py-2 text-sm font-medium text-cyan-100 hover:bg-cyan-950/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50">
                        {busyAction === "resume" ? <RefreshCw size={14} className="animate-spin" aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
                        {busyAction === "resume" ? "Resuming…" : "Resume Worker Run"}
                      </button>
                    </div>
                  )}
                  {!adapterPinned && (state === "RUNNING" || state === "PAUSED") && (
                    <p className="rounded-lg border border-amber-700/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-100" role="status">
                      Pause and resume need a certified adapter pinned to this run. Cancellation remains available when the run state allows it.
                    </p>
                  )}
                  {adapterPinned && state !== "RUNNING" && state !== "PAUSED" && (
                    <p className="text-xs text-slate-500">Pause is available only while RUNNING; resume is available only while PAUSED.</p>
                  )}
                </div>
              </div>
            )}
          </section>

          <div className="grid gap-4 xl:grid-cols-2">
            <EvidenceSection kind="events" title="Worker events" rows={data.events} emptyText="No worker events have been recorded." />
            <EvidenceSection kind="transitions" title="State transitions" rows={data.transitions} emptyText="No state transitions have been recorded." />
            <EvidenceSection kind="artifacts" title="Artifacts" rows={data.artifacts} emptyText="No artifacts have been recorded for this run." />
            <EvidenceSection kind="usage" title="Usage" rows={data.usage} emptyText="No usage records have been recorded for this run." />
            <EvidenceSection kind="checkpoints" title="Checkpoints" rows={data.checkpoints} emptyText="No durable checkpoints have been recorded." />
          </div>
        </>
      )}
    </main>
  );
}
