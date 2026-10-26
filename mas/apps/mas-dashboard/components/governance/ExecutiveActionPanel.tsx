"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
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

type ExecutiveActionField = {
  name: string;
  label: string;
  control: "select" | "text" | "textarea" | "number";
  required: boolean;
  max_length?: number | null;
  minimum?: number | null;
  maximum?: number | null;
  option_source?: "projects" | "workers" | "model_profiles" | null;
  help_text?: string | null;
};

type PrivilegedActionOption = {
  action: string;
  label: string;
  risk: string;
  requires_approval: boolean;
  fields: ExecutiveActionField[];
};

type ExecutiveActionCatalogue = {
  schema_version: "aiat.executive-actions.catalogue.v1";
  cfo_model_override_fields: ExecutiveActionField[];
  cto_worker_run_fields: ExecutiveActionField[];
  ceo_privileged_actions: PrivilegedActionOption[];
};

const EXECUTIVE_ACTION_SCHEMA = "aiat.executive-action.v1";
const EXECUTIVE_ACTION_CATALOGUE_SCHEMA = "aiat.executive-actions.catalogue.v1";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const FIELD_CLASS = "mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-900 px-2 py-2 text-base text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70 disabled:cursor-not-allowed disabled:opacity-60";
const MONO_FIELD_CLASS = `${FIELD_CLASS} font-mono`;

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

type PendingPrivilegedActionReceipt = {
  action: string;
  risk: string;
  recordId: string | null;
};

function pendingPrivilegedActionReceipt(value: unknown): PendingPrivilegedActionReceipt | null {
  const envelope = objectRecord(value);
  const result = objectRecord(envelope?.result);
  const evidence = objectRecord(envelope?.evidence);
  if (envelope?.schema_version !== EXECUTIVE_ACTION_SCHEMA || result?.decision !== "pending_approval") return null;

  const resultId = typeof result.record_id === "string" ? result.record_id : null;
  const evidenceId = typeof evidence?.record_id === "string" ? evidence.record_id : null;
  const candidateId = resultId && evidenceId && resultId !== evidenceId
    ? null
    : resultId ?? evidenceId;
  return {
    action: typeof evidence?.action === "string" ? evidence.action : "CEO privileged action",
    risk: typeof result.risk === "string" ? result.risk : "not reported",
    recordId: candidateId && UUID_PATTERN.test(candidateId) ? candidateId : null,
  };
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

async function readOptionList(path: string, label: string, signal?: AbortSignal): Promise<unknown[]> {
  const response = await fetch(path, { cache: "no-store", signal });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) throw new Error(apiErrorMessage(payload, response.status, `${label} could not be loaded`));
  if (!Array.isArray(payload)) throw new Error(`${label} returned an unexpected response. Refresh the choices and try again.`);
  return payload;
}

async function readActionCatalogue(signal?: AbortSignal): Promise<ExecutiveActionCatalogue> {
  const response = await fetch("/api/executive/actions/catalogue", { cache: "no-store", signal });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) throw new Error(apiErrorMessage(payload, response.status, "Executive action fields could not be loaded"));
  const body = objectRecord(payload);
  if (
    body?.schema_version !== EXECUTIVE_ACTION_CATALOGUE_SCHEMA
    || !Array.isArray(body.cfo_model_override_fields)
    || !Array.isArray(body.cto_worker_run_fields)
    || !Array.isArray(body.ceo_privileged_actions)
  ) {
    throw new Error("Executive action fields returned an unexpected response. Refresh the choices and try again.");
  }
  return body as unknown as ExecutiveActionCatalogue;
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
  const [catalogue, setCatalogue] = useState<ExecutiveActionCatalogue | null>(null);
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [projectOptionsError, setProjectOptionsError] = useState<string | null>(null);
  const [workerOptionsError, setWorkerOptionsError] = useState<string | null>(null);
  const [profileOptionsError, setProfileOptionsError] = useState<string | null>(null);
  const [catalogueError, setCatalogueError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [reason, setReason] = useState("");
  const [workerId, setWorkerId] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [taskType, setTaskType] = useState("code.edit");
  const [taskPrompt, setTaskPrompt] = useState("");
  const [runBudgetUsd, setRunBudgetUsd] = useState("");
  const [runTimeoutSeconds, setRunTimeoutSeconds] = useState("");
  const [dispatchProjectId, setDispatchProjectId] = useState("");
  const [guidedCeoAction, setGuidedCeoAction] = useState("");
  const [ceoFieldValues, setCeoFieldValues] = useState<Record<string, string>>({});
  const [confirmPrivileged, setConfirmPrivileged] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);

  const loadActionOptions = useCallback(async (signal?: AbortSignal) => {
    setOptionsLoading(true);
    const [projectResult, workerResult, profileResult, catalogueResult] = await Promise.allSettled([
      readOptionList("/api/projects?limit=1000", "Project choices", signal),
      readOptionList("/api/workers", "Worker choices", signal),
      readOptionList("/api/governance/model-profiles", "Model Profile choices", signal),
      readActionCatalogue(signal),
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
    if (catalogueResult.status === "fulfilled") {
      setCatalogue(catalogueResult.value);
      setCatalogueError(null);
    } else {
      setCatalogue(null);
      setCatalogueError(catalogueResult.reason instanceof Error
        ? catalogueResult.reason.message
        : "Executive action fields could not be loaded. Refresh action choices and try again.");
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
      const body: ExecutiveCFOModelOverrideRequest = {
        project_id: normalizedProjectId,
        requested_profile_id: normalizedProfileId,
        requested_by: "office_cfo",
        reason: normalizedReason,
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
      const normalizedPrompt = taskPrompt.trim();
      if (!normalizedPrompt) throw new Error("Enter task instructions for the selected worker.");
      if (normalizedPrompt.length > 20_000) throw new Error("Task instructions must be 20,000 characters or fewer.");
      const budgetUsd = runBudgetUsd.trim() ? Number(runBudgetUsd) : undefined;
      if (budgetUsd !== undefined && (!Number.isFinite(budgetUsd) || budgetUsd <= 0)) {
        throw new Error("Maximum run cost must be a positive amount in USD. Leave it blank to use the existing budget policy.");
      }
      const timeoutSeconds = runTimeoutSeconds.trim() ? Number(runTimeoutSeconds) : undefined;
      if (timeoutSeconds !== undefined && (!Number.isSafeInteger(timeoutSeconds) || timeoutSeconds < 1)) {
        throw new Error("Execution timeout must be a positive whole number of seconds.");
      }
      const body: ExecutiveCTOWorkerRunRequest = {
        requested_by: "office_cto",
        dispatch: {
          worker_id: normalizedWorkerId,
          idempotency_key: normalizedKey,
          task_type: normalizedTaskType,
          prompt: normalizedPrompt,
          project_id: normalizedProjectId || null,
          ...(budgetUsd !== undefined ? { budget_usd: budgetUsd } : {}),
          ...(timeoutSeconds !== undefined ? { timeout_seconds: timeoutSeconds } : {}),
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
      const action = guidedCeoAction.trim();
      const selectedAction = catalogue?.ceo_privileged_actions.find((item) => item.action === action && item.requires_approval);
      if (!selectedAction) throw new Error("Choose a supported approval-gated action from the current catalogue.");
      const payload: Record<string, string> = {};
      for (const field of selectedAction.fields) {
        const normalized = (ceoFieldValues[field.name] ?? "").trim();
        if (field.required && !normalized) throw new Error(`${field.label} is required.`);
        if (field.max_length && normalized.length > field.max_length) {
          throw new Error(`${field.label} must be ${field.max_length.toLocaleString()} characters or fewer.`);
        }
        if (normalized) payload[field.name] = normalized;
      }
      const body: ExecutiveCEOPrivilegedActionRequest = {
        action,
        requested_by: "exec_ceo",
        payload,
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
  const approvalActions = catalogue?.ceo_privileged_actions.filter((item) => item.requires_approval) ?? [];
  const selectedPrivilegedAction = approvalActions.find((item) => item.action === guidedCeoAction) ?? null;
  const privilegedFieldsReady = Boolean(selectedPrivilegedAction?.fields.every((field) => {
    const value = (ceoFieldValues[field.name] ?? "").trim();
    return !field.required || value.length > 0;
  }));
  const taskPromptField = catalogue?.cto_worker_run_fields.find((field) => field.name === "prompt");
  const pendingApprovalReceipt = result?.label === "CEO privileged action" && !result.error
    ? pendingPrivilegedActionReceipt(result.body)
    : null;

  return (
    <section className="rounded-xl border border-amber-900/60 bg-slate-900/70 p-5 xl:col-span-2" aria-labelledby="executive-actions-heading">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h2 id="executive-actions-heading" className="text-sm font-semibold text-white">Executive actions</h2>
        <span className="font-mono text-[11px] text-slate-500">aiat.executive-action.v1</span>
      </div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-3xl text-xs text-slate-400">Operator-authenticated requests are recorded by the control plane. Response records contain decision identifiers; privileged requests still require the human approval shown by the gate.</p>
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
          <button type="submit" disabled={busy !== null || optionsLoading || Boolean(catalogueError) || !catalogue || Boolean(projectOptionsError) || Boolean(profileOptionsError) || projects.length === 0 || profiles.length === 0} className="min-h-11 rounded border border-amber-700 px-3 py-2 text-sm text-amber-200 hover:bg-amber-950 disabled:cursor-not-allowed disabled:opacity-50">{busy === "CFO model override" ? "Submitting…" : "Request override"}</button>
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
          <label className="block text-sm text-slate-300" htmlFor="cto-task-prompt">Task instructions
            <textarea id="cto-task-prompt" required maxLength={taskPromptField?.max_length ?? 20_000} value={taskPrompt} onChange={(event) => setTaskPrompt(event.target.value)} rows={5} placeholder="Describe the task and the expected result." aria-describedby="cto-task-prompt-help" className={`${FIELD_CLASS} resize-y`} />
          </label>
          <p id="cto-task-prompt-help" className="-mt-2 text-xs text-slate-500">{taskPromptField?.help_text || "Instructions are sent to the selected worker as its prompt."}</p>
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
          <label className="block text-sm text-slate-300" htmlFor="cto-run-budget">Maximum run cost (USD, optional)
            <input id="cto-run-budget" type="number" min="0.01" step="0.01" value={runBudgetUsd} onChange={(event) => setRunBudgetUsd(event.target.value)} inputMode="decimal" placeholder="Use existing budget policy" aria-describedby="cto-run-budget-help" className={FIELD_CLASS} />
          </label>
          <p id="cto-run-budget-help" className="-mt-2 text-xs text-slate-500">A positive value requests a per-run cost reservation. Leave blank to use existing company and project budget policy; zero is not an unset value.</p>
          <label className="block text-sm text-slate-300" htmlFor="cto-run-timeout">Execution timeout (seconds, optional)
            <input id="cto-run-timeout" type="number" min="1" max="86400" step="1" value={runTimeoutSeconds} onChange={(event) => setRunTimeoutSeconds(event.target.value)} inputMode="numeric" placeholder="Use worker/runtime policy" aria-describedby="cto-run-timeout-help" className={FIELD_CLASS} />
          </label>
          <p id="cto-run-timeout-help" className="-mt-2 text-xs text-slate-500">Set a positive whole number up to 86,400 seconds to bound this run; leave blank to use worker/runtime policy.</p>
          <button type="submit" disabled={busy !== null || optionsLoading || Boolean(catalogueError) || !catalogue || Boolean(workerOptionsError) || workers.length === 0 || !taskPrompt.trim()} className="min-h-11 rounded border border-amber-700 px-3 py-2 text-sm text-amber-200 hover:bg-amber-950 disabled:cursor-not-allowed disabled:opacity-50">{busy === "CTO worker dispatch" ? "Dispatching…" : "Dispatch governed run"}</button>
        </form>

        <form className="min-w-0 space-y-3 rounded-lg border border-rose-900/60 bg-slate-950/60 p-3" aria-labelledby="ceo-privileged-action-heading" onSubmit={(event) => void requestPrivilegedAction(event)}>
          <h3 id="ceo-privileged-action-heading" className="text-xs font-semibold uppercase tracking-wide text-rose-200">CEO · privileged action</h3>
          <p className="text-xs text-slate-400">A request is recorded for a separate human decision. Submitting here does not dispatch or execute the action.</p>
          <label className="block text-sm text-slate-300" htmlFor="ceo-guided-action">Approval-gated action
            <select
              id="ceo-guided-action"
              required
              value={guidedCeoAction}
              onChange={(event) => { setGuidedCeoAction(event.target.value); setCeoFieldValues({}); }}
              disabled={optionsLoading || Boolean(catalogueError) || approvalActions.length === 0}
              aria-required="true"
              aria-describedby="ceo-guided-action-help"
              className={FIELD_CLASS}
            >
              <option value="">{optionsLoading ? "Loading action fields…" : "Choose an action"}</option>
              {approvalActions.map((item) => (
                <option key={item.action} value={item.action}>{item.label} · {item.risk} risk</option>
              ))}
            </select>
          </label>
          <p id="ceo-guided-action-help" className="text-xs text-slate-500" aria-live="polite">
            {catalogueError || (approvalActions.length === 0 ? "No approval-gated actions are available." : "Fields come from the current privileged-action policy. The policy gate and human approval remain authoritative.")}
          </p>
          {selectedPrivilegedAction?.fields.map((field) => (
            <label key={field.name} className="block text-sm text-slate-300" htmlFor={`ceo-field-${field.name}`}>
              {field.label}
              {field.control === "textarea" ? (
                <textarea
                  id={`ceo-field-${field.name}`}
                  required={field.required}
                  maxLength={field.max_length ?? undefined}
                  value={ceoFieldValues[field.name] ?? ""}
                  onChange={(event) => setCeoFieldValues((current) => ({ ...current, [field.name]: event.target.value }))}
                  rows={3}
                  className={`${FIELD_CLASS} resize-y`}
                />
              ) : (
                <input
                  id={`ceo-field-${field.name}`}
                  required={field.required}
                  maxLength={field.max_length ?? undefined}
                  value={ceoFieldValues[field.name] ?? ""}
                  onChange={(event) => setCeoFieldValues((current) => ({ ...current, [field.name]: event.target.value }))}
                  autoComplete="off"
                  className={FIELD_CLASS}
                />
              )}
              {field.help_text && <span className="mt-1 block text-xs text-slate-500">{field.help_text}</span>}
            </label>
          ))}
          <p className="-mt-2 text-xs text-slate-500">Do not include credentials, secret values, or access tokens. Requests wait for a separate human decision.</p>
          <label className="flex items-start gap-2 text-sm text-slate-300"><input type="checkbox" checked={confirmPrivileged} onChange={(event) => setConfirmPrivileged(event.target.checked)} className="mt-0.5 min-h-11 min-w-11" />I understand this enters the audited privileged-action approval gate.</label>
          <button type="submit" disabled={busy !== null || optionsLoading || Boolean(catalogueError) || !confirmPrivileged || !selectedPrivilegedAction || !privilegedFieldsReady} className="min-h-11 rounded border border-rose-700 px-3 py-2 text-sm text-rose-200 hover:bg-rose-950 disabled:cursor-not-allowed disabled:opacity-50">{busy === "CEO privileged action" ? "Submitting…" : "Request privileged action"}</button>
        </form>
      </div>
      {result && <div className={`mt-4 rounded-lg border p-3 text-sm ${result.error ? "border-rose-900 bg-rose-950/30 text-rose-200" : "border-emerald-900 bg-emerald-950/20 text-emerald-200"}`} role="status" aria-live="polite" aria-label="Executive action result">
        <div className="mb-1 font-semibold">{result.label}</div>
        {pendingApprovalReceipt ? (
          <>
            <section className="rounded-lg border border-amber-800/70 bg-amber-950/20 p-3" aria-label="Privileged action pending approval">
              <h3 className="font-semibold text-amber-100">Request awaiting human approval</h3>
              <p className="mt-1 text-xs text-amber-100/80">This request is queued for a separate human decision. Opening it will not approve or dispatch the action.</p>
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                <div><dt className="text-amber-100/60">Action</dt><dd className="mt-1 break-all font-mono text-amber-50">{pendingApprovalReceipt.action}</dd></div>
                <div><dt className="text-amber-100/60">Risk</dt><dd className="mt-1 text-amber-50">{pendingApprovalReceipt.risk}</dd></div>
                <div className="sm:col-span-2"><dt className="text-amber-100/60">Approval record</dt><dd className="mt-1 break-all font-mono text-amber-50">{pendingApprovalReceipt.recordId ?? "Record ID unavailable"}</dd></div>
              </dl>
              {pendingApprovalReceipt.recordId ? (
                <Link
                  href={`/approvals?request_id=${encodeURIComponent(pendingApprovalReceipt.recordId)}`}
                  className="mt-3 inline-flex min-h-11 items-center rounded border border-amber-700 px-3 py-2 text-sm font-medium text-amber-100 hover:bg-amber-900/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300"
                >
                  Review this request in the Approval inbox
                </Link>
              ) : <p className="mt-3 text-xs text-amber-100/70">Open the Approval inbox to locate the current request.</p>}
            </section>
            <details className="mt-3 rounded border border-slate-700 px-3 py-2">
              <summary className="min-h-11 cursor-pointer py-3 text-xs text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70">Inspect response record</summary>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{typeof result.body === "string" ? result.body : JSON.stringify(result.body, null, 2)}</pre>
            </details>
          </>
        ) : <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{typeof result.body === "string" ? result.body : JSON.stringify(result.body, null, 2)}</pre>}
      </div>}
    </section>
  );
}
