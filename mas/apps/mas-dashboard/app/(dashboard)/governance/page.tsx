"use client";

import { useEffect, useState } from "react";
import { ShieldCheck, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { ExecutiveActionPanel } from "@/components/governance/ExecutiveActionPanel";

type ModelProfile = {
  profile_id: string;
  purpose: string;
  status: string;
  approved_provider_ids?: string[];
  versions?: Array<{ version: string; provider_id: string; exact_model_id: string; status: string }>;
};

type ModelCatalogue = {
  schema_version: string;
  registry_model_count: number;
  profile_count: number;
  profile_version_count: number;
  covered_profile_version_count: number;
  profile_pending_model_count: number;
  findings?: Array<{ code: string; profile_id: string; exact_model_id: string }>;
  entries?: Array<{ model_id: string; provider_id: string; profile_state: string }>;
};

type WorkerRun = {
  id: string;
  worker_id: string;
  task_type: string;
  state: string;
  model_resolution_snapshot_id?: string | null;
  created_at?: string;
};

type Steward = {
  id: string;
  worker_id: string;
  status: string;
  monitoring_cadence?: string;
  last_monitor_at?: string | null;
  candidate_count?: number;
  monitoring?: Array<{ status: string; last_checked_at?: string | null; last_error?: string | null }>;
};

export default function GovernancePage() {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [catalogue, setCatalogue] = useState<ModelCatalogue | null>(null);
  const [runs, setRuns] = useState<WorkerRun[]>([]);
  const [stewards, setStewards] = useState<Steward[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);

  async function refresh() {
    const hadData = profiles.length > 0 || catalogue !== null || runs.length > 0 || stewards.length > 0;
    setLoading(true);
    setError("");
    try {
      const [profileResponse, catalogueResponse, runResponse, stewardResponse] = await Promise.all([
        fetch("/api/governance/model-profiles", { cache: "no-store" }),
        fetch("/api/governance/model-profiles/catalogue", { cache: "no-store" }),
        fetch("/api/governance/runs?limit=50", { cache: "no-store" }),
        fetch("/api/governance/stewards", { cache: "no-store" }),
      ]);
      if (!profileResponse.ok || !catalogueResponse.ok || !runResponse.ok || !stewardResponse.ok) {
        throw new Error("Governance data is unavailable from the control plane");
      }
      const [profileData, catalogueData, runData, stewardData] = await Promise.all([profileResponse.json(), catalogueResponse.json(), runResponse.json(), stewardResponse.json()]);
      setProfiles(Array.isArray(profileData) ? profileData : []);
      setCatalogue(catalogueData && typeof catalogueData === "object" ? catalogueData as ModelCatalogue : null);
      setRuns(Array.isArray(runData) ? runData : []);
      setStewards(Array.isArray(stewardData) ? stewardData : []);
      setStale(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setStale(hadData);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  return (
    <main className="dashboard-page min-h-full p-6 lg:p-8" aria-label="Governance">
      <PageHeader
        icon="shield-check"
        title="Governance"
        description="Immutable model policy and worker-run evidence owned by the AIAT control plane."
        actions={(
          <button type="button" onClick={() => void refresh()} disabled={loading} aria-label="Refresh governance" title="Refresh governance" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        )}
      />
      {error && (
        <ErrorBanner
          tone={stale ? "warning" : "error"}
          title={stale ? "Showing last known governance state" : "Governance data unavailable"}
          action={(
            <button type="button" onClick={() => void refresh()} disabled={loading} className="min-h-11 px-3 rounded border border-current text-xs font-medium hover:bg-white/10 disabled:opacity-50">
              Retry
            </button>
          )}
        >
          {stale ? `${error}. The latest refresh failed; retained governance data remains visible.` : error}
        </ErrorBanner>
      )}
      <div className="mt-6 grid gap-6 xl:grid-cols-2" role="region" aria-label="Governance read surfaces" aria-busy={loading}>
        <ExecutiveActionPanel />
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="model-profiles-heading">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white"><ShieldCheck size={16} className="text-emerald-400" /><h2 id="model-profiles-heading">Model Profiles</h2></div>
          {profiles.length === 0 ? <p className="text-sm text-slate-500">No persisted profiles are available.</p> : (
            <div className="space-y-3" role="list" aria-label="Governed model profiles">
              {profiles.map((profile) => (
                <div key={profile.profile_id} role="listitem" className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                  <div className="flex items-center justify-between gap-3"><span className="font-mono text-sm text-white">{profile.profile_id}</span><span className="text-xs uppercase text-emerald-300">{profile.status}</span></div>
                  <p className="mt-1 text-xs text-slate-400">{profile.purpose}</p>
                  <div className="mt-2 space-y-1 text-xs text-slate-500">
                    {(profile.versions ?? []).map((version) => <div key={version.version} className="font-mono text-slate-300">{version.version} · {version.provider_id}/{version.exact_model_id} · {version.status}</div>)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="worker-runs-heading">
          <div className="mb-4 flex items-center justify-between"><h2 id="worker-runs-heading" className="text-sm font-semibold text-white">Recent WorkerRuns</h2><span className="text-xs text-slate-400">authoritative runtime state</span></div>
          {runs.length === 0 ? <p className="text-sm text-slate-500">No worker runs are available.</p> : (
            <div className="overflow-x-auto"><table className="w-full text-left text-xs"><caption className="sr-only">Recent governed worker runs</caption><thead className="text-slate-400"><tr><th scope="col" className="pb-2">Worker</th><th scope="col" className="pb-2">Task</th><th scope="col" className="pb-2">State</th><th scope="col" className="pb-2">Model snapshot</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id} className="border-t border-slate-800"><td className="py-2 font-mono text-slate-300">{run.worker_id}</td><td className="py-2 text-slate-400">{run.task_type}</td><td className="py-2 text-emerald-300">{run.state}</td><td className="py-2 font-mono text-slate-400">{run.model_resolution_snapshot_id ? "pinned" : "none"}</td></tr>)}</tbody></table></div>
          )}
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="worker-stewards-heading">
          <div className="mb-4 flex items-center justify-between"><h2 id="worker-stewards-heading" className="text-sm font-semibold text-white">External Worker Stewards</h2><span className="text-xs text-slate-400">one steward per worker</span></div>
          {stewards.length === 0 ? <p className="text-sm text-slate-500">No external worker stewards are registered.</p> : (
            <div className="space-y-2" role="list" aria-label="External worker stewards">
              {stewards.map((steward) => {
                const monitor = steward.monitoring?.[0];
                return <div key={steward.id} role="listitem" className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs">
                  <div className="flex items-center justify-between gap-3"><span className="font-mono text-slate-200">{steward.worker_id}</span><span className={steward.status === "READY" ? "text-emerald-300" : "text-amber-300"}>{steward.status}</span></div>
                  <div className="mt-1 text-slate-400">{steward.candidate_count ?? 0} candidate(s) · {steward.monitoring_cadence ?? "daily"} monitoring</div>
                  {monitor?.last_error ? <div className="mt-1 text-rose-300">Monitor: {monitor.last_error}</div> : monitor?.last_checked_at ? <div className="mt-1 text-slate-500">Last check: {new Date(monitor.last_checked_at).toLocaleString()}</div> : <div className="mt-1 text-slate-500">Awaiting first monitor run</div>}
                </div>;
              })}
            </div>
          )}
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 xl:col-span-2" aria-labelledby="runtime-catalogue-heading">
          <div className="mb-4 flex items-center justify-between"><h2 id="runtime-catalogue-heading" className="text-sm font-semibold text-white">Runtime Model Catalogue</h2><span className="font-mono text-xs text-slate-400" aria-label={`Catalogue schema ${catalogue?.schema_version ?? "unavailable"}`}>{catalogue?.schema_version ?? "unavailable"}</span></div>
          {!catalogue ? <p className="text-sm text-slate-500">The runtime catalogue is unavailable.</p> : <>
            <div className="grid gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-2xl font-semibold text-white">{catalogue.registry_model_count}</div><div className="text-xs text-slate-500">registered models</div></div>
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-2xl font-semibold text-white">{catalogue.profile_count}</div><div className="text-xs text-slate-500">governed profiles</div></div>
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-2xl font-semibold text-white">{catalogue.covered_profile_version_count}/{catalogue.profile_version_count}</div><div className="text-xs text-slate-500">profile versions reconciled</div></div>
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-2xl font-semibold text-amber-300">{catalogue.profile_pending_model_count}</div><div className="text-xs text-slate-500">models awaiting profile</div></div>
            </div>
            {(catalogue.entries ?? []).length > 0 && <div className="mt-4 grid gap-2 md:grid-cols-2 lg:grid-cols-3">{(catalogue.entries ?? []).slice(0, 12).map((entry) => <div key={`${entry.provider_id}:${entry.model_id}`} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs"><div className="font-mono text-slate-200">{entry.model_id}</div><div className="mt-1 text-slate-500">{entry.provider_id} · <span className={entry.profile_state === "approved_profile_present" ? "text-emerald-300" : "text-amber-300"}>{entry.profile_state}</span></div></div>)}</div>}
            {(catalogue.findings ?? []).length > 0 && <div className="mt-3 text-xs text-rose-300">{catalogue.findings?.length} profile binding finding(s) require review.</div>}
          </>}
        </section>
      </div>
    </main>
  );
}
