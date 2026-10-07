"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { BrainCircuit, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type ModelVersion = {
  version: string;
  provider_id: string;
  exact_model_id: string;
  status: string;
  capabilities?: string[];
  cost_per_1k_input_usd?: number;
  cost_per_1k_output_usd?: number;
  max_cost_usd?: number | null;
  max_tokens_per_request?: number | null;
};

type ModelProfile = {
  profile_id: string;
  purpose: string;
  status: string;
  versions?: ModelVersion[];
};

type ExecutiveReconciliation = {
  projects?: { usage?: { total_cost_usd?: number; llm_calls?: number } };
  budgets?: {
    configured_count?: number;
    available_usd?: number;
    used_usd?: number;
    overages?: unknown[];
    reservation_reconciliation?: { anomaly_count?: number };
  };
};

type RejectedCandidate = {
  profile_id: string;
  version?: string | null;
  exact_model_id?: string | null;
  reasons: string[];
};

type PreviewResult = {
  authorized: boolean;
  snapshot?: {
    resolved_profile_id: string;
    resolved_profile_version: string;
    provider_id: string;
    exact_model_id: string;
    cost_estimate_usd: number;
    required_capabilities?: string[];
    fallback_chain?: string[];
  };
  error?: { code: string; message: string };
  rejected_candidates?: RejectedCandidate[];
};

const CAPABILITIES = [
  "tool_calling",
  "structured_output",
  "streaming",
  "vision",
  "reasoning",
  "embedding",
  "local",
] as const;

function formatUsd(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Unavailable";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 8,
  }).format(value);
}

async function responseError(response: Response): Promise<string> {
  try {
    const data = await response.json() as { error?: unknown; detail?: unknown };
    const candidate = data.error ?? data.detail;
    if (typeof candidate === "string") return candidate;
    if (candidate !== undefined) return JSON.stringify(candidate);
  } catch {
    // Keep the user-facing fallback bounded when the proxy returned non-JSON.
  }
  return `Request failed (${response.status}). Retry, or check Governance access and control-plane health.`;
}

export default function ModelResolutionPreview() {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [reconciliation, setReconciliation] = useState<ExecutiveReconciliation | null>(null);
  const [profilesError, setProfilesError] = useState("");
  const [budgetError, setBudgetError] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [taskType, setTaskType] = useState("coding");
  const [profileId, setProfileId] = useState("");
  const [promptTokens, setPromptTokens] = useState(2000);
  const [outputTokens, setOutputTokens] = useState(1000);
  const [budgetUsd, setBudgetUsd] = useState("");
  const [capabilities, setCapabilities] = useState<string[]>(["tool_calling"]);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setProfilesError("");
    setBudgetError("");
    const [profileResult, reconciliationResult] = await Promise.allSettled([
      fetch("/api/governance/model-profiles", { cache: "no-store" }),
      fetch("/api/executive/reconciliation", { cache: "no-store" }),
    ]);

    let denied = false;
    if (profileResult.status === "fulfilled") {
      const response = profileResult.value;
      if (response.status === 401 || response.status === 403) denied = true;
      else if (response.ok) {
        const data = await response.json() as unknown;
        setProfiles(Array.isArray(data) ? data as ModelProfile[] : []);
      } else setProfilesError(await responseError(response));
    } else setProfilesError("Model Profiles could not be loaded. Check control-plane connectivity and retry.");

    if (reconciliationResult.status === "fulfilled") {
      const response = reconciliationResult.value;
      if (response.status === 401 || response.status === 403) denied = true;
      else if (response.ok) setReconciliation(await response.json() as ExecutiveReconciliation);
      else setBudgetError(await responseError(response));
    } else setBudgetError("Usage and budget reconciliation could not be loaded. Check control-plane connectivity and retry.");

    setAccessDenied(denied);
    setLoading(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  function toggleCapability(capability: string) {
    setCapabilities((current) => current.includes(capability)
      ? current.filter((item) => item !== capability)
      : [...current, capability]);
  }

  async function submitPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPreview(null);
    setPreviewError("");
    setPreviewing(true);
    const body = {
      task_type: taskType.trim(),
      requested_profile_id: profileId || null,
      prompt_tokens: promptTokens,
      expected_output_tokens: outputTokens,
      ...(budgetUsd.trim() ? { budget_usd: Number(budgetUsd) } : {}),
      task_required_capabilities: capabilities,
    };

    try {
      const response = await fetch("/api/governance/model-profiles/resolve-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (response.status === 401 || response.status === 403) {
        setAccessDenied(true);
        setPreviewError("This operator identity is not authorized to preview model resolution.");
      } else if (!response.ok) setPreviewError(await responseError(response));
      else setPreview(await response.json() as PreviewResult);
    } catch {
      setPreviewError("Model resolution preview could not reach the control plane. Retry when it is available.");
    } finally {
      setPreviewing(false);
    }
  }

  const budgets = reconciliation?.budgets;
  const usage = reconciliation?.projects?.usage;

  return (
    <main className="dashboard-page min-h-full space-y-6 p-6 lg:p-8" aria-label="Model governance">
      <PageHeader
        icon="brain"
        title="Model Profiles"
        description="Inspect governed model versions, recorded spend, budget state, and a non-dispatch resolution estimate."
        actions={(
          <button type="button" onClick={() => void refresh()} disabled={loading} aria-label="Refresh model governance" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        )}
      />

      {accessDenied && (
        <section role="region" aria-label="Model governance access status" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5">
          <h2 className="font-semibold text-amber-100">Model governance access denied</h2>
          <p className="mt-2 text-sm text-amber-200/80">The current operator identity cannot read or preview model governance. No profile or budget state is inferred.</p>
        </section>
      )}
      {profilesError && !accessDenied && <ErrorBanner tone="error" title="Model Profiles unavailable" action={<button type="button" onClick={() => void refresh()} className="min-h-11 rounded border border-current px-3 text-xs">Retry</button>}>{profilesError}</ErrorBanner>}

      <section className="grid gap-3 sm:grid-cols-3" aria-label="Recorded model usage and budgets">
        <article className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h2 className="text-xs font-medium uppercase tracking-wide text-slate-400">Recorded project usage</h2>
          <p className="mt-2 text-2xl font-semibold text-white">{formatUsd(usage?.total_cost_usd)}</p>
          <p className="mt-1 text-xs text-slate-500">{usage?.llm_calls ?? 0} recorded model calls across projects</p>
        </article>
        <article className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h2 className="text-xs font-medium uppercase tracking-wide text-slate-400">Aggregate budget available</h2>
          <p className="mt-2 text-2xl font-semibold text-white">{formatUsd(budgets?.available_usd)}</p>
          <p className="mt-1 text-xs text-slate-500">{budgets?.configured_count ?? 0} configured company budget(s); {formatUsd(budgets?.used_usd)} used</p>
        </article>
        <article className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h2 className="text-xs font-medium uppercase tracking-wide text-slate-400">Budget attention</h2>
          <p className={`mt-2 text-2xl font-semibold ${(budgets?.overages?.length ?? 0) + (budgets?.reservation_reconciliation?.anomaly_count ?? 0) > 0 ? "text-amber-300" : "text-emerald-300"}`}>
            {(budgets?.overages?.length ?? 0) + (budgets?.reservation_reconciliation?.anomaly_count ?? 0)} finding(s)
          </p>
          <p className="mt-1 text-xs text-slate-500">Budget overages and reservation reconciliation anomalies</p>
        </article>
      </section>
      {budgetError && <ErrorBanner tone="warning" title="Recorded usage and budgets unavailable">{budgetError}</ErrorBanner>}
      {reconciliation && budgets?.configured_count === 0 && <p className="text-sm text-amber-200" role="status">No company budget rows are configured. The displayed budget balance is not an unlimited-spend authorization.</p>}

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="profiles-heading">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white"><BrainCircuit size={16} className="text-cyan-300" /><h2 id="profiles-heading">Persisted Model Profiles</h2></div>
        {loading && profiles.length === 0 ? <p className="text-sm text-slate-400">Loading governed profiles…</p> : profiles.length === 0 ? <p className="text-sm text-slate-500">No persisted Model Profiles are available.</p> : (
          <div className="grid gap-3 lg:grid-cols-2" role="list" aria-label="Persisted model profile versions">
            {profiles.map((profile) => (
              <article key={profile.profile_id} role="listitem" className="rounded-lg border border-slate-800 bg-slate-950/60 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="font-mono text-sm text-white">{profile.profile_id}</h3>
                  <span className={`text-xs uppercase ${profile.status.toLowerCase() === "approved" ? "text-emerald-300" : "text-amber-300"}`}>{profile.status}</span>
                </div>
                <p className="mt-1 text-sm text-slate-400">{profile.purpose}</p>
                {(profile.versions ?? []).length === 0 ? <p className="mt-3 text-xs text-slate-500">No versions are recorded.</p> : (
                  <div className="mt-3 space-y-2">
                    {(profile.versions ?? []).map((version) => (
                      <div key={version.version} className="rounded border border-slate-800 px-3 py-2 text-xs">
                        <div className="flex flex-wrap justify-between gap-2"><span className="font-mono text-slate-200">{version.version} · {version.provider_id}/{version.exact_model_id}</span><span className="uppercase text-slate-400">{version.status}</span></div>
                        <div className="mt-1 text-slate-500">Input {formatUsd(version.cost_per_1k_input_usd)}/1k · output {formatUsd(version.cost_per_1k_output_usd)}/1k · max estimate {formatUsd(version.max_cost_usd ?? undefined)}</div>
                        {version.max_tokens_per_request != null && <div className="mt-1 text-slate-500">Request limit: {version.max_tokens_per_request.toLocaleString()} tokens</div>}
                      </div>
                    ))}
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="preview-heading">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white"><h2 id="preview-heading">Resolution preview</h2><span className="rounded border border-cyan-500/30 bg-cyan-500/5 px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-cyan-200">No dispatch</span></div>
        <p className="mb-4 text-sm text-slate-400">Preview estimate, not dispatch authorization. It checks the persisted profile versions against the inputs below. Dispatch separately loads organization, project, worker, steward, and task policy and reserves the authoritative budget.</p>
        <form className="grid gap-4 md:grid-cols-2" onSubmit={submitPreview}>
          <label className="block text-sm text-slate-300">Task type
            <input value={taskType} onChange={(event) => setTaskType(event.target.value)} required maxLength={120} className="mt-1 min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-sm text-white" />
          </label>
          <label className="block text-sm text-slate-300">Requested profile
            <select value={profileId} onChange={(event) => setProfileId(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-sm text-white">
              <option value="">Any eligible approved profile</option>
              {profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_id} · {profile.status}</option>)}
            </select>
          </label>
          <label className="block text-sm text-slate-300">Estimated prompt tokens
            <input type="number" min={0} step={1} value={promptTokens} onChange={(event) => setPromptTokens(Number(event.target.value))} className="mt-1 min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-sm text-white" />
          </label>
          <label className="block text-sm text-slate-300">Estimated output tokens
            <input type="number" min={0} step={1} value={outputTokens} onChange={(event) => setOutputTokens(Number(event.target.value))} className="mt-1 min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-sm text-white" />
          </label>
          <label className="block text-sm text-slate-300 md:col-span-2">Preview budget cap (USD, optional)
            <input type="number" min={0} step="any" value={budgetUsd} onChange={(event) => setBudgetUsd(event.target.value)} placeholder="Leave blank to omit a request-level cap" className="mt-1 min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-sm text-white placeholder:text-slate-600" />
            <span className="mt-1 block text-xs text-slate-500">This cap is an estimate input. It does not replace company or project budget checks.</span>
          </label>
          <fieldset className="md:col-span-2">
            <legend className="text-sm text-slate-300">Required capabilities</legend>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
              {CAPABILITIES.map((capability) => (
                <label key={capability} className="inline-flex min-h-11 items-center gap-2 text-xs text-slate-300">
                  <input type="checkbox" checked={capabilities.includes(capability)} onChange={() => toggleCapability(capability)} className="h-4 w-4 accent-cyan-400" />
                  <span>{capability.replaceAll("_", " ")}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="md:col-span-2">
            <button type="submit" disabled={previewing || loading || accessDenied} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50">
              <RefreshCw size={14} className={previewing ? "animate-spin" : ""} /> {previewing ? "Checking profiles…" : "Preview resolution"}
            </button>
          </div>
        </form>

        {previewError && <div className="mt-4" role="alert"><ErrorBanner tone="error" title="Resolution preview failed">{previewError}</ErrorBanner></div>}
        {preview?.authorized && preview.snapshot && (
          <section className="mt-5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4" aria-label="Resolution estimate result">
            <h3 className="font-semibold text-emerald-200">Candidate found for the entered inputs</h3>
            <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
              <div><dt className="text-xs text-slate-500">Resolved profile</dt><dd className="mt-1 font-mono text-slate-100">{preview.snapshot.resolved_profile_id} · {preview.snapshot.resolved_profile_version}</dd></div>
              <div><dt className="text-xs text-slate-500">Provider and model</dt><dd className="mt-1 font-mono text-slate-100">{preview.snapshot.provider_id}/{preview.snapshot.exact_model_id}</dd></div>
              <div><dt className="text-xs text-slate-500">Estimated model cost</dt><dd className="mt-1 font-semibold text-white">{formatUsd(preview.snapshot.cost_estimate_usd)}</dd></div>
              <div><dt className="text-xs text-slate-500">Required capabilities</dt><dd className="mt-1 text-slate-200">{preview.snapshot.required_capabilities?.join(", ") || "None"}</dd></div>
            </dl>
            {preview.snapshot.fallback_chain && preview.snapshot.fallback_chain.length > 1 && <p className="mt-3 text-xs text-slate-400">Eligible fallback chain: {preview.snapshot.fallback_chain.join(" → ")}</p>}
          </section>
        )}
        {preview && !preview.authorized && (
          <section className="mt-5 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4" aria-label="Resolution blockers">
            <h3 className="font-semibold text-amber-200">No profile satisfies these preview inputs</h3>
            <p className="mt-1 text-sm text-amber-100/80">{preview.error?.code ?? "NO_COMPLIANT_MODEL"}: {preview.error?.message ?? "Review candidate blockers below."}</p>
            {(preview.rejected_candidates ?? []).length === 0 ? <p className="mt-3 text-sm text-slate-400">Check that an approved profile exists and that the requested profile ID is valid.</p> : (
              <ul className="mt-3 space-y-2" aria-label="Rejected profile reasons">
                {preview.rejected_candidates?.map((candidate, index) => (
                  <li key={`${candidate.profile_id}:${candidate.version ?? index}`} className="rounded border border-slate-800 bg-slate-950/50 p-3 text-sm">
                    <div className="font-mono text-slate-200">{candidate.profile_id}{candidate.version ? ` · ${candidate.version}` : ""}{candidate.exact_model_id ? ` · ${candidate.exact_model_id}` : ""}</div>
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-amber-100/80">{candidate.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-xs text-slate-400">Try a compatible profile, lower the token estimate, or adjust the preview cap. Dispatch will still apply durable policy and current budget checks.</p>
          </section>
        )}
      </section>
    </main>
  );
}
