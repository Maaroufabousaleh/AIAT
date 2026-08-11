"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, CheckCircle2, RefreshCw, ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

interface EvidenceCategory {
  category: string;
  status: string;
  required: boolean;
  item_count: number;
  evidence_refs: string[];
  reason?: string | null;
}

interface EvidenceItem {
  id: string;
  category: string;
  kind: string;
  status?: string | null;
  source?: string | null;
  checksum?: string | null;
  occurred_at?: string | null;
}

interface EvidenceCheck {
  name: string;
  required: boolean;
  passed: boolean;
  reason?: string | null;
}

interface EvidenceNotice {
  artifact_id: string;
  field: string;
  value: string;
}

interface EvidencePackage {
  schema_version: string;
  project_id: string;
  policy_id: string;
  policy_version: string;
  status: string;
  completeness_score: number;
  checks: EvidenceCheck[];
  categories: EvidenceCategory[];
  items: EvidenceItem[];
  notices: EvidenceNotice[];
  snapshot?: { id?: string; generated_at?: string; status?: string } | null;
}

function statusClass(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "complete" || normalized === "present" || normalized === "passed") {
    return "text-emerald-300";
  }
  if (normalized === "incomplete" || normalized === "missing") {
    return "text-amber-300";
  }
  return "text-slate-300";
}

export default function ProjectEvidencePage() {
  const { id } = useParams<{ id: string }>();
  const [packageView, setPackageView] = useState<EvidencePackage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [packageStale, setPackageStale] = useState(false);
  const [packageRefreshError, setPackageRefreshError] = useState<string | null>(null);
  const hasPackageRef = useRef(false);

  const loadPackage = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    setPackageRefreshError(null);
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(id)}/evidence/package`, {
        cache: "no-store",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof payload.error === "string" ? payload.error : "Evidence package unavailable");
      }
      setPackageView(payload as EvidencePackage);
      hasPackageRef.current = true;
      setPackageStale(false);
      setPackageRefreshError(null);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Evidence package unavailable";
      if (hasPackageRef.current) {
        setPackageStale(true);
        setPackageRefreshError(message);
      } else {
        setPackageView(null);
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void loadPackage();
  }, [loadPackage]);

  return (
    <div className="min-h-full p-6 lg:p-8">
      <PageHeader
        title="Project evidence"
        description="A bounded, secret-safe view over the project’s canonical evidence authorities."
        actions={(
          <div className="flex gap-2">
            <Link href={`/projects/${encodeURIComponent(id || "")}`} className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800">
              <ArrowLeft size={14} /> Project
            </Link>
            <button type="button" onClick={() => void loadPackage()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-cyan-400/30 px-3 py-2 text-sm text-cyan-200 hover:bg-cyan-400/10 disabled:opacity-50">
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
            </button>
          </div>
        )}
      />

      {error && <div className="mt-6"><ErrorBanner tone="warning">{error}</ErrorBanner></div>}
      {packageStale && packageRefreshError && (
        <div className="mt-6" data-testid="project-evidence-stale">
          <ErrorBanner
            tone="warning"
            title="Showing last known evidence package"
            action={(
              <button
                type="button"
                onClick={() => void loadPackage()}
                disabled={loading}
                aria-busy={loading}
                className="inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs font-medium text-slate-100 transition-colors hover:bg-slate-800 disabled:cursor-wait disabled:opacity-60"
              >
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} aria-hidden="true" />
                Retry
              </button>
            )}
          >
            {packageRefreshError} The last successful package remains visible
            until a retry succeeds.
          </ErrorBanner>
        </div>
      )}
      {loading && !packageView && <p className="mt-8 text-sm text-slate-400">Loading evidence package…</p>}

      {packageView && (
        <>
          <section className="mt-6 grid gap-4 md:grid-cols-3">
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-5">
              <p className="text-xs uppercase tracking-wide text-slate-500">Completion</p>
              <p className={`mt-2 text-2xl font-semibold ${statusClass(packageView.status)}`}>{packageView.status}</p>
              <p className="mt-1 text-sm text-slate-400">{Math.round(packageView.completeness_score * 100)}% under {packageView.policy_id} v{packageView.policy_version}</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-5">
              <p className="text-xs uppercase tracking-wide text-slate-500">Coverage</p>
              <p className="mt-2 text-2xl font-semibold text-slate-100">{packageView.categories.length}</p>
              <p className="mt-1 text-sm text-slate-400">evidence categories, {packageView.items.length} bounded items</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-5">
              <p className="text-xs uppercase tracking-wide text-slate-500">Snapshot</p>
              <p className="mt-2 text-2xl font-semibold text-slate-100">{packageView.snapshot ? "Stored" : "Fresh"}</p>
              <p className="mt-1 break-all text-sm text-slate-400">{packageView.snapshot?.id || "No durable snapshot yet"}</p>
            </div>
          </section>

          <section className="mt-6 rounded-xl border border-slate-800 bg-slate-900/70 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-100"><CheckCircle2 size={16} className="text-emerald-300" /> Required checks</div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {packageView.checks.map((check) => (
                <div key={check.name} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="font-medium text-slate-200">{check.name}</span>
                    <span className={check.passed ? "text-emerald-300" : "text-amber-300"}>{check.passed ? "pass" : "open"}</span>
                  </div>
                  {check.reason && <p className="mt-1 text-xs text-slate-500">{check.reason}</p>}
                </div>
              ))}
            </div>
          </section>

          <section className="mt-6 rounded-xl border border-slate-800 bg-slate-900/70 p-5">
            <h2 className="text-sm font-semibold text-slate-100">Category coverage</h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {packageView.categories.map((category) => (
                <div key={category.category} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-slate-200">{category.category}</span>
                    <span className={`text-xs ${statusClass(category.status)}`}>{category.status}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{category.item_count} item{category.item_count === 1 ? "" : "s"}{category.required ? " · required" : ""}</p>
                </div>
              ))}
            </div>
          </section>

          {packageView.notices.length > 0 && (
            <section className="mt-6 rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
              <div className="flex items-center gap-2 text-sm font-semibold text-amber-200"><ShieldAlert size={16} /> Metadata notices</div>
              <p className="mt-2 text-xs leading-5 text-amber-100/70">Resource licence or restriction values are displayed as metadata notices only; they never determine completion status.</p>
              <div className="mt-4 grid gap-2">
                {packageView.notices.map((notice, index) => (
                  <div key={`${notice.artifact_id}-${notice.field}-${index}`} className="rounded-lg border border-amber-400/20 px-3 py-2 text-xs text-amber-100/80">
                    {notice.artifact_id} · {notice.field}: {notice.value}
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="mt-6 rounded-xl border border-slate-800 bg-slate-900/70 p-5">
            <h2 className="text-sm font-semibold text-slate-100">Evidence items</h2>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[42rem] text-left text-xs">
                <thead className="text-slate-500"><tr><th className="pb-2 pr-4 font-medium">ID</th><th className="pb-2 pr-4 font-medium">Category</th><th className="pb-2 pr-4 font-medium">Kind</th><th className="pb-2 pr-4 font-medium">Status</th><th className="pb-2 font-medium">Source</th></tr></thead>
                <tbody className="divide-y divide-slate-800/80">
                  {packageView.items.map((item) => (
                    <tr key={`${item.category}-${item.id}`} className="text-slate-300"><td className="max-w-[14rem] truncate py-2 pr-4 font-mono">{item.id}</td><td className="py-2 pr-4">{item.category}</td><td className="py-2 pr-4">{item.kind}</td><td className="py-2 pr-4">{item.status || "observed"}</td><td className="py-2">{item.source || "canonical"}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
