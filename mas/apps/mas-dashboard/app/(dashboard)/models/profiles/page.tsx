"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { BrainCircuit, RefreshCw, Save } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type CatalogueEntry = {
  model_id: string;
  provider_id: string;
  description?: string;
  profile_state?: string;
  max_context_tokens?: number;
  capabilities?: Record<string, unknown>;
};

type Catalogue = {
  entries?: CatalogueEntry[];
};

type ModelVersion = {
  id: string;
  version: string;
  provider_id: string;
  exact_model_id: string;
  status: string;
  capabilities?: string[];
};

type ActivationResult = {
  profile_id: string;
  version_id: string;
  version: string;
  version_status: string;
  already_approved: boolean;
  approval_record: {
    id: string;
    decided_by: string;
    reason: string;
    created_at: string;
    evidence?: { version_snapshot_sha256?: string };
  };
};

type ModelProfile = {
  profile_id: string;
  purpose: string;
  status: string;
  approved_provider_ids?: string[];
  versions?: ModelVersion[];
};

type PreviewResult = {
  authorized?: boolean;
  snapshot?: {
    resolved_profile_id: string;
    resolved_profile_version: string;
    provider_id: string;
    exact_model_id: string;
    cost_estimate_usd?: number;
    fallback_chain?: string[];
  };
  error?: { code?: string; message?: string };
  rejected_candidates?: Array<{
    profile_id: string;
    version?: string | null;
    reasons: string[];
  }>;
};

const FEATURE_KEYS = ["tool_calling", "structured_output", "vision", "reasoning", "streaming", "embedding"] as const;

function safeText(value: unknown): string {
  if (typeof value !== "string") return "Request was rejected. Check the profile values and retry.";
  return value
    .replace(/\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)\b/gi, "[redacted]")
    .slice(0, 500);
}

async function getError(response: Response): Promise<string> {
  let message = "Request was rejected.";
  try {
    const body = await response.json() as { error?: unknown; detail?: unknown };
    const candidate = body.error ?? body.detail;
    if (typeof candidate === "string") message = safeText(candidate);
    else if (candidate !== undefined) message = safeText(JSON.stringify(candidate));
  } catch {
    // Keep the status visible even when the proxy returned non-JSON.
  }
  return `${response.status}: ${message}`;
}

function hasCapability(entry: CatalogueEntry, feature: string): boolean {
  return entry.capabilities?.[feature] === true;
}

export default function ModelProfilesPage() {
  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([]);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [profileId, setProfileId] = useState("");
  const [purpose, setPurpose] = useState("");
  const [versionLabel, setVersionLabel] = useState("1.0.0");
  const [selectedModelKey, setSelectedModelKey] = useState("");
  const [createdProfile, setCreatedProfile] = useState<ModelProfile | null>(null);
  const [profileSaved, setProfileSaved] = useState(false);
  const [versionSaved, setVersionSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveNotice, setSaveNotice] = useState("");
  const [activationReasons, setActivationReasons] = useState<Record<string, string>>({});
  const [activationErrors, setActivationErrors] = useState<Record<string, string>>({});
  const [activatingVersionId, setActivatingVersionId] = useState<string | null>(null);
  const [activationResult, setActivationResult] = useState<ActivationResult | null>(null);
  const [previewProfileId, setPreviewProfileId] = useState("");
  const [taskType, setTaskType] = useState("coding");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    const [catalogueResult, profilesResult] = await Promise.allSettled([
      fetch("/api/governance/model-profiles/catalogue", { cache: "no-store" }),
      fetch("/api/governance/model-profiles", { cache: "no-store" }),
    ]);
    const errors: string[] = [];
    if (catalogueResult.status === "fulfilled" && catalogueResult.value.ok) {
      const data = await catalogueResult.value.json() as Catalogue;
      const entries = Array.isArray(data.entries) ? data.entries : [];
      setCatalogue(entries);
      setSelectedModelKey((current) => current || (entries[0] ? `${entries[0].provider_id}\n${entries[0].model_id}` : ""));
    } else {
      errors.push(catalogueResult.status === "fulfilled"
        ? `Catalogue request failed (${catalogueResult.value.status}).`
        : "Model catalogue could not be reached.");
    }
    if (profilesResult.status === "fulfilled" && profilesResult.value.ok) {
      const data = await profilesResult.value.json() as unknown;
      const nextProfiles = Array.isArray(data) ? data as ModelProfile[] : [];
      setProfiles(nextProfiles);
      setPreviewProfileId((current) => current || nextProfiles.find((item) => item.status.toLowerCase() === "approved")?.profile_id || nextProfiles[0]?.profile_id || "");
    } else {
      errors.push(profilesResult.status === "fulfilled"
        ? `Profiles request failed (${profilesResult.value.status}).`
        : "Model profiles could not be reached.");
    }
    setLoadError(errors.join(" "));
    setLoading(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const selectedModel = useMemo(() => catalogue.find((entry) => `${entry.provider_id}\n${entry.model_id}` === selectedModelKey) ?? null, [catalogue, selectedModelKey]);
  const previewProfiles = useMemo(() => {
    const rows = createdProfile ? [...profiles.filter((profile) => profile.profile_id !== createdProfile.profile_id), createdProfile] : profiles;
    return rows;
  }, [createdProfile, profiles]);

  function makeVersionBody(entry: CatalogueEntry) {
    const capabilities = FEATURE_KEYS.filter((feature) => hasCapability(entry, feature));
    return {
      version: versionLabel.trim(),
      provider_id: entry.provider_id,
      exact_model_id: entry.model_id,
      capabilities,
      context_window: entry.max_context_tokens ?? 0,
      tool_calling: hasCapability(entry, "tool_calling"),
      structured_output: hasCapability(entry, "structured_output"),
      vision: hasCapability(entry, "vision"),
      reasoning: hasCapability(entry, "reasoning"),
      streaming: hasCapability(entry, "streaming"),
      embedding: hasCapability(entry, "embedding"),
      status: "draft",
    };
  }

  async function saveVersion(profile: ModelProfile, entry: CatalogueEntry) {
    const response = await fetch(`/api/model-profiles/${encodeURIComponent(profile.profile_id)}/versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(makeVersionBody(entry)),
    });
    if (!response.ok) {
      setSaveError(await getError(response));
      return false;
    }
    const version = await response.json() as ModelVersion;
    const updated: ModelProfile = { ...profile, versions: [...(profile.versions ?? []).filter((item) => item.version !== version.version), version] };
    setCreatedProfile(updated);
    setProfiles((current) => [...current.filter((item) => item.profile_id !== updated.profile_id), updated]);
    setPreviewProfileId(updated.profile_id);
    setVersionSaved(true);
    setSaveError("");
    setSaveNotice(`Draft version ${version.version} saved for ${profile.profile_id}. It is not approved or active.`);
    return true;
  }

  async function activateVersion(profile: ModelProfile, version: ModelVersion) {
    const reason = (activationReasons[version.id] ?? "").trim();
    if (!reason) {
      setActivationErrors((current) => ({ ...current, [version.id]: "Enter a reason for approving this exact version." }));
      return;
    }
    setActivatingVersionId(version.id);
    setActivationErrors((current) => ({ ...current, [version.id]: "" }));
    setActivationResult(null);
    try {
      const response = await fetch(
        `/api/model-profiles/${encodeURIComponent(profile.profile_id)}/versions/${encodeURIComponent(version.id)}/activate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        },
      );
      if (!response.ok) {
        const message = await getError(response);
        setActivationErrors((current) => ({ ...current, [version.id]: message }));
        return;
      }
      const result = await response.json() as ActivationResult;
      setActivationResult(result);
      setActivationReasons((current) => ({ ...current, [version.id]: "" }));
      await refresh();
    } catch {
      setActivationErrors((current) => ({ ...current, [version.id]: "Approval could not reach AIAT. Refresh before retrying." }));
    } finally {
      setActivatingVersionId(null);
    }
  }

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveError("");
    setSaveNotice("");
    if (!selectedModel) {
      setSaveError("Choose a model from the current catalogue before saving a profile.");
      return;
    }
    setSaving(true);
    try {
      let profile = createdProfile;
      if (!profileSaved || !profile) {
        const response = await fetch("/api/model-profiles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            profile_id: profileId.trim(),
            purpose: purpose.trim(),
            approved_provider_ids: [selectedModel.provider_id],
            required_capabilities: [],
            fallback_profile_ids: [],
            status: "draft",
          }),
        });
        if (!response.ok) {
          setSaveError(await getError(response));
          return;
        }
        const result = await response.json() as ModelProfile;
        profile = { ...result, status: "draft", versions: [] };
        setCreatedProfile(profile);
        setProfileSaved(true);
        setProfiles((current) => [...current.filter((item) => item.profile_id !== profile?.profile_id), profile as ModelProfile]);
        setPreviewProfileId(profile.profile_id);
      }
      const saved = await saveVersion(profile, selectedModel);
      if (!saved && profile) {
        setSaveNotice(`Draft profile ${profile.profile_id} was saved. Correct the version details or retry adding its draft version.`);
      }
    } catch {
      setSaveError("The profile request could not reach AIAT. The draft may already have been saved; refresh before retrying.");
    } finally {
      setSaving(false);
    }
  }

  async function runPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPreview(null);
    setPreviewError("");
    setPreviewing(true);
    try {
      const response = await fetch("/api/model-profiles/resolve-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_type: taskType.trim(), requested_profile_id: previewProfileId || null, prompt_tokens: 1000, expected_output_tokens: 500 }),
      });
      if (!response.ok) setPreviewError(await getError(response));
      else setPreview(await response.json() as PreviewResult);
    } catch {
      setPreviewError("Resolution preview could not reach AIAT. Retry when the control plane is available.");
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <main className="dashboard-page min-h-full space-y-6 p-6 lg:p-8" aria-label="Model profile setup">
      <PageHeader
        icon="brain"
        title="Create Model Profile"
        description="Prepare a draft model profile and immutable draft version, then inspect governed resolution without dispatching a model request."
        actions={(
          <button type="button" onClick={() => void refresh()} disabled={loading} aria-label="Refresh model profile catalogue" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        )}
      />

      {loadError && <ErrorBanner tone="warning" title="Model catalogue unavailable">{loadError}</ErrorBanner>}

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="draft-profile-title">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white"><BrainCircuit size={16} className="text-cyan-300" /><h2 id="draft-profile-title">Profile and version</h2></div>
        <p className="mb-5 max-w-3xl text-sm text-slate-400">This workflow writes status <code className="text-slate-200">draft</code> for both records. The selected provider is added to this profile’s approved-provider list. No credentials, provider settings, or model requests are sent.</p>
        <form onSubmit={createDraft} className="grid gap-4 md:grid-cols-2" aria-label="Create draft model profile">
          <label className="space-y-1 text-sm text-slate-300">Profile ID
            <input required maxLength={160} value={profileId} disabled={profileSaved} onChange={(event) => setProfileId(event.target.value)} placeholder="coding-internal" className="min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-white" />
          </label>
          <label className="space-y-1 text-sm text-slate-300">Purpose
            <input required maxLength={500} value={purpose} disabled={profileSaved} onChange={(event) => setPurpose(event.target.value)} placeholder="Governed coding tasks" className="min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-white" />
          </label>
          <label className="space-y-1 text-sm text-slate-300">Draft version
            <input required maxLength={100} value={versionLabel} disabled={versionSaved} onChange={(event) => setVersionLabel(event.target.value)} className="min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-white" />
          </label>
          <label className="space-y-1 text-sm text-slate-300 md:col-span-2">Provider and exact model from catalogue
            <select required disabled={loading || profileSaved || catalogue.length === 0} value={selectedModelKey} onChange={(event) => setSelectedModelKey(event.target.value)} className="min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-white">
              {catalogue.length === 0 && <option value="">No catalogue models available</option>}
              {catalogue.map((entry) => <option key={`${entry.provider_id}:${entry.model_id}`} value={`${entry.provider_id}\n${entry.model_id}`}>{entry.provider_id} / {entry.model_id}{entry.description ? ` — ${entry.description}` : ""}</option>)}
            </select>
          </label>
          {selectedModel && <p className="md:col-span-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-400">Provider allow-list entry: <code className="text-slate-200">{selectedModel.provider_id}</code>. Version <code className="text-slate-200">1.0.0</code> will remain draft. Catalogue state: {selectedModel.profile_state ?? "available"}.</p>}
          {saveError && <div role="alert" className="md:col-span-2 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">Save failed ({saveError}). {saveError.startsWith("409:") ? "This profile or version already exists; refresh and choose a new profile ID or version." : saveError.startsWith("422:") ? "Review the profile values and the provider/model selection." : "You can correct the values and retry."}</div>}
          {saveNotice && <p role="status" className="md:col-span-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm text-emerald-200">{saveNotice}</p>}
          <div className="md:col-span-2 flex flex-wrap items-center gap-3">
            <button type="submit" disabled={saving || loading || !selectedModel || (profileSaved && versionSaved)} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-cyan-600 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-50"><Save size={15} />{saving ? "Saving draft…" : profileSaved && !versionSaved ? "Retry draft version" : "Create draft profile and version"}</button>
            {profileSaved && <span className="text-xs text-slate-400">Draft profile saved: <code>{createdProfile?.profile_id}</code></span>}
          </div>
        </form>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="governed-profiles-title">
        <h2 id="governed-profiles-title" className="text-sm font-semibold text-white">Governed profiles and versions</h2>
        <p className="mt-1 text-sm text-slate-400">Draft versions remain unavailable to model resolution. An operator can approve one exact immutable version with a recorded reason; no model request is sent.</p>
        {profiles.length === 0 ? (
          <p className="mt-4 rounded-lg border border-slate-800 bg-slate-950/60 p-4 text-sm text-slate-400">No model profiles are configured yet. Create a draft profile and version above.</p>
        ) : (
          <div className="mt-4 space-y-3" role="list" aria-label="Model profiles and versions">
            {profiles.map((profile) => (
              <article key={profile.profile_id} className="rounded-lg border border-slate-800 bg-slate-950/50 p-4" role="listitem" aria-label={`Model profile ${profile.profile_id}`}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-medium text-white">{profile.profile_id}</h3>
                  <span className="text-xs uppercase tracking-wide text-slate-400">Profile: {profile.status}</span>
                </div>
                <p className="mt-1 text-sm text-slate-400">{profile.purpose}</p>
                {(profile.versions ?? []).length === 0 ? (
                  <p className="mt-3 text-sm text-slate-500">No versions yet.</p>
                ) : (
                  <div className="mt-3 space-y-3">
                    {(profile.versions ?? []).map((version) => (
                      <div key={version.id || version.version} className="rounded-lg border border-slate-800 p-3" aria-label={`Profile version ${profile.profile_id} ${version.version}`}>
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <p className="font-mono text-sm text-slate-200">{version.version} · {version.provider_id}/{version.exact_model_id}</p>
                          <span className="text-xs uppercase tracking-wide text-slate-400">Version: {version.status}</span>
                        </div>
                        {version.status.toLowerCase() === "draft" && version.id && (
                          <div className="mt-3 space-y-2">
                            <label className="block space-y-1 text-sm text-slate-300">
                              <span>Approval reason for {profile.profile_id} {version.version}</span>
                              <textarea
                                required
                                minLength={1}
                                maxLength={4_000}
                                value={activationReasons[version.id] ?? ""}
                                onChange={(event) => setActivationReasons((current) => ({ ...current, [version.id]: event.target.value }))}
                                aria-label={`Approval reason for ${profile.profile_id} ${version.version}`}
                                className="min-h-20 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                              />
                            </label>
                            {activationErrors[version.id] && <p role="alert" className="text-sm text-rose-200">{activationErrors[version.id]}</p>}
                            <button
                              type="button"
                              onClick={() => void activateVersion(profile, version)}
                              disabled={!activationReasons[version.id]?.trim() || activatingVersionId !== null}
                              className="inline-flex min-h-11 items-center rounded-lg border border-emerald-500/40 px-4 py-2 text-sm font-semibold text-emerald-100 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {activatingVersionId === version.id ? "Approving…" : "Approve and activate exact version"}
                            </button>
                          </div>
                        )}
                        {activationResult?.version_id === version.id && (
                          <div role="status" className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm text-emerald-100">
                            <p>{activationResult.already_approved ? "This version was already approved." : "Exact version approved and activated."} Decision {activationResult.approval_record.id} by {activationResult.approval_record.decided_by} at {activationResult.approval_record.created_at}.</p>
                            <p className="mt-1 text-emerald-200">Reason: {activationResult.approval_record.reason}</p>
                            {activationResult.approval_record.evidence?.version_snapshot_sha256 && <p className="mt-1 break-all font-mono text-xs text-emerald-300">Version snapshot SHA-256: {activationResult.approval_record.evidence.version_snapshot_sha256}</p>}
                            <p className="mt-1 text-xs text-emerald-300">No model provider was contacted.</p>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="dry-run-title">
        <h2 id="dry-run-title" className="text-sm font-semibold text-white">Resolution preview</h2>
        <p className="mt-1 text-sm text-slate-400">This uses AIAT’s resolver dry-run. It does not contact a model provider or count as dispatch authorization.</p>
        <form onSubmit={runPreview} className="mt-4 grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end" aria-label="Preview model resolution">
          <label className="space-y-1 text-sm text-slate-300">Requested profile
            <select value={previewProfileId} onChange={(event) => setPreviewProfileId(event.target.value)} className="min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-white">
              <option value="">Use policy selection</option>
              {previewProfiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_id} · {profile.status}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-sm text-slate-300">Task type
            <input required maxLength={100} value={taskType} onChange={(event) => setTaskType(event.target.value)} className="min-h-11 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 text-white" />
          </label>
          <button type="submit" disabled={previewing || !taskType.trim()} className="min-h-11 rounded-lg border border-cyan-500/50 px-4 py-2 text-sm font-semibold text-cyan-100 hover:bg-cyan-500/10 disabled:opacity-50">{previewing ? "Previewing…" : "Preview resolution"}</button>
        </form>
        {previewError && <p role="alert" className="mt-4 text-sm text-rose-200">{previewError}</p>}
        {preview && (preview.authorized && preview.snapshot ? (
          <section role="region" aria-label="Resolution preview result" className="mt-4 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4 text-sm">
            <h3 className="font-semibold text-emerald-200">Dry-run resolved</h3>
            <p className="mt-2 text-slate-200">{preview.snapshot.resolved_profile_id} · {preview.snapshot.resolved_profile_version} · {preview.snapshot.provider_id}/{preview.snapshot.exact_model_id}</p>
            <p className="mt-1 text-slate-400">Estimated cost: {typeof preview.snapshot.cost_estimate_usd === "number" ? `$${preview.snapshot.cost_estimate_usd.toFixed(6)}` : "not available"}. No model request was sent.</p>
          </section>
        ) : (
          <section role="region" aria-label="Resolution preview result" className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm">
            <h3 className="font-semibold text-amber-200">No eligible model in this dry-run</h3>
            <p className="mt-2 text-slate-300">{preview.error?.code ? `${preview.error.code}: ` : ""}{preview.error?.message ?? "The requested profile is draft or does not satisfy the effective policy."}</p>
            {(preview.rejected_candidates ?? []).map((candidate) => <p key={`${candidate.profile_id}:${candidate.version}`} className="mt-2 text-xs text-slate-400">{candidate.profile_id}{candidate.version ? ` · ${candidate.version}` : ""}: {candidate.reasons.join("; ")}</p>)}
            <p className="mt-2 text-xs text-slate-500">A preview is advisory and never dispatches a provider request.</p>
          </section>
        ))}
      </section>
    </main>
  );
}
