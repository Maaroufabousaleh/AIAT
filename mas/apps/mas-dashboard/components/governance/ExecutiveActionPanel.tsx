"use client";

import { FormEvent, useState } from "react";
import type {
  ExecutiveCEOPrivilegedActionRequest,
  ExecutiveCFOModelOverrideRequest,
  ExecutiveCTOWorkerRunRequest,
} from "@/lib/generated/orchestrator-api";

type ActionResult = {
  label: string;
  body: unknown;
  error?: boolean;
};

const DEFAULT_DISPATCH = JSON.stringify(
  {
    worker_id: "",
    idempotency_key: "",
    task_type: "kpi.reconcile",
    task_input: {},
    project_id: null,
  },
  null,
  2,
);

const DEFAULT_PAYLOAD = JSON.stringify({}, null, 2);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

async function submit(path: string, body: unknown): Promise<unknown> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) {
    const message = typeof data === "object" && data && "error" in data
      ? String((data as { error?: unknown }).error)
      : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return data;
}

export function ExecutiveActionPanel() {
  const [projectId, setProjectId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [reason, setReason] = useState("");
  const [dispatchText, setDispatchText] = useState(DEFAULT_DISPATCH);
  const [ceoAction, setCeoAction] = useState("");
  const [ceoPayload, setCeoPayload] = useState(DEFAULT_PAYLOAD);
  const [confirmPrivileged, setConfirmPrivileged] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);

  async function runAction(label: string, path: string, body: unknown) {
    setBusy(label);
    setResult(null);
    try {
      setResult({ label, body: await submit(path, body) });
    } catch (error) {
      setResult({ label, error: true, body: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(null);
    }
  }

  async function requestModelOverride(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body: ExecutiveCFOModelOverrideRequest = {
      project_id: projectId.trim(),
      requested_profile_id: profileId.trim(),
      requested_by: "office_cfo",
      reason: reason.trim(),
    };
    await runAction("CFO model override", "/api/executive/actions/cfo/model-overrides", body);
  }

  async function dispatchWorker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const parsed = JSON.parse(dispatchText) as Record<string, unknown>;
      const body: ExecutiveCTOWorkerRunRequest = {
        requested_by: "office_cto",
        dispatch: parsed as ExecutiveCTOWorkerRunRequest["dispatch"],
      };
      await runAction("CTO worker dispatch", "/api/executive/actions/cto/worker-runs", body);
    } catch (error) {
      setResult({ label: "CTO worker dispatch", error: true, body: error instanceof Error ? error.message : "Dispatch JSON must be an object" });
    }
  }

  async function requestPrivilegedAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmPrivileged) return;
    try {
      const body: ExecutiveCEOPrivilegedActionRequest = {
        action: ceoAction.trim(),
        requested_by: "exec_ceo",
        payload: asRecord(JSON.parse(ceoPayload)),
      };
      await runAction("CEO privileged action", "/api/executive/actions/ceo/privileged-actions", body);
    } catch (error) {
      setResult({ label: "CEO privileged action", error: true, body: error instanceof Error ? error.message : "Payload JSON must be an object" });
    }
  }

  return (
    <section className="rounded-xl border border-amber-900/60 bg-slate-900/70 p-5 xl:col-span-2" aria-labelledby="executive-actions-heading">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h2 id="executive-actions-heading" className="text-sm font-semibold text-white">Executive actions</h2>
        <span className="font-mono text-[11px] text-slate-500">aiat.executive-action.v1</span>
      </div>
      <p className="mb-4 text-xs text-slate-400">Operator-authenticated requests are recorded by the control plane. Responses contain identifiers and decisions, never worker output or arbitrary payloads.</p>
      <div className="grid gap-5 lg:grid-cols-3">
        <form className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3" aria-labelledby="cfo-model-override-heading" onSubmit={(event) => void requestModelOverride(event)}>
          <h3 id="cfo-model-override-heading" className="text-xs font-semibold uppercase tracking-wide text-slate-300">CFO · model override</h3>
          <label className="block text-xs text-slate-400">Project ID<input required value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="UUID" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Requested profile ID<input required value={profileId} onChange={(event) => setProfileId(event.target.value)} placeholder="approved-profile" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Reason<textarea required value={reason} onChange={(event) => setReason(event.target.value)} rows={3} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-white" /></label>
          <button type="submit" disabled={busy !== null} className="min-h-11 rounded border border-amber-700 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-950 disabled:opacity-50">{busy === "CFO model override" ? "Submitting…" : "Request override"}</button>
        </form>

        <form className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3" aria-labelledby="cto-worker-run-heading" onSubmit={(event) => void dispatchWorker(event)}>
          <h3 id="cto-worker-run-heading" className="text-xs font-semibold uppercase tracking-wide text-slate-300">CTO · worker run</h3>
          <label className="block text-xs text-slate-400">Dispatch JSON<textarea required value={dispatchText} onChange={(event) => setDispatchText(event.target.value)} rows={10} spellCheck={false} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-white" /></label>
          <button type="submit" disabled={busy !== null} className="min-h-11 rounded border border-amber-700 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-950 disabled:opacity-50">{busy === "CTO worker dispatch" ? "Dispatching…" : "Dispatch governed run"}</button>
        </form>

        <form className="space-y-3 rounded-lg border border-rose-900/60 bg-slate-950/60 p-3" aria-labelledby="ceo-privileged-action-heading" onSubmit={(event) => void requestPrivilegedAction(event)}>
          <h3 id="ceo-privileged-action-heading" className="text-xs font-semibold uppercase tracking-wide text-rose-200">CEO · privileged action</h3>
          <label className="block text-xs text-slate-400">Action<input required value={ceoAction} onChange={(event) => setCeoAction(event.target.value)} placeholder="security.override_cso" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Payload JSON<textarea required value={ceoPayload} onChange={(event) => setCeoPayload(event.target.value)} rows={5} spellCheck={false} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-white" /></label>
          <label className="flex items-start gap-2 text-xs text-slate-400"><input type="checkbox" checked={confirmPrivileged} onChange={(event) => setConfirmPrivileged(event.target.checked)} className="mt-0.5 min-h-11 min-w-11" />I understand this enters the audited privileged-action approval gate.</label>
          <button type="submit" disabled={busy !== null || !confirmPrivileged} className="min-h-11 rounded border border-rose-700 px-3 py-1.5 text-xs text-rose-200 hover:bg-rose-950 disabled:opacity-50">{busy === "CEO privileged action" ? "Submitting…" : "Request privileged action"}</button>
        </form>
      </div>
      {result && <div className={`mt-4 rounded-lg border p-3 text-xs ${result.error ? "border-rose-900 bg-rose-950/30 text-rose-200" : "border-emerald-900 bg-emerald-950/20 text-emerald-200"}`} role="status" aria-live="polite" aria-label="Executive action result"><div className="mb-1 font-semibold">{result.label}</div><pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{typeof result.body === "string" ? result.body : JSON.stringify(result.body, null, 2)}</pre></div>}
    </section>
  );
}
