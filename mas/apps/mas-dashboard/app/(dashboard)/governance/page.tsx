"use client";

import { useEffect, useState } from "react";
import { ShieldCheck, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type ModelProfile = {
  profile_id: string;
  purpose: string;
  status: string;
  approved_provider_ids?: string[];
  versions?: Array<{ version: string; provider_id: string; exact_model_id: string; status: string }>;
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
  const [runs, setRuns] = useState<WorkerRun[]>([]);
  const [stewards, setStewards] = useState<Steward[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const [profileResponse, runResponse, stewardResponse] = await Promise.all([
        fetch("/api/governance/model-profiles"),
        fetch("/api/governance/runs?limit=50"),
        fetch("/api/governance/stewards"),
      ]);
      if (!profileResponse.ok || !runResponse.ok || !stewardResponse.ok) {
        throw new Error("Governance data is unavailable from the control plane");
      }
      const [profileData, runData, stewardData] = await Promise.all([profileResponse.json(), runResponse.json(), stewardResponse.json()]);
      setProfiles(Array.isArray(profileData) ? profileData : []);
      setRuns(Array.isArray(runData) ? runData : []);
      setStewards(Array.isArray(stewardData) ? stewardData : []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  return (
    <div className="min-h-full p-6 lg:p-8">
      <PageHeader
        title="Governance"
        description="Immutable model policy and worker-run evidence owned by the AIAT control plane."
        actions={(
          <button type="button" onClick={() => void refresh()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        )}
      />
      {error && <ErrorBanner>{error}</ErrorBanner>}
      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white"><ShieldCheck size={16} className="text-emerald-400" /> Model Profiles</div>
          {profiles.length === 0 ? <p className="text-sm text-slate-500">No persisted profiles are available.</p> : (
            <div className="space-y-3">
              {profiles.map((profile) => (
                <div key={profile.profile_id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
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
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5">
          <div className="mb-4 flex items-center justify-between"><span className="text-sm font-semibold text-white">Recent WorkerRuns</span><span className="text-xs text-slate-500">authoritative runtime state</span></div>
          {runs.length === 0 ? <p className="text-sm text-slate-500">No worker runs are available.</p> : (
            <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-slate-500"><tr><th className="pb-2">Worker</th><th className="pb-2">Task</th><th className="pb-2">State</th><th className="pb-2">Model snapshot</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id} className="border-t border-slate-800"><td className="py-2 font-mono text-slate-300">{run.worker_id}</td><td className="py-2 text-slate-400">{run.task_type}</td><td className="py-2 text-emerald-300">{run.state}</td><td className="py-2 font-mono text-slate-500">{run.model_resolution_snapshot_id ? "pinned" : "none"}</td></tr>)}</tbody></table></div>
          )}
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5">
          <div className="mb-4 flex items-center justify-between"><span className="text-sm font-semibold text-white">External Worker Stewards</span><span className="text-xs text-slate-500">one steward per worker</span></div>
          {stewards.length === 0 ? <p className="text-sm text-slate-500">No external worker stewards are registered.</p> : (
            <div className="space-y-2">
              {stewards.map((steward) => {
                const monitor = steward.monitoring?.[0];
                return <div key={steward.id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs">
                  <div className="flex items-center justify-between gap-3"><span className="font-mono text-slate-200">{steward.worker_id}</span><span className={steward.status === "READY" ? "text-emerald-300" : "text-amber-300"}>{steward.status}</span></div>
                  <div className="mt-1 text-slate-500">{steward.candidate_count ?? 0} candidate(s) · {steward.monitoring_cadence ?? "daily"} monitoring</div>
                  {monitor?.last_error ? <div className="mt-1 text-rose-300">Monitor: {monitor.last_error}</div> : monitor?.last_checked_at ? <div className="mt-1 text-slate-500">Last check: {new Date(monitor.last_checked_at).toLocaleString()}</div> : <div className="mt-1 text-slate-500">Awaiting first monitor run</div>}
                </div>;
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
