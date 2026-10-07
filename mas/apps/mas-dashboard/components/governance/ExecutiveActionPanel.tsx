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

const DEFAULT_TASK_INPUT = "{}";
const DEFAULT_PAYLOAD = "{}";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
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

export function ExecutiveActionPanel({ accessDenied = false }: { accessDenied?: boolean }) {
  const [projectId, setProjectId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [reason, setReason] = useState("");
  const [workerId, setWorkerId] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [taskType, setTaskType] = useState("kpi.reconcile");
  const [taskInput, setTaskInput] = useState(DEFAULT_TASK_INPUT);
  const [dispatchProjectId, setDispatchProjectId] = useState("");
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
      const normalizedWorkerId = workerId.trim();
      const normalizedProjectId = dispatchProjectId.trim();
      const normalizedKey = idempotencyKey.trim();
      const normalizedTaskType = taskType.trim();
      if (!UUID_PATTERN.test(normalizedWorkerId)) {
        throw new Error("Worker ID must be a valid UUID.");
      }
      if (normalizedProjectId && !UUID_PATTERN.test(normalizedProjectId)) {
        throw new Error("Project ID must be a valid UUID when provided.");
      }
      if (!normalizedKey || normalizedKey.length > 256) {
        throw new Error("Idempotency key is required and must be 256 characters or fewer.");
      }
      if (!normalizedTaskType || normalizedTaskType.length > 256) {
        throw new Error("Task type is required and must be 256 characters or fewer.");
      }
      const body: ExecutiveCTOWorkerRunRequest = {
        requested_by: "office_cto",
        dispatch: {
          worker_id: normalizedWorkerId,
          idempotency_key: normalizedKey,
          task_type: normalizedTaskType,
          task_input: parseJsonObject(taskInput, "Task input"),
          project_id: normalizedProjectId || null,
        },
      };
      await runAction("CTO worker dispatch", "/api/executive/actions/cto/worker-runs", body);
    } catch (error) {
      setResult({ label: "CTO worker dispatch", error: true, body: error instanceof Error ? error.message : "Check the worker run fields." });
    }
  }

  async function requestPrivilegedAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmPrivileged) return;
    try {
      const action = ceoAction.trim();
      if (!action || action.length > 256) {
        throw new Error("Action is required and must be 256 characters or fewer.");
      }
      const body: ExecutiveCEOPrivilegedActionRequest = {
        action,
        requested_by: "exec_ceo",
        payload: parseJsonObject(ceoPayload, "Payload"),
      };
      await runAction("CEO privileged action", "/api/executive/actions/ceo/privileged-actions", body);
    } catch (error) {
      setResult({ label: "CEO privileged action", error: true, body: error instanceof Error ? error.message : "Check the privileged action fields." });
    }
  }

  if (accessDenied) {
    return (
      <section className="rounded-xl border border-amber-900/60 bg-slate-900/70 p-5 xl:col-span-2" aria-labelledby="executive-actions-heading">
        <div className="mb-1 flex items-center justify-between gap-3">
          <h2 id="executive-actions-heading" className="text-sm font-semibold text-white">Executive actions</h2>
          <span className="font-mono text-[11px] text-slate-500">aiat.executive-action.v1</span>
        </div>
        <p className="text-sm text-amber-200/80">Executive action forms are hidden because this operator identity is not authorized to read or change governance.</p>
      </section>
    );
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
          <label className="block text-xs text-slate-400">Worker ID<input required value={workerId} onChange={(event) => setWorkerId(event.target.value)} placeholder="UUID" autoComplete="off" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Task type<input required maxLength={256} value={taskType} onChange={(event) => setTaskType(event.target.value)} placeholder="code.edit" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Idempotency key<input required maxLength={256} value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} placeholder="unique-request-key" autoComplete="off" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Project ID (optional)<input value={dispatchProjectId} onChange={(event) => setDispatchProjectId(event.target.value)} placeholder="UUID" autoComplete="off" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Task input (JSON object)<textarea required value={taskInput} onChange={(event) => setTaskInput(event.target.value)} rows={4} spellCheck={false} aria-describedby="cto-task-input-help" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-white" /></label>
          <p id="cto-task-input-help" className="text-[11px] text-slate-500">Use an object such as {`{"issue":"Fix the failing test"}`}. Arrays and scalar JSON values are rejected.</p>
          <button type="submit" disabled={busy !== null} className="min-h-11 rounded border border-amber-700 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-950 disabled:opacity-50">{busy === "CTO worker dispatch" ? "Dispatching…" : "Dispatch governed run"}</button>
        </form>

        <form className="space-y-3 rounded-lg border border-rose-900/60 bg-slate-950/60 p-3" aria-labelledby="ceo-privileged-action-heading" onSubmit={(event) => void requestPrivilegedAction(event)}>
          <h3 id="ceo-privileged-action-heading" className="text-xs font-semibold uppercase tracking-wide text-rose-200">CEO · privileged action</h3>
          <label className="block text-xs text-slate-400">Action<input required maxLength={256} value={ceoAction} onChange={(event) => setCeoAction(event.target.value)} placeholder="security.override_cso" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-xs text-white" /></label>
          <label className="block text-xs text-slate-400">Payload (JSON object)<textarea required value={ceoPayload} onChange={(event) => setCeoPayload(event.target.value)} rows={5} spellCheck={false} aria-describedby="ceo-payload-help" className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-white" /></label>
          <p id="ceo-payload-help" className="text-[11px] text-slate-500">Enter a JSON object, for example {`{"ticket":"SEC-12"}`}. Fields depend on the action; this request still goes through the audited privileged-action approval gate.</p>
          <label className="flex items-start gap-2 text-xs text-slate-400"><input type="checkbox" checked={confirmPrivileged} onChange={(event) => setConfirmPrivileged(event.target.checked)} className="mt-0.5 min-h-11 min-w-11" />I understand this enters the audited privileged-action approval gate.</label>
          <button type="submit" disabled={busy !== null || !confirmPrivileged} className="min-h-11 rounded border border-rose-700 px-3 py-1.5 text-xs text-rose-200 hover:bg-rose-950 disabled:opacity-50">{busy === "CEO privileged action" ? "Submitting…" : "Request privileged action"}</button>
        </form>
      </div>
      {result && <div className={`mt-4 rounded-lg border p-3 text-xs ${result.error ? "border-rose-900 bg-rose-950/30 text-rose-200" : "border-emerald-900 bg-emerald-950/20 text-emerald-200"}`} role="status" aria-live="polite" aria-label="Executive action result"><div className="mb-1 font-semibold">{result.label}</div><pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{typeof result.body === "string" ? result.body : JSON.stringify(result.body, null, 2)}</pre></div>}
    </section>
  );
}
