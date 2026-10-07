"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
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

type ProjectOption = {
  id: string;
  name?: string;
  state?: string;
};

type WorkerOption = {
  id: string;
  worker_id?: string;
  name?: string;
  status?: string;
};

type ModelProfileOption = {
  profile_id: string;
  purpose?: string;
  status?: string;
};

const DEFAULT_TASK_INPUT = "{}";
const DEFAULT_PAYLOAD = "{}";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const FIELD_CLASS = "mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-2 text-base text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70 disabled:cursor-not-allowed disabled:opacity-60";
const MONO_FIELD_CLASS = `${FIELD_CLASS} font-mono`;

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function apiErrorMessage(payload: unknown, status: number, fallback: string): string {
  const body = objectRecord(payload);
  const detail = body?.detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((entry) => {
      const issue = objectRecord(entry);
      if (typeof issue?.msg !== "string") return [];
      const location = Array.isArray(issue.loc)
        ? issue.loc.map(String).filter((part) => part !== "body").join(".")
        : "";
      return [location ? `${location}: ${issue.msg}` : issue.msg];
    });
    if (messages.length) return messages.join("; ");
  }
  if (typeof body?.error === "string" && body.error.trim()) return body.error;
  if (typeof detail === "string" && detail.trim()) return detail;
  const detailBody = objectRecord(detail);
  if (typeof detailBody?.message === "string" && detailBody.message.trim()) return detailBody.message;
  if (typeof body?.message === "string" && body.message.trim()) return body.message;
  return `${fallback} (HTTP ${status})`;
}

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

async function readOptionList(path: string, label: string, signal?: AbortSignal): Promise<unknown[]> {
  const response = await fetch(path, { cache: "no-store", signal });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) throw new Error(apiErrorMessage(payload, response.status, `${label} could not be loaded`));
  if (!Array.isArray(payload)) throw new Error(`${label} returned an unexpected response. Refresh the choices and try again.`);
  return payload;
}

function projectOptions(data: unknown[]): ProjectOption[] {
  return data.flatMap((value) => {
    const item = objectRecord(value);
    return typeof item?.id === "string" && item.id.trim()
      ? [{ id: item.id, name: typeof item.name === "string" ? item.name : undefined, state: typeof item.state === "string" ? item.state : undefined }]
      : [];
  });
}

function workerOptions(data: unknown[]): WorkerOption[] {
  return data.flatMap((value) => {
    const item = objectRecord(value);
    return typeof item?.id === "string" && item.id.trim()
      ? [{
        id: item.id,
        worker_id: typeof item.worker_id === "string" ? item.worker_id : undefined,
        name: typeof item.name === "string" ? item.name : undefined,
        status: typeof item.status === "string" ? item.status : undefined,
      }]
      : [];
  });
}

function modelProfileOptions(data: unknown[]): ModelProfileOption[] {
  return data.flatMap((value) => {
    const item = objectRecord(value);
    return typeof item?.profile_id === "string" && item.profile_id.trim()
      ? [{
        profile_id: item.profile_id,
        purpose: typeof item.purpose === "string" ? item.purpose : undefined,
        status: typeof item.status === "string" ? item.status : undefined,
      }]
      : [];
  });
}

function createIdempotencyKey(): string {
  if (typeof window !== "undefined" && typeof window.crypto?.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `governance-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function submit(path: string, body: unknown): Promise<unknown> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => null) as unknown;
  if (!response.ok) throw new Error(apiErrorMessage(data, response.status, "Request failed"));
  return data;
}

export function ExecutiveActionPanel({ accessDenied = false }: { accessDenied?: boolean }) {
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [workers, setWorkers] = useState<WorkerOption[]>([]);
  const [profiles, setProfiles] = useState<ModelProfileOption[]>([]);
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [projectOptionsError, setProjectOptionsError] = useState<string | null>(null);
  const [workerOptionsError, setWorkerOptionsError] = useState<string | null>(null);
  const [profileOptionsError, setProfileOptionsError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [reason, setReason] = useState("");
  const [workerId, setWorkerId] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [taskType, setTaskType] = useState("kpi.reconcile");
  const [taskInput, setTaskInput] = useState(DEFAULT_TASK_INPUT);
  const [dispatchProjectId, setDispatchProjectId] = useState("");
  const [scopeInput, setScopeInput] = useState("");
  const [ceoAction, setCeoAction] = useState("");
  const [ceoPayload, setCeoPayload] = useState(DEFAULT_PAYLOAD);
  const [confirmPrivileged, setConfirmPrivileged] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);

  const loadActionOptions = useCallback(async (signal?: AbortSignal) => {
    setOptionsLoading(true);
    const [projectResult, workerResult, profileResult] = await Promise.allSettled([
      readOptionList("/api/projects?limit=1000", "Project choices", signal),
      readOptionList("/api/workers", "Worker choices", signal),
      readOptionList("/api/governance/model-profiles", "Model Profile choices", signal),
    ]);
    if (signal?.aborted) return;

    if (projectResult.status === "fulfilled") {
      setProjects(projectOptions(projectResult.value));
      setProjectOptionsError(null);
    } else {
      setProjects([]);
      setProjectOptionsError(projectResult.reason instanceof Error
        ? projectResult.reason.message
        : "Project choices could not be loaded. Refresh action choices and try again.");
    }
    if (workerResult.status === "fulfilled") {
      setWorkers(workerOptions(workerResult.value));
      setWorkerOptionsError(null);
    } else {
      setWorkers([]);
      setWorkerOptionsError(workerResult.reason instanceof Error
        ? workerResult.reason.message
        : "Worker choices could not be loaded. Refresh action choices and try again.");
    }
    if (profileResult.status === "fulfilled") {
      setProfiles(modelProfileOptions(profileResult.value));
      setProfileOptionsError(null);
    } else {
      setProfiles([]);
      setProfileOptionsError(profileResult.reason instanceof Error
        ? profileResult.reason.message
        : "Model Profile choices could not be loaded. Refresh action choices and try again.");
    }
    setOptionsLoading(false);
  }, []);

  useEffect(() => {
    if (accessDenied) return;
    const controller = new AbortController();
    void loadActionOptions(controller.signal);
    return () => controller.abort();
  }, [accessDenied, loadActionOptions]);

  useEffect(() => {
    setIdempotencyKey(createIdempotencyKey());
  }, []);

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
    try {
      const normalizedProjectId = projectId.trim();
      const normalizedProfileId = profileId.trim();
      if (!normalizedProjectId) throw new Error("Choose a project from the current project list.");
      if (!UUID_PATTERN.test(normalizedProjectId)) throw new Error("The selected project ID is invalid. Refresh action choices and select a listed project.");
      if (!projects.some((project) => project.id === normalizedProjectId)) {
        throw new Error("The selected project is no longer in the current project list. Refresh action choices and choose a current project.");
      }
      if (!normalizedProfileId) throw new Error("Choose a Model Profile from the current list.");
      if (normalizedProfileId.length > 256) throw new Error("Model Profile ID must be 256 characters or fewer.");
      if (!profiles.some((profile) => profile.profile_id === normalizedProfileId)) {
        throw new Error("The selected Model Profile is no longer in the current list. Refresh action choices and choose a current profile.");
      }
      const normalizedReason = reason.trim();
      if (!normalizedReason) throw new Error("Enter a reason for the model override.");
      if (normalizedReason.length > 4_000) throw new Error("Reason must be 4,000 characters or fewer.");
      const scope = scopeInput.trim() ? parseJsonObject(scopeInput, "Scope") : undefined;
      const body: ExecutiveCFOModelOverrideRequest = {
        project_id: normalizedProjectId,
        requested_profile_id: normalizedProfileId,
        requested_by: "office_cfo",
        reason: normalizedReason,
        ...(scope ? { scope } : {}),
      };
      await runAction("CFO model override", "/api/executive/actions/cfo/model-overrides", body);
    } catch (error) {
      setResult({ label: "CFO model override", error: true, body: error instanceof Error ? error.message : "Check the model override fields." });
    }
  }

  async function dispatchWorker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const normalizedWorkerId = workerId.trim();
      const normalizedProjectId = dispatchProjectId.trim();
      const normalizedKey = idempotencyKey.trim();
      const normalizedTaskType = taskType.trim();
      if (!normalizedWorkerId) throw new Error("Choose a worker from the current worker list.");
      if (!UUID_PATTERN.test(normalizedWorkerId)) throw new Error("The selected worker ID is invalid. Refresh action choices and select a listed worker.");
      if (!workers.some((worker) => worker.id === normalizedWorkerId)) {
        throw new Error("The selected worker is no longer in the current worker list. Refresh action choices and choose a current worker.");
      }
      if (normalizedProjectId && !UUID_PATTERN.test(normalizedProjectId)) {
        throw new Error("The selected project ID is invalid. Refresh action choices and select a listed project, or choose No project.");
      }
      if (normalizedProjectId && !projects.some((project) => project.id === normalizedProjectId)) {
        throw new Error("The selected project is no longer in the current project list. Refresh action choices and choose a current project, or choose No project.");
      }
      if (!normalizedKey || normalizedKey.length > 256) {
        throw new Error("A generated idempotency key is required and must be 256 characters or fewer.");
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

  const projectChoiceMessage = projectOptionsError
    ?? (optionsLoading ? "Loading current project choices…" : projects.length === 0 ? "No projects are available from the control plane." : "Choose a current project.");
  const workerChoiceMessage = workerOptionsError
    ?? (optionsLoading ? "Loading current worker choices…" : workers.length === 0 ? "No workers are available from the control plane." : "Choose a current worker.");
  const profileChoiceMessage = profileOptionsError
    ?? (optionsLoading ? "Loading current Model Profile choices…" : profiles.length === 0 ? "No Model Profiles are available from the control plane." : "Choose a current Model Profile.");

  return (
    <section className="rounded-xl border border-amber-900/60 bg-slate-900/70 p-5 xl:col-span-2" aria-labelledby="executive-actions-heading">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h2 id="executive-actions-heading" className="text-sm font-semibold text-white">Executive actions</h2>
        <span className="font-mono text-[11px] text-slate-500">aiat.executive-action.v1</span>
      </div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-3xl text-xs text-slate-400">Operator-authenticated requests are recorded by the control plane. Responses contain identifiers and decisions, never worker output or arbitrary payloads.</p>
        <button
          type="button"
          onClick={() => void loadActionOptions()}
          disabled={optionsLoading}
          className="min-h-11 rounded border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {optionsLoading ? "Loading choices…" : "Refresh action choices"}
        </button>
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        <form className="min-w-0 space-y-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3" aria-labelledby="cfo-model-override-heading" onSubmit={(event) => void requestModelOverride(event)}>
          <h3 id="cfo-model-override-heading" className="text-xs font-semibold uppercase tracking-wide text-slate-300">CFO · model override</h3>
          <label className="block text-sm text-slate-300" htmlFor="cfo-project">Project
            <select id="cfo-project" value={projectId} onChange={(event) => setProjectId(event.target.value)} disabled={optionsLoading || Boolean(projectOptionsError) || projects.length === 0} aria-required="true" aria-describedby="cfo-project-help" className={FIELD_CLASS}>
              <option value="">{optionsLoading ? "Loading projects…" : "Choose a project"}</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name || "Unnamed project"} · {project.state || "state unknown"} · {project.id.slice(0, 8)}</option>)}
            </select>
          </label>
          <p id="cfo-project-help" className={`-mt-2 text-xs ${projectOptionsError ? "text-rose-300" : "text-slate-500"}`} aria-live="polite">{projectChoiceMessage}</p>
          <label className="block text-sm text-slate-300" htmlFor="cfo-profile">Model Profile
            <select id="cfo-profile" value={profileId} onChange={(event) => setProfileId(event.target.value)} disabled={optionsLoading || Boolean(profileOptionsError) || profiles.length === 0} aria-required="true" aria-describedby="cfo-profile-help" className={FIELD_CLASS}>
              <option value="">{optionsLoading ? "Loading Model Profiles…" : "Choose a Model Profile"}</option>
              {profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_id}{profile.purpose ? ` · ${profile.purpose}` : ""} · {profile.status || "status unknown"}</option>)}
            </select>
          </label>
          <p id="cfo-profile-help" className={`-mt-2 text-xs ${profileOptionsError ? "text-rose-300" : "text-slate-500"}`} aria-live="polite">{profileChoiceMessage}</p>
          <label className="block text-sm text-slate-300" htmlFor="cfo-reason">Reason
            <textarea id="cfo-reason" required maxLength={4000} value={reason} onChange={(event) => setReason(event.target.value)} rows={3} className={`${FIELD_CLASS} resize-y`} />
          </label>
          <details className="rounded border border-slate-800 px-3 py-2">
            <summary className="min-h-11 cursor-pointer py-3 text-sm text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70">Advanced scope (optional JSON object)</summary>
            <label className="block text-sm text-slate-300" htmlFor="cfo-scope">Scope (JSON object)
              <textarea id="cfo-scope" value={scopeInput} onChange={(event) => setScopeInput(event.target.value)} rows={3} spellCheck={false} aria-describedby="cfo-scope-help" className={`${MONO_FIELD_CLASS} resize-y`} />
            </label>
            <p id="cfo-scope-help" className="mt-1 text-xs text-slate-500">The API accepts an optional scope object but publishes no fields for it. Leave blank unless your request needs action-specific scope data.</p>
          </details>
          <button type="submit" disabled={busy !== null || optionsLoading || Boolean(projectOptionsError) || Boolean(profileOptionsError) || projects.length === 0 || profiles.length === 0} className="min-h-11 rounded border border-amber-700 px-3 py-2 text-sm text-amber-200 hover:bg-amber-950 disabled:cursor-not-allowed disabled:opacity-50">{busy === "CFO model override" ? "Submitting…" : "Request override"}</button>
        </form>

        <form className="min-w-0 space-y-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3" aria-labelledby="cto-worker-run-heading" onSubmit={(event) => void dispatchWorker(event)}>
          <h3 id="cto-worker-run-heading" className="text-xs font-semibold uppercase tracking-wide text-slate-300">CTO · worker run</h3>
          <label className="block text-sm text-slate-300" htmlFor="cto-worker">Worker
            <select id="cto-worker" value={workerId} onChange={(event) => setWorkerId(event.target.value)} disabled={optionsLoading || Boolean(workerOptionsError) || workers.length === 0} aria-required="true" aria-describedby="cto-worker-help" className={FIELD_CLASS}>
              <option value="">{optionsLoading ? "Loading workers…" : "Choose a worker"}</option>
              {workers.map((worker) => <option key={worker.id} value={worker.id}>{worker.name || worker.worker_id || "Unnamed worker"}{worker.worker_id && worker.name ? ` · ${worker.worker_id}` : ""} · {worker.status || "status unknown"}</option>)}
            </select>
          </label>
          <p id="cto-worker-help" className={`-mt-2 text-xs ${workerOptionsError ? "text-rose-300" : "text-slate-500"}`} aria-live="polite">{workerChoiceMessage}</p>
          <label className="block text-sm text-slate-300" htmlFor="cto-task-type">Task type
            <input id="cto-task-type" required maxLength={256} value={taskType} onChange={(event) => setTaskType(event.target.value)} placeholder="code.edit" className={MONO_FIELD_CLASS} />
          </label>
          <p className="-mt-2 text-xs text-slate-500">The worker registry does not publish a task-type catalogue; use the task type supported by the selected worker.</p>
          <label className="block text-sm text-slate-300" htmlFor="cto-idempotency-key">Idempotency key
            <div className="mt-1 flex flex-wrap gap-2">
              <input id="cto-idempotency-key" required maxLength={256} value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} autoComplete="off" className={`${MONO_FIELD_CLASS} mt-0 min-w-0 flex-1`} />
              <button type="button" onClick={() => setIdempotencyKey(createIdempotencyKey())} disabled={busy !== null} className="min-h-11 rounded border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50">Generate new key</button>
            </div>
          </label>
          <p className="-mt-2 text-xs text-slate-500">Generated for this request. Keep it unchanged when retrying; create a new key for a distinct run.</p>
          <label className="block text-sm text-slate-300" htmlFor="cto-project">Project (optional)
            <select id="cto-project" value={dispatchProjectId} onChange={(event) => setDispatchProjectId(event.target.value)} disabled={optionsLoading} aria-describedby="cto-project-help" className={FIELD_CLASS}>
              <option value="">No project</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name || "Unnamed project"} · {project.state || "state unknown"} · {project.id.slice(0, 8)}</option>)}
            </select>
          </label>
          <p id="cto-project-help" className={`-mt-2 text-xs ${projectOptionsError ? "text-amber-200" : "text-slate-500"}`} aria-live="polite">{projectOptionsError || (optionsLoading ? "Loading project choices…" : "Choose No project or a current project.")}</p>
          <details className="rounded border border-slate-800 px-3 py-2">
            <summary className="min-h-11 cursor-pointer py-3 text-sm text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70">Advanced task input (JSON object)</summary>
            <label className="block text-sm text-slate-300" htmlFor="cto-task-input">Task input (JSON object)
              <textarea id="cto-task-input" value={taskInput} onChange={(event) => setTaskInput(event.target.value)} rows={4} spellCheck={false} aria-describedby="cto-task-input-help" className={`${MONO_FIELD_CLASS} resize-y`} />
            </label>
            <p id="cto-task-input-help" className="mt-1 text-xs text-slate-500">The API has no schema for this worker-specific object. Use an object such as {`{"issue":"Fix the failing test"}`}; arrays and scalar JSON values are rejected.</p>
          </details>
          <button type="submit" disabled={busy !== null || optionsLoading || Boolean(workerOptionsError) || workers.length === 0} className="min-h-11 rounded border border-amber-700 px-3 py-2 text-sm text-amber-200 hover:bg-amber-950 disabled:cursor-not-allowed disabled:opacity-50">{busy === "CTO worker dispatch" ? "Dispatching…" : "Dispatch governed run"}</button>
        </form>

        <form className="min-w-0 space-y-3 rounded-lg border border-rose-900/60 bg-slate-950/60 p-3" aria-labelledby="ceo-privileged-action-heading" onSubmit={(event) => void requestPrivilegedAction(event)}>
          <h3 id="ceo-privileged-action-heading" className="text-xs font-semibold uppercase tracking-wide text-rose-200">CEO · privileged action</h3>
          <p className="text-xs text-slate-400">This request enters the audited privileged-action approval gate. The approval service remains authoritative for the action and its payload.</p>
          <details className="rounded border border-rose-900/60 px-3 py-2">
            <summary className="min-h-11 cursor-pointer py-3 text-sm text-rose-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/70">Advanced action and payload</summary>
            <p className="mb-3 text-xs text-slate-500">The executive API publishes no action catalogue or payload field schema. Enter an action recognized by CEO policy and its required object fields.</p>
            <label className="block text-sm text-slate-300" htmlFor="ceo-action">Action
              <input id="ceo-action" maxLength={256} value={ceoAction} onChange={(event) => setCeoAction(event.target.value)} placeholder="security.override_cso" className={MONO_FIELD_CLASS} />
            </label>
            <label className="mt-3 block text-sm text-slate-300" htmlFor="ceo-payload">Payload (JSON object)
              <textarea id="ceo-payload" value={ceoPayload} onChange={(event) => setCeoPayload(event.target.value)} rows={5} spellCheck={false} aria-describedby="ceo-payload-help" className={`${MONO_FIELD_CLASS} resize-y`} />
            </label>
            <p id="ceo-payload-help" className="mt-1 text-xs text-slate-500">Enter a JSON object such as {`{"ticket":"SEC-12"}`}.</p>
          </details>
          <label className="flex items-start gap-2 text-sm text-slate-300"><input type="checkbox" checked={confirmPrivileged} onChange={(event) => setConfirmPrivileged(event.target.checked)} className="mt-0.5 min-h-11 min-w-11" />I understand this enters the audited privileged-action approval gate.</label>
          <button type="submit" disabled={busy !== null || !confirmPrivileged} className="min-h-11 rounded border border-rose-700 px-3 py-2 text-sm text-rose-200 hover:bg-rose-950 disabled:cursor-not-allowed disabled:opacity-50">{busy === "CEO privileged action" ? "Submitting…" : "Request privileged action"}</button>
        </form>
      </div>
      {result && <div className={`mt-4 rounded-lg border p-3 text-sm ${result.error ? "border-rose-900 bg-rose-950/30 text-rose-200" : "border-emerald-900 bg-emerald-950/20 text-emerald-200"}`} role="status" aria-live="polite" aria-label="Executive action result"><div className="mb-1 font-semibold">{result.label}</div><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{typeof result.body === "string" ? result.body : JSON.stringify(result.body, null, 2)}</pre></div>}
    </section>
  );
}
