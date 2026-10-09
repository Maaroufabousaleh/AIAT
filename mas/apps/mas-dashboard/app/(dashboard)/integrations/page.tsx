"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { RefreshCw, Search } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import type {
  PMConnectionCreateRequest,
  PMLifecyclePlanCreateRequest,
  PMProjectProvisioningApplyRequest,
  PMProjectProvisioningRequest,
  ProjectProvisioningPlan,
} from "@/lib/generated/orchestrator-api";

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

type ProjectSummary = {
  id: string;
  name: string;
  state?: string;
};

type ProjectProvisioningPlanResult = {
  project_id: string;
  connection_id: string;
  plan: ProjectProvisioningPlan;
  plan_digest: string;
  blockers?: string[];
  manual_actions?: string[];
};

type LifecycleTargetType = "pm_connection" | "pm_binding";
type LifecycleStatus = "DISABLED" | "SHADOW" | "READ_ONLY" | "ACTIVE" | "DRAINING";

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
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState("");
  const [projectBindings, setProjectBindings] = useState<Array<Record<string, unknown>>>([]);
  const [bindingsLoading, setBindingsLoading] = useState(false);
  const [bindingsError, setBindingsError] = useState<string | null>(null);
  const [providerKind, setProviderKind] = useState("youtrack");
  const [connectionName, setConnectionName] = useState("");
  const [connectionBaseUrl, setConnectionBaseUrl] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [mappingProfile, setMappingProfile] = useState("dedicated_project");
  const [externalProjectId, setExternalProjectId] = useState("");
  const [provisioningPlan, setProvisioningPlan] = useState<ProjectProvisioningPlanResult | null>(null);
  const [reviewedProvisioningDigest, setReviewedProvisioningDigest] = useState("");
  const [lifecycleTargetType, setLifecycleTargetType] = useState<LifecycleTargetType>("pm_connection");
  const [desiredLifecycleStatus, setDesiredLifecycleStatus] = useState<LifecycleStatus>("SHADOW");
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
  const bindingRequestSequence = useRef(0);

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

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    setProjectsError(null);
    try {
      const response = await requestJson<unknown>("/api/projects?limit=1000");
      const payload = Array.isArray(response) ? response : record(response).projects;
      const loadedProjects = Array.isArray(payload)
        ? payload.map(record).flatMap((project) => {
          const id = text(project.id, "");
          const name = text(project.name, "");
          return id && name ? [{ id, name, state: text(project.state, "") }] : [];
        })
        : [];
      setProjects(loadedProjects);
      setProjectId((current) =>
        loadedProjects.some((project) => project.id === current)
          ? current
          : loadedProjects[0]?.id ?? "",
      );
    } catch (reason) {
      setProjectsError(reason instanceof IntegrationApiError
        ? `Projects could not be loaded (HTTP ${reason.status}).`
        : "Projects could not be loaded.");
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  const loadProjectBindings = useCallback(async (selectedProjectId: string) => {
    const sequence = ++bindingRequestSequence.current;
    if (!UUID_PATTERN.test(selectedProjectId)) {
      setProjectBindings([]);
      setBindingsError(null);
      setBindingsLoading(false);
      setBindingId("");
      return;
    }
    setBindingsLoading(true);
    setBindingsError(null);
    try {
      const response = await requestJson<unknown>(
        `/api/integrations/pm/projects/${encodeURIComponent(selectedProjectId)}/bindings`,
      );
      if (sequence !== bindingRequestSequence.current) return;
      const bindings = Array.isArray(response)
        ? response.map(record).filter((binding) => text(binding.project_id, "") === selectedProjectId)
        : [];
      setProjectBindings(bindings);
      setBindingId((current) =>
        bindings.some((binding) => text(binding.id, "") === current)
          ? current
          : text(bindings.find((binding) => text(binding.connection_id, "") === connectionId)?.id, ""),
      );
    } catch (reason) {
      if (sequence !== bindingRequestSequence.current) return;
      setProjectBindings([]);
      setBindingsError(reason instanceof IntegrationApiError
        ? `Project bindings could not be loaded (HTTP ${reason.status}).`
        : "Project bindings could not be loaded.");
    } finally {
      if (sequence === bindingRequestSequence.current) setBindingsLoading(false);
    }
  }, [connectionId]);

  const submitMutation = async (
    path: string,
    payload: Record<string, unknown>,
    action: string,
    onSuccess?: (result: Record<string, unknown>) => void | Promise<void>,
    successMessage?: string | ((result: Record<string, unknown>) => string),
  ) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await requestJson<Record<string, unknown>>(path, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await onSuccess?.(result);
      await refresh(connectionFilterId);
      setNotice(typeof successMessage === "function"
        ? successMessage(result)
        : successMessage ?? `${action} completed.`);
    } catch (reason) {
      reportActionFailure(reason, action);
    } finally {
      setBusy(false);
    }
  };

  const createConnection = () => {
    const payload: PMConnectionCreateRequest = {
      provider_kind: providerKind,
      display_name: connectionName.trim(),
      base_url: connectionBaseUrl.trim(),
      credential_ref: credentialRef.trim(),
      capability_profile: "pm",
      config: {},
    };
    void submitMutation(
      "/api/integrations/pm/connections",
      payload,
      "Provider connection creation",
      (result) => {
        const createdId = text(result.id, "");
        if (UUID_PATTERN.test(createdId)) {
          setConnectionId(createdId);
          setConnectionFilterId("");
          setBindingId("");
        }
        setConnectionName("");
        setConnectionBaseUrl("");
        setCredentialRef("");
      },
      (result) => `Connection ${text(result.display_name, connectionName.trim())} created in ${text(result.status, "DISABLED")} state.`,
    );
  };

  const generateProvisioningPlan = () => {
    if (!UUID_PATTERN.test(projectId) || !UUID_PATTERN.test(connectionId)) return;
    const payload: PMProjectProvisioningRequest = {
      connection_id: connectionId,
      mapping_profile: mappingProfile,
      ...(externalProjectId.trim() ? { external_project_id: externalProjectId.trim() } : {}),
    };
    setProvisioningPlan(null);
    setReviewedProvisioningDigest("");
    void submitMutation(
      `/api/integrations/pm/projects/${encodeURIComponent(projectId)}/provisioning/plan`,
      payload,
      "Project provisioning plan",
      (result) => {
        const planResult = result as ProjectProvisioningPlanResult;
        setProvisioningPlan(planResult);
        setReviewedProvisioningDigest("");
      },
      "Project provisioning plan created. Review its actions and digest before applying.",
    );
  };

  const applyProvisioningPlan = () => {
    if (!provisioningPlan || reviewedProvisioningDigest !== provisioningPlan.plan_digest) return;
    if ((provisioningPlan.blockers ?? []).length > 0 || mutationBlocked) return;
    const confirmed = window.confirm(
      `Apply this exact project provisioning plan? Digest: ${provisioningPlan.plan_digest}`,
    );
    if (!confirmed) return;
    const payload: PMProjectProvisioningApplyRequest = {
      plan: provisioningPlan.plan,
      plan_digest: provisioningPlan.plan_digest,
      confirm: true,
    };
    void submitMutation(
      `/api/integrations/pm/projects/${encodeURIComponent(projectId)}/provisioning/apply`,
      payload,
      "Project provisioning",
      async (result) => {
        const binding = record(result.binding);
        const createdBindingId = text(binding.id, "");
        if (UUID_PATTERN.test(createdBindingId)) setBindingId(createdBindingId);
        await loadProjectBindings(projectId);
        setProvisioningPlan(null);
        setReviewedProvisioningDigest("");
      },
      (result) => {
        const binding = record(result.binding);
        return `Project provisioned and bound in ${text(binding.status || result.status, "unknown")} state.`;
      },
    );
  };

  const generateLifecyclePlan = () => {
    if (!UUID_PATTERN.test(connectionId)) return;
    if (lifecycleTargetType === "pm_binding" && !UUID_PATTERN.test(bindingId)) return;
    const payload: PMLifecyclePlanCreateRequest = {
      target_type: lifecycleTargetType,
      connection_id: connectionId,
      ttl_seconds: 3600,
      ...(lifecycleTargetType === "pm_connection"
        ? { desired_connection_status: desiredLifecycleStatus }
        : { binding_id: bindingId, desired_binding_status: desiredLifecycleStatus }),
    };
    setReviewedDigest("");
    void submitMutation(
      "/api/integrations/pm/lifecycle-plans",
      payload,
      "Lifecycle plan generation",
      () => setReviewedDigest(""),
      `Lifecycle plan to ${desiredLifecycleStatus} created. Review blockers and the exact digest, then approve it before applying.`,
    );
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
  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => {
    setProjectBindings([]);
    setBindingId("");
    void loadProjectBindings(projectId);
    return () => { bindingRequestSequence.current += 1; };
  }, [projectId, loadProjectBindings]);

  const mutationBlocked = accessDenied || stale || loading || busy;
  const selectedProject = projects.find((project) => project.id === projectId);
  const connectionBindings = projectBindings.filter(
    (binding) => text(binding.project_id, "") === projectId
      && text(binding.connection_id, "") === connectionId,
  );

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
          {!accessDenied && (
            <section
              className="rounded-xl border border-slate-800 bg-slate-900/70 p-4"
              aria-labelledby="pm-connection-create-heading"
            >
              <div>
                <h2 id="pm-connection-create-heading" className="text-sm font-semibold text-white">Create provider connection</h2>
                <p className="mt-1 text-xs text-slate-400">Register a supported PM provider using an AIAT credential reference. This form never accepts or displays a credential value.</p>
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                <label className="grid gap-1 text-xs text-slate-300">
                  Provider
                  <select
                    aria-label="Provider kind"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                    value={providerKind}
                    disabled={mutationBlocked}
                    onChange={(event) => setProviderKind(event.target.value)}
                  >
                    <option value="youtrack">YouTrack</option>
                    <option value="github">GitHub</option>
                    <option value="fake">Fake provider (local testing)</option>
                  </select>
                </label>
                <label className="grid gap-1 text-xs text-slate-300">
                  Display name
                  <input
                    aria-label="Connection display name"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-3 text-sm text-slate-100 disabled:opacity-50"
                    value={connectionName}
                    disabled={mutationBlocked}
                    maxLength={200}
                    onChange={(event) => setConnectionName(event.target.value)}
                  />
                </label>
                <label className="grid gap-1 text-xs text-slate-300">
                  Provider base URL
                  <input
                    aria-label="Provider base URL"
                    type="url"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-3 text-sm text-slate-100 disabled:opacity-50"
                    value={connectionBaseUrl}
                    disabled={mutationBlocked}
                    maxLength={1000}
                    onChange={(event) => setConnectionBaseUrl(event.target.value)}
                  />
                </label>
                <label className="grid gap-1 text-xs text-slate-300">
                  Credential reference
                  <input
                    aria-label="Credential reference"
                    autoComplete="off"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-3 text-sm text-slate-100 disabled:opacity-50"
                    value={credentialRef}
                    disabled={mutationBlocked}
                    maxLength={200}
                    placeholder="Existing AIAT credential name"
                    onChange={(event) => setCredentialRef(event.target.value)}
                  />
                  <span className="text-[11px] text-slate-500">Create the secret in Credentials first; enter only its reference here.</span>
                </label>
                <div className="flex items-end">
                  <button
                    type="button"
                    className="min-h-11 w-full rounded bg-cyan-700 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={mutationBlocked || !connectionName.trim() || !connectionBaseUrl.trim() || !credentialRef.trim()}
                    onClick={createConnection}
                  >Create connection</button>
                </div>
              </div>
            </section>
          )}

          {!accessDenied && (
            <section
              className="space-y-4 rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-4"
              aria-labelledby="pm-project-setup-heading"
            >
              <div>
                <h2 id="pm-project-setup-heading" className="text-sm font-semibold text-cyan-100">Provision and bind a project</h2>
                <p className="mt-1 text-xs text-cyan-200/70">Generate a provider plan first. Applying the exact reviewed plan may create or adopt a provider project and records its AIAT binding.</p>
              </div>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <label className="grid gap-1 text-xs text-slate-300">
                  AIAT project
                  <select
                    aria-label="Project to provision"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                    value={projectId}
                    disabled={mutationBlocked || projectsLoading || projects.length === 0}
                    onChange={(event) => {
                      setProjectId(event.target.value);
                      setProvisioningPlan(null);
                      setReviewedProvisioningDigest("");
                    }}
                  >
                    <option value="">{projectsLoading ? "Loading projects…" : "Select a project"}</option>
                    {projects.map((project) => (
                      <option key={project.id} value={project.id}>{project.name} · {project.state || "unknown"}</option>
                    ))}
                  </select>
                </label>
                <label className="grid gap-1 text-xs text-slate-300">
                  Provider connection
                  <select
                    aria-label="Project provider connection"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                    value={connectionId}
                    disabled={mutationBlocked || data.connections.length === 0}
                    onChange={(event) => {
                      setConnectionId(event.target.value);
                      setBindingId("");
                      setProvisioningPlan(null);
                      setReviewedProvisioningDigest("");
                    }}
                  >
                    <option value="">Select a connection</option>
                    {data.connections.map((connection) => (
                      <option key={text(connection.id)} value={text(connection.id, "")}>{text(connection.display_name || connection.id)} · {text(connection.status)}</option>
                    ))}
                  </select>
                </label>
                <label className="grid gap-1 text-xs text-slate-300">
                  Mapping profile
                  <select
                    aria-label="Project mapping profile"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                    value={mappingProfile}
                    disabled={mutationBlocked}
                    onChange={(event) => {
                      setMappingProfile(event.target.value);
                      setProvisioningPlan(null);
                      setReviewedProvisioningDigest("");
                    }}
                  >
                    <option value="dedicated_project">Dedicated provider project</option>
                    <option value="umbrella_issues">Explicit shared project / issues</option>
                  </select>
                </label>
                <label className="grid gap-1 text-xs text-slate-300">
                  Existing provider project ID (optional)
                  <input
                    aria-label="Existing provider project ID"
                    className="min-h-11 rounded border border-slate-700 bg-slate-950 px-3 text-sm text-slate-100 disabled:opacity-50"
                    value={externalProjectId}
                    disabled={mutationBlocked}
                    maxLength={240}
                    onChange={(event) => {
                      setExternalProjectId(event.target.value);
                      setProvisioningPlan(null);
                      setReviewedProvisioningDigest("");
                    }}
                  />
                </label>
              </div>
              {projectsError && (
                <div className="flex flex-wrap items-center gap-3">
                  <p role="alert" className="text-sm text-amber-200">{projectsError}</p>
                  <button
                    type="button"
                    className="min-h-9 rounded border border-slate-700 px-3 py-1 text-xs font-medium text-slate-200 hover:bg-slate-900 disabled:cursor-wait disabled:opacity-60"
                    disabled={projectsLoading}
                    aria-busy={projectsLoading}
                    onClick={() => void loadProjects()}
                  >{projectsLoading ? "Retrying project list…" : "Retry project list"}</button>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="min-h-11 rounded border border-cyan-700 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={mutationBlocked || projectsLoading || !UUID_PATTERN.test(projectId) || !UUID_PATTERN.test(connectionId)}
                  onClick={generateProvisioningPlan}
                >Generate project provisioning plan</button>
                {selectedProject && <span className="text-xs text-slate-400">Selected project: {selectedProject.name}</span>}
              </div>
              {provisioningPlan && (() => {
                const plan = provisioningPlan.plan;
                const actions = Array.isArray(plan.actions) ? plan.actions : [];
                const blockers = provisioningPlan.blockers ?? plan.blockers ?? [];
                const manualActions = provisioningPlan.manual_actions ?? plan.manual_actions ?? [];
                const destructive = actions.some((action) => action.destructive === true);
                return (
                  <article aria-label="Project provisioning plan" className="rounded border border-slate-800 bg-slate-950/70 p-3 text-sm text-slate-200">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="font-semibold">Plan {text(plan.plan_id)}</h3>
                      <span className="font-mono text-xs text-cyan-200">Digest: {provisioningPlan.plan_digest}</span>
                    </div>
                    <p className="mt-1 text-xs text-slate-400">{text(plan.provider_kind)} · project {text(plan.project_id)} · profile {text(plan.mapping_profile)}</p>
                    <ul className="mt-3 list-disc space-y-1 pl-5 text-xs">
                      {actions.map((action, index) => (
                        <li key={`${action.action}-${action.resource}-${index}`}>{action.action}: {action.resource}{action.manual ? " (manual step)" : ""}{action.destructive ? " (destructive)" : ""}</li>
                      ))}
                      {!actions.length && <li>No provider actions are included in this plan.</li>}
                    </ul>
                    {blockers.length > 0 && <div role="alert" className="mt-3 rounded border border-rose-700/50 bg-rose-900/20 p-2 text-xs text-rose-200"><strong>Plan blocked:</strong> {blockers.join("; ")}</div>}
                    {manualActions.length > 0 && <div className="mt-3 rounded border border-amber-700/50 bg-amber-900/20 p-2 text-xs text-amber-100"><strong>Manual steps:</strong> {manualActions.join("; ")}</div>}
                    {destructive && <p role="alert" className="mt-3 text-xs text-rose-200">This plan includes a destructive action and cannot be applied from this workspace.</p>}
                    <label className="mt-3 flex min-h-11 items-center gap-2 text-xs text-slate-300">
                      <input
                        type="checkbox"
                        checked={reviewedProvisioningDigest === provisioningPlan.plan_digest}
                        disabled={mutationBlocked || blockers.length > 0 || destructive}
                        onChange={(event) => setReviewedProvisioningDigest(event.target.checked ? provisioningPlan.plan_digest : "")}
                      />
                      I reviewed this exact project provisioning digest
                    </label>
                    <button
                      type="button"
                      className="mt-3 min-h-11 rounded bg-cyan-700 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={mutationBlocked || blockers.length > 0 || destructive || reviewedProvisioningDigest !== provisioningPlan.plan_digest}
                      onClick={applyProvisioningPlan}
                    >Apply reviewed plan and create binding</button>
                  </article>
                );
              })()}
              <div aria-label="Project provider bindings" className="rounded border border-slate-800 bg-slate-950/40 p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Connection mappings for selected project</h3>
                    <p className="mt-1 text-xs text-slate-500">Canonical project-to-provider bindings for the selected connection.</p>
                  </div>
                  {selectedProject && <Link className="text-xs text-cyan-300 underline underline-offset-2 hover:text-cyan-100" href={`/projects/${encodeURIComponent(selectedProject.id)}`}>Open project</Link>}
                </div>
                {bindingsLoading ? <p role="status" className="mt-2 text-xs text-slate-500">Loading project bindings…</p> : null}
                {!bindingsLoading && bindingsError && <p role="alert" className="mt-2 text-xs text-amber-200">{bindingsError} Use Refresh to retry the canonical read.</p>}
                {!bindingsLoading && bindingsError && <button type="button" className="mt-2 min-h-9 rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-900" onClick={() => void loadProjectBindings(projectId)}>Retry binding read</button>}
                {!bindingsLoading && !bindingsError && !selectedProject && <p className="mt-2 text-xs text-slate-500">Select an AIAT project to inspect its provider mappings.</p>}
                {!bindingsLoading && !bindingsError && selectedProject && !connectionId && <p className="mt-2 text-xs text-slate-500">Select a provider connection above to inspect its mapping rows for this project.</p>}
                {!bindingsLoading && !bindingsError && selectedProject && connectionId && connectionBindings.length === 0 && <p className="mt-2 text-xs text-slate-500">No provider mappings are recorded for this project and connection.</p>}
                {connectionBindings.map((binding) => {
                  const blockers = Array.isArray(binding.activation_blockers)
                    ? binding.activation_blockers.slice(0, 5).map(String)
                    : [];
                  return (
                    <article key={text(binding.id)} aria-label={`Project provider binding ${text(binding.id)}`} className="mt-3 rounded border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-mono text-slate-100">{text(binding.id, "Unknown binding")}</span>
                        <span className="rounded border border-slate-700 px-2 py-1">{text(binding.status, "unknown status")} · {text(binding.provisioning_state, "unprovisioned")}</span>
                      </div>
                      <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
                        <div><dt className="text-slate-500">Provider project</dt><dd>{text(binding.external_project_key || binding.external_project_id, "Pending")}</dd></div>
                        <div><dt className="text-slate-500">Repository</dt><dd>{text(binding.external_repository, "—")}</dd></div>
                        <div><dt className="text-slate-500">Mapping profile</dt><dd>{text(binding.mapping_profile)}</dd></div>
                        <div><dt className="text-slate-500">Direction</dt><dd>{text(binding.direction)}</dd></div>
                      </dl>
                      {blockers.length > 0 && <p className="mt-2 text-amber-200">Activation blockers: {blockers.join("; ")}</p>}
                    </article>
                  );
                })}
              </div>
            </section>
          )}

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
                <p className="mt-1 text-xs text-blue-200/70">The PM outbox read API lists due PENDING rows only. It has no connection-scoped FAILED or DEAD_LETTER collection/detail route; use the existing shared router queue for general message review.</p>
                <p className="mt-2 text-xs text-slate-400">The shared queue is not attributed to the selected provider connection. Replay controls are not exposed in this integration view.</p>
                <Link href="/dlq" className="mt-2 inline-flex min-h-10 items-center rounded border border-blue-700 px-3 py-2 text-xs font-semibold text-blue-200 hover:bg-blue-900/30">Open shared dead-letter review</Link>
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
              <p className="mt-1 text-xs text-cyan-200/70">Choose a connection or project binding transition. Plan generation records readiness checks; applying requires a fresh read, review of the exact digest, and operator approval.</p>
            </div>
            {!accessDenied && <div className="grid gap-3 md:grid-cols-4">
              <label className="grid gap-1 text-xs text-slate-300">
                Connection
                <select
                  aria-label="Lifecycle connection"
                  className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                  value={connectionId}
                  disabled={mutationBlocked || data.connections.length === 0}
                  onChange={(event) => {
                    setConnectionId(event.target.value);
                    setBindingId("");
                  }}
                >
                  <option value="">Select a connection</option>
                  {data.connections.map((connection) => (
                    <option key={text(connection.id)} value={text(connection.id, "")}>{text(connection.display_name || connection.id)} · {text(connection.status)}</option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-xs text-slate-300">
                Target
                <select
                  aria-label="Lifecycle target"
                  className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                  value={lifecycleTargetType}
                  disabled={mutationBlocked}
                  onChange={(event) => setLifecycleTargetType(event.target.value as LifecycleTargetType)}
                >
                  <option value="pm_connection">Provider connection</option>
                  <option value="pm_binding">Project binding</option>
                </select>
              </label>
              <label className="grid gap-1 text-xs text-slate-300">
                Project binding
                <select
                  aria-label="Lifecycle project binding"
                  className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                  value={bindingId}
                  disabled={mutationBlocked || lifecycleTargetType !== "pm_binding" || bindingsLoading || connectionBindings.length === 0}
                  onChange={(event) => setBindingId(event.target.value)}
                >
                  <option value="">{bindingsLoading ? "Loading bindings…" : "Select a project binding"}</option>
                  {connectionBindings.map((binding) => (
                    <option key={text(binding.id)} value={text(binding.id, "")}>{text(binding.external_project_key || binding.external_project_id, text(binding.id))} · {text(binding.status)}</option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-xs text-slate-300">
                Desired state
                <select
                  aria-label="Desired lifecycle state"
                  className="min-h-11 rounded border border-slate-700 bg-slate-950 px-2 text-sm text-slate-100 disabled:opacity-50"
                  value={desiredLifecycleStatus}
                  disabled={mutationBlocked}
                  onChange={(event) => setDesiredLifecycleStatus(event.target.value as LifecycleStatus)}
                >
                  <option value="DISABLED">DISABLED</option>
                  <option value="SHADOW">SHADOW</option>
                  <option value="READ_ONLY">READ_ONLY</option>
                  <option value="ACTIVE">ACTIVE</option>
                  <option value="DRAINING">DRAINING</option>
                </select>
              </label>
              <button
                type="button"
                className="min-h-11 self-end rounded bg-cyan-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                disabled={mutationBlocked || !UUID_PATTERN.test(connectionId) || (lifecycleTargetType === "pm_binding" && !UUID_PATTERN.test(bindingId))}
                onClick={generateLifecyclePlan}
              >Generate {desiredLifecycleStatus} plan</button>
            </div>}
            {accessDenied && <p className="text-sm text-amber-200/80" role="status">Lifecycle mutation controls are hidden until authorization is restored.</p>}
            <div className="space-y-3">
              {data.lifecyclePlans.map((entry) => {
                const plan = record(entry.plan);
                const digest = text(entry.plan_digest, "");
                const planId = text(plan.plan_id || entry.id, "unknown-plan");
                const status = text(entry.status || plan.status, "unknown");
                const actorMappings = trustedActorMappings(entry);
                const blockers = Array.isArray(plan.blockers) ? plan.blockers.map(String) : [];
                return (
                  <article key={planId} aria-label={`Lifecycle plan ${planId}`} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-slate-100">{planId}</span>
                      <span className="rounded-full border border-slate-700 px-2 py-1">{status}</span>
                    </div>
                    <div className="mt-2 break-all font-mono text-cyan-200">Digest: {digest}</div>
                    <p className="mt-1 text-slate-400">Target {text(plan.target_type)} · connection {text(plan.connection_id)} · binding {text(plan.binding_id)} · desired connection {text(plan.desired_connection_status)} · desired binding {text(plan.desired_binding_status)}</p>
                    {blockers.length > 0 && <p role="alert" className="mt-2 text-rose-200">Blocked: {blockers.join("; ")}</p>}
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
                        disabled={mutationBlocked || reviewedDigest !== digest || status !== "PLANNED" || blockers.length > 0}
                        onClick={() => void submitMutation(
                          `/api/integrations/pm/lifecycle-plans/${encodeURIComponent(planId)}/approve`,
                          { plan_digest: digest, reason: "Dashboard operator approval" },
                          "Lifecycle plan approval",
                          undefined,
                          "Lifecycle plan approved. Apply is still a separate, confirmed action.",
                        )}
                      >Approve</button>
                      <button
                        type="button"
                        className="min-h-11 rounded border border-amber-700 px-3 py-2 text-amber-300 disabled:opacity-50"
                        disabled={mutationBlocked || reviewedDigest !== digest || status !== "APPROVED" || blockers.length > 0}
                        onClick={() => {
                          if (!window.confirm(`Apply only this exact persisted lifecycle plan? Digest: ${digest}`)) return;
                          void submitMutation(
                            `/api/integrations/pm/lifecycle-plans/${encodeURIComponent(planId)}/apply`,
                            { plan_digest: digest, confirm: true },
                            "Lifecycle plan application",
                            async () => {
                              if (projectId) await loadProjectBindings(projectId);
                            },
                            (result) => `Lifecycle plan applied${result.application ? `: ${text(record(result.application).status, "complete")}` : " successfully"}.`,
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
