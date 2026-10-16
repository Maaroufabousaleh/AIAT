"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

type StewardRow = {
  worker_id: string;
  status?: string;
  candidate_count?: number;
  pending_rollouts?: number;
};

type Provenance = {
  canonical_source_repository?: string;
  exact_release?: string | null;
  commit_sha?: string | null;
  package_version?: string | null;
  oci_image_digest?: string | null;
  dependency_lock_hash?: string | null;
  protocol_api_version?: string | null;
  adapter_version?: string | null;
  transport_type?: string;
  security_scan_status?: string;
};

type Candidate = {
  candidate_id: string;
  worker_id: string;
  intake_status: string;
  bundle?: {
    bundle_id?: string;
    semantic_version?: string;
    upstream_compatibility_range?: string;
    status?: string;
    migration_notes?: string[];
  };
  diff?: Record<string, unknown>;
  adapter?: {
    adapter_id?: string;
    version?: string;
    content_hash?: string;
    transport_type?: string;
    entrypoint?: string | null;
    implementation_ref?: string | null;
    status?: string;
  };
  source_provenance?: Provenance;
  evidence?: Record<string, unknown>;
  certification_id?: string | null;
  approval_record_id?: string | null;
};

type Certification = {
  passed?: boolean;
  checks?: Record<string, boolean>;
  failures?: string[];
  certification_id?: string;
};

type Rollout = {
  rollout_id: string;
  candidate_id: string;
  status: string;
  eligible_task_classes?: string[];
  sample_count?: number;
  sample_targets?: {
    shadow?: number;
    readonly_canary?: number;
    live_canary?: number;
  };
  shadow_sample_target?: number;
  readonly_canary_sample_target?: number;
  live_canary_sample_target?: number;
  comparison_metrics?: Record<string, number>;
  rollback_thresholds?: Record<string, number>;
  rollback_reason?: string | null;
};

type StewardDetail = {
  steward_id?: string;
  worker_id?: string;
  status?: string;
  active_bundle_id?: string | null;
  active_adapter_id?: string | null;
  pending_rollouts?: number;
  candidate_count?: number;
  provenance?: Provenance;
};

type IntakeTransition = {
  from?: string;
  to?: string;
  actor?: string;
  recorded_at?: string;
  evidence?: {
    operator_note?: string;
    reason?: string;
  };
};

const STAGE_NEXT: Record<string, string | null> = {
  DISCOVERED: "SOURCE_REVIEW",
  SOURCE_REVIEW: "SECURITY_REVIEW",
  LICENSE_REVIEW: "SECURITY_REVIEW",
  SECURITY_REVIEW: "INTERFACE_RESEARCH",
  INTERFACE_RESEARCH: "GENERATED",
  GENERATED: "CERTIFYING",
  CERTIFYING: null,
  APPROVED: null,
  REJECTED: null,
  BLOCKED: null,
};

const ROLLOUT_NEXT: Record<string, string | null> = {
  PENDING: "SHADOW",
  SHADOW: "CANARY",
  CANARY: "PROMOTING",
  PROMOTING: "ACTIVE",
  ACTIVE: null,
  ROLLING_BACK: null,
  ROLLED_BACK: null,
};

function safeRepositoryLabel(value: string | undefined): string {
  if (!value) return "Not recorded";
  try {
    const url = new URL(value);
    return `${url.protocol}//${url.host}${url.pathname}`;
  } catch {
    return value.split(/[?#]/, 1)[0].slice(0, 240);
  }
}

function safeEvidenceText(value: string | undefined): string | undefined {
  if (!value) return undefined;
  return value
    .replace(/\b(bearer)\s+[^\s,;]+/gi, "$1 [redacted]")
    .replace(/\b(api[_-]?key|password|secret|token)(["']?\s*[:=]\s*["']?)[^"'\s,;]+/gi, "$1$2[redacted]")
    .slice(0, 500);
}

function messageFromPayload(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const body = payload as Record<string, unknown>;
  const raw = body.error ?? body.detail;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (parsed && typeof parsed === "object") {
        const nested = parsed as Record<string, unknown>;
        const detail = nested.detail ?? nested.message;
        if (typeof detail === "string") return detail;
        if (detail && typeof detail === "object") {
          const governed = detail as Record<string, unknown>;
          const message = typeof governed.message === "string" ? governed.message : fallback;
          const blockers = Array.isArray(governed.blockers)
            ? governed.blockers.filter((item): item is string => typeof item === "string")
            : [];
          return blockers.length > 0 ? `${message}: ${blockers.join(", ")}` : message;
        }
      }
    } catch {
      // Plain bounded error text is already suitable for the operator view.
    }
    return raw.slice(0, 800);
  }
  return fallback;
}

function Field({
  label,
  value,
  onChange,
  required = false,
  multiline = false,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  multiline?: boolean;
  placeholder?: string;
}) {
  const className = "mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400";
  return (
    <label className="block text-xs text-slate-300">
      {label}{required ? " *" : ""}
      {multiline ? (
        <textarea
          aria-label={label}
          required={required}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          rows={3}
          className={className}
        />
      ) : (
        <input
          aria-label={label}
          required={required}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className={className}
        />
      )}
    </label>
  );
}

function Scalar({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="break-all font-mono text-xs text-slate-200">{value || "Not recorded"}</dd>
    </div>
  );
}

export function WorkerStewardLifecycle({
  stewards,
  accessDenied,
}: {
  stewards: StewardRow[];
  accessDenied: boolean;
}) {
  const firstWorkerId = stewards[0]?.worker_id ?? "";
  const [workerId, setWorkerId] = useState(firstWorkerId);
  const [detail, setDetail] = useState<StewardDetail | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [rollout, setRollout] = useState<Rollout | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [workerAccessDenied, setWorkerAccessDenied] = useState(false);
  const hasDetail = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [semanticVersion, setSemanticVersion] = useState("");
  const [adapterVersion, setAdapterVersion] = useState("");
  const [compatibilityRange, setCompatibilityRange] = useState("");
  const [entrypoint, setEntrypoint] = useState("");
  const [implementationRef, setImplementationRef] = useState("");
  const [diffSummary, setDiffSummary] = useState("");
  const [migrationNotes, setMigrationNotes] = useState("");
  const [stageEvidence, setStageEvidence] = useState("");
  const [approvalReasons, setApprovalReasons] = useState<Record<string, string>>({});
  const [blockedReasons, setBlockedReasons] = useState<Record<string, string>>({});
  const [eligibleTaskClasses, setEligibleTaskClasses] = useState<string[]>(["read_only"]);
  const [sampleCount, setSampleCount] = useState("10");
  const [regressionFraction, setRegressionFraction] = useState("0");
  const [sideEffects, setSideEffects] = useState("0");
  const [rollbackReason, setRollbackReason] = useState("");

  useEffect(() => {
    if (workerId || !firstWorkerId) return;
    setWorkerId(firstWorkerId);
  }, [firstWorkerId, workerId]);

  const loadDetail = useCallback(async (selectedWorkerId: string) => {
    if (!selectedWorkerId || accessDenied) return;
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/governance/stewards/${encodeURIComponent(selectedWorkerId)}`, { cache: "no-store" });
      if (response.status === 401 || response.status === 403) {
        setWorkerAccessDenied(true);
        setError("Workers-section authorization is required to inspect or change this steward.");
        setStale(hasDetail.current);
        return;
      }
      const payload = await response.json();
      if (!response.ok) throw new Error(messageFromPayload(payload, "Steward details are unavailable."));
      setDetail(payload.steward as StewardDetail);
      setCandidates(Array.isArray(payload.candidates) ? payload.candidates as Candidate[] : []);
      hasDetail.current = true;
      setWorkerAccessDenied(false);
      setStale(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Steward details are unavailable.");
      setStale(hasDetail.current);
    } finally {
      setLoading(false);
    }
  }, [accessDenied]);

  useEffect(() => {
    setDetail(null);
    setCandidates([]);
    setRollout(null);
    hasDetail.current = false;
    setWorkerAccessDenied(false);
    setError("");
    if (workerId) void loadDetail(workerId);
  }, [workerId, loadDetail]);

  const operatorAccessDenied = accessDenied || workerAccessDenied;
  const provenance = detail?.provenance;
  const approvedCandidates = useMemo(
    () => candidates.filter((candidate) => candidate.intake_status === "APPROVED"),
    [candidates],
  );

  async function requestMutation(path: string, body: Record<string, unknown>, fallback: string) {
    if (operatorAccessDenied) return null;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401 || response.status === 403) {
        setWorkerAccessDenied(true);
        setError("Workers-section authorization was denied. Steward controls are now hidden.");
        return null;
      }
      if (!response.ok) {
        setError(messageFromPayload(payload, fallback));
        return null;
      }
      setWorkerAccessDenied(false);
      return payload as Record<string, unknown>;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : fallback);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function refreshDetail() {
    await loadDetail(workerId);
  }

  async function createCandidate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workerId) return;
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/candidates`,
      {
        semantic_version: semanticVersion.trim(),
        adapter_version: adapterVersion.trim(),
        upstream_compatibility_range: compatibilityRange.trim(),
        adapter_entrypoint: entrypoint.trim() || null,
        implementation_ref: implementationRef.trim() || null,
        diff: diffSummary.trim() ? { summary: diffSummary.trim() } : {},
        migration_notes: migrationNotes.split("\n").map((line) => line.trim()).filter(Boolean),
      },
      "Candidate creation was blocked.",
    );
    if (!result) return;
    setSemanticVersion("");
    setAdapterVersion("");
    setCompatibilityRange("");
    setEntrypoint("");
    setImplementationRef("");
    setDiffSummary("");
    setMigrationNotes("");
    setNotice("Candidate created from the steward’s current immutable provenance. Review its exact identity before advancing it.");
    await refreshDetail();
  }

  async function advanceCandidate(candidate: Candidate, target: string) {
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/candidates/${encodeURIComponent(candidate.candidate_id)}/stage`,
      { target_status: target, actor: "dashboard-operator", evidence: stageEvidence.trim() ? { operator_note: stageEvidence.trim() } : {} },
      `Candidate transition to ${target} was blocked.`,
    );
    if (result) {
      setStageEvidence("");
      setNotice(`Candidate intake advanced to ${target}.`);
      await refreshDetail();
    }
  }

  async function certifyCandidate(candidate: Candidate) {
    if (!window.confirm("Run server-owned adapter conformance for this exact immutable candidate? The control plane will enforce its certification gates.")) return;
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/candidates/${encodeURIComponent(candidate.candidate_id)}/certify`,
      {},
      "Server-owned candidate certification was blocked.",
    );
    if (result) {
      const certification = result as Certification;
      setNotice(certification.passed ? "Server-owned certification passed. Independent human approval is still required." : "Certification did not pass; inspect the recorded failure reasons.");
      await refreshDetail();
    }
  }

  async function approveCandidate(candidate: Candidate) {
    const reason = (approvalReasons[candidate.candidate_id] ?? "").trim();
    if (!reason) {
      setError("Record an approval reason before approving a candidate.");
      return;
    }
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/candidates/${encodeURIComponent(candidate.candidate_id)}/approve`,
      { decided_by: "dashboard-operator", reason, evidence: {} },
      "Candidate approval was blocked.",
    );
    if (result) {
      setApprovalReasons((current) => ({ ...current, [candidate.candidate_id]: "" }));
      setNotice("Candidate approval was recorded separately from certification. It has not been activated by this action.");
      await refreshDetail();
    }
  }

  async function blockCandidate(candidate: Candidate) {
    const reason = (blockedReasons[candidate.candidate_id] ?? "").trim();
    if (!reason) {
      setError("Record a reason before blocking a candidate.");
      return;
    }
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/candidates/${encodeURIComponent(candidate.candidate_id)}/stage`,
      { target_status: "BLOCKED", actor: "dashboard-operator", evidence: { reason } },
      "Candidate block was rejected.",
    );
    if (result) {
      setBlockedReasons((current) => ({ ...current, [candidate.candidate_id]: "" }));
      setNotice("Candidate was blocked with the supplied reason.");
      await refreshDetail();
    }
  }

  async function startRollout() {
    if (approvedCandidates.length !== 1) {
      setError("The existing rollout API cannot select among multiple approved candidates. Keep exactly one approved candidate before starting a rollout.");
      return;
    }
    if (eligibleTaskClasses.length === 0) {
      setError("Select at least one eligible low-risk task class before starting a rollout.");
      return;
    }
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/rollouts`,
      { actor: "dashboard-operator", eligible_task_classes: eligibleTaskClasses },
      "Rollout start was blocked.",
    );
    if (result) {
      setRollout(result as unknown as Rollout);
      setNotice("A staged rollout record was created for the server-selected approved candidate. Review its candidate ID before advancing.");
    }
  }

  async function advanceRollout(target: string) {
    if (!rollout) return;
    const currentSamples = Number.parseInt(sampleCount, 10);
    const samples = Number.isFinite(currentSamples) && currentSamples >= 0 ? currentSamples : 0;
    const metrics = {
      regression_fraction: Number.parseFloat(regressionFraction) || 0,
      irreversible_side_effects: Number.parseFloat(sideEffects) || 0,
    };
    if (target === "ACTIVE" && !window.confirm("Promote this exact rollout and activate its immutable worker pointers? The control plane will recheck all operational gates.")) return;
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/rollouts/${encodeURIComponent(rollout.rollout_id)}/advance`,
      { target_status: target, sample_count: samples, comparison_metrics: metrics },
      `Rollout transition to ${target} was blocked by the control plane.`,
    );
    if (result) {
      setRollout(result as unknown as Rollout);
      setNotice(`Rollout transition to ${target} was recorded.`);
      await refreshDetail();
    }
  }

  async function rollbackRollout() {
    if (!rollout) return;
    const reason = rollbackReason.trim();
    if (!reason) {
      setError("Record a rollback reason before rolling back this rollout.");
      return;
    }
    if (!window.confirm("Roll back this exact rollout through the control plane?")) return;
    const result = await requestMutation(
      `/api/governance/stewards/${encodeURIComponent(workerId)}/rollouts/${encodeURIComponent(rollout.rollout_id)}/rollback`,
      { reason },
      "Rollout rollback was blocked.",
    );
    if (result) {
      setRollout(result as unknown as Rollout);
      setRollbackReason("");
      setNotice("Rollback was recorded by the control plane.");
      await refreshDetail();
    }
  }

  const workerLabel = (steward: StewardRow) => steward.worker_id;
  const rolloutNext = rollout ? ROLLOUT_NEXT[rollout.status] : null;
  const rolloutTargets = rollout?.sample_targets ?? {
    shadow: rollout?.shadow_sample_target,
    readonly_canary: rollout?.readonly_canary_sample_target,
    live_canary: rollout?.live_canary_sample_target,
  };

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-5" aria-labelledby="worker-steward-lifecycle-heading" aria-busy={loading || busy}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="worker-steward-lifecycle-heading" className="text-sm font-semibold text-white">Worker steward candidates and rollout</h2>
          <p className="mt-1 max-w-3xl text-xs text-slate-400">Review the immutable source/runtime candidate, record intake evidence, run server-owned certification, approve separately, then use the governed staged rollout transitions.</p>
        </div>
        {!operatorAccessDenied && workerId && (
          <button type="button" onClick={() => void refreshDetail()} disabled={loading || busy} className="min-h-11 rounded border border-slate-700 px-3 py-2 text-xs text-slate-200 disabled:opacity-50">Refresh steward</button>
        )}
      </div>

      {operatorAccessDenied && (
        <div className="mt-4 rounded border border-amber-700/60 bg-amber-950/20 p-3 text-sm text-amber-200" role="region" aria-label="Worker steward access denied">
          <h3 className="font-medium">Worker section access required</h3>
          <p className="mt-1 text-xs">Governance data remains separate from worker mutation authority. Candidate and rollout controls are hidden because the workers section did not authorize this request.</p>
        </div>
      )}

      {error && !operatorAccessDenied && (
        <ErrorBanner tone={stale ? "warning" : "error"} title={stale ? "Showing last known steward state" : "Steward action unavailable"}>
          {error}
        </ErrorBanner>
      )}
      {notice && <p className="mt-3 rounded border border-cyan-800/60 bg-cyan-950/20 p-3 text-xs text-cyan-200" role="status" aria-live="polite">{notice}</p>}

      {stewards.length === 0 ? (
        <p className="mt-4 text-sm text-slate-400">No dedicated worker stewards are registered. Candidate generation is unavailable until a worker has a canonical steward.</p>
      ) : (
        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(14rem,0.8fr)_minmax(0,2fr)]">
          <div>
            <label className="block text-xs text-slate-400">Select steward
              <select aria-label="Select worker steward" value={workerId} onChange={(event) => setWorkerId(event.target.value)} disabled={loading || busy} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white disabled:opacity-60">
                {stewards.map((steward) => <option key={steward.worker_id} value={steward.worker_id}>{workerLabel(steward)} · {steward.status ?? "UNKNOWN"}</option>)}
              </select>
            </label>
            <p className="mt-2 text-xs text-slate-500">Steward read: {detail?.status ?? "loading"} · {detail?.candidate_count ?? candidates.length} candidate(s) · {detail?.pending_rollouts ?? stewards.find((item) => item.worker_id === workerId)?.pending_rollouts ?? 0} pending rollout(s)</p>
            {detail && (
              <dl className="mt-3 grid gap-2 rounded border border-slate-800 bg-slate-950/50 p-3">
                <Scalar label="Worker ID" value={detail.worker_id} />
                <Scalar label="Steward ID" value={detail.steward_id} />
                <Scalar label="Source repository" value={safeRepositoryLabel(provenance?.canonical_source_repository)} />
                <Scalar label="Exact release" value={provenance?.exact_release} />
                <Scalar label="Source commit" value={provenance?.commit_sha} />
                <Scalar label="Package version" value={provenance?.package_version} />
                <Scalar label="OCI image digest" value={provenance?.oci_image_digest} />
                <Scalar label="Transport / adapter" value={[provenance?.transport_type, provenance?.adapter_version].filter(Boolean).join(" · ") || undefined} />
                <Scalar label="Security scan status" value={provenance?.security_scan_status} />
                <Scalar label="Active immutable bundle" value={detail.active_bundle_id} />
                <Scalar label="Active immutable adapter" value={detail.active_adapter_id} />
              </dl>
            )}
          </div>

          <div className="space-y-4">
            {!operatorAccessDenied && detail && !["SUSPENDED", "RETIRED"].includes(detail.status ?? "") && (
              <details className="rounded border border-cyan-900/60 bg-cyan-950/10 p-3" open={candidates.length === 0}>
                <summary className="cursor-pointer text-sm font-medium text-cyan-100">Create immutable candidate</summary>
                <p className="mt-2 text-xs text-slate-400">The source revision and provenance below are inherited by the control plane from this steward. They cannot be overridden here. Licence data is metadata only and is not a review gate.</p>
                <form className="mt-3 grid gap-3 md:grid-cols-2" onSubmit={(event) => void createCandidate(event)}>
                  <Field label="Candidate semantic version" value={semanticVersion} onChange={setSemanticVersion} required placeholder="e.g. 1.2.0" />
                  <Field label="Adapter version" value={adapterVersion} onChange={setAdapterVersion} required placeholder="e.g. 1.2.0" />
                  <Field label="Upstream compatibility range" value={compatibilityRange} onChange={setCompatibilityRange} required placeholder="e.g. ^1.18.0" />
                  <Field label="Adapter entrypoint" value={entrypoint} onChange={setEntrypoint} placeholder="Optional; inherited adapter defaults apply" />
                  <Field label="Implementation reference" value={implementationRef} onChange={setImplementationRef} placeholder="Optional immutable ref" />
                  <Field label="Diff summary" value={diffSummary} onChange={setDiffSummary} placeholder="Short operator-supplied change summary" />
                  <div className="md:col-span-2"><Field label="Migration notes (one per line)" value={migrationNotes} onChange={setMigrationNotes} multiline placeholder="Compatibility or migration notes" /></div>
                  <button type="submit" disabled={busy || operatorAccessDenied} className="min-h-11 rounded bg-cyan-700 px-3 py-2 text-sm font-medium text-white hover:bg-cyan-600 disabled:opacity-50 md:col-span-2">Create candidate from current steward provenance</button>
                </form>
              </details>
            )}

            <div className="space-y-3" role="region" aria-label="Steward candidate records">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Candidate records</h3>
              {loading && !detail && <p className="text-sm text-slate-500" role="status">Loading candidate records…</p>}
              {!loading && candidates.length === 0 && <p className="text-sm text-slate-500">No candidate records are available for this steward.</p>}
              {candidates.map((candidate) => {
                const certification = candidate.evidence?.certification as Certification | undefined;
                const intakeTransitions = candidate.evidence?.intake_transitions;
                const nextStage = STAGE_NEXT[candidate.intake_status] ?? null;
                const canBlock = ["DISCOVERED", "SOURCE_REVIEW", "SECURITY_REVIEW", "INTERFACE_RESEARCH", "GENERATED", "CERTIFYING"].includes(candidate.intake_status);
                return (
                  <article key={candidate.candidate_id} className="rounded border border-slate-800 bg-slate-950/60 p-3" aria-label={`Candidate ${candidate.candidate_id}`}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <h4 className="font-mono text-xs text-cyan-200">{candidate.candidate_id}</h4>
                        <p className="mt-1 text-xs text-slate-400">{candidate.bundle?.semantic_version ?? "version unknown"} · adapter {candidate.adapter?.version ?? "unknown"} · {candidate.intake_status}</p>
                      </div>
                      <span className="rounded-full border border-slate-700 px-2 py-1 text-[10px] text-slate-300">Bundle {candidate.bundle?.status ?? "unknown"} · adapter {candidate.adapter?.status ?? "unknown"}</span>
                    </div>
                    <dl className="mt-3 grid gap-2 sm:grid-cols-2">
                      <Scalar label="Bundle ID" value={candidate.bundle?.bundle_id} />
                      <Scalar label="Adapter ID" value={candidate.adapter?.adapter_id} />
                      <Scalar label="Adapter content SHA-256" value={candidate.adapter?.content_hash} />
                      <Scalar label="Implementation ref" value={candidate.adapter?.implementation_ref ? safeRepositoryLabel(candidate.adapter.implementation_ref) : undefined} />
                      <Scalar label="Source commit" value={candidate.source_provenance?.commit_sha} />
                      <Scalar label="Exact release" value={candidate.source_provenance?.exact_release} />
                      <Scalar label="Source repository" value={safeRepositoryLabel(candidate.source_provenance?.canonical_source_repository)} />
                      <Scalar label="Compatibility range" value={candidate.bundle?.upstream_compatibility_range} />
                      <Scalar label="Approval record" value={candidate.approval_record_id} />
                    </dl>
                    {typeof candidate.diff?.summary === "string" && <p className="mt-3 rounded border border-slate-800 bg-slate-900/60 p-2 text-xs text-slate-300"><span className="font-medium text-slate-200">Candidate diff:</span> {candidate.diff.summary.slice(0, 1000)}</p>}
                    {(candidate.bundle?.migration_notes ?? []).filter((note): note is string => typeof note === "string").length > 0 && (
                      <div className="mt-2 text-xs text-slate-400">
                        <h5 className="font-medium text-slate-300">Migration notes</h5>
                        <ul className="mt-1 list-disc space-y-1 pl-5">{(candidate.bundle?.migration_notes ?? []).filter((note): note is string => typeof note === "string").map((note, index) => <li key={`${index}-${note.slice(0, 24)}`}>{note.slice(0, 500)}</li>)}</ul>
                      </div>
                    )}

                    {certification && (
                      <div className={`mt-3 rounded border p-2 text-xs ${certification.passed ? "border-emerald-800/60 bg-emerald-950/20 text-emerald-200" : "border-rose-800/60 bg-rose-950/20 text-rose-200"}`}>
                        <h5 className="font-semibold">Server-owned certification: {certification.passed ? "PASSED" : "FAILED"}</h5>
                        <p className="mt-1 font-mono">{certification.certification_id ?? candidate.certification_id ?? "Certification ID unavailable"}</p>
                        {certification.failures?.length ? <p className="mt-1">Blockers: {certification.failures.join(", ")}</p> : null}
                        {certification.checks && <ul className="mt-1 grid gap-1 sm:grid-cols-2">{Object.entries(certification.checks).map(([name, passed]) => <li key={name}>{passed ? "PASS" : "BLOCKED"} · {name}</li>)}</ul>}
                      </div>
                    )}
                    {Boolean(intakeTransitions) && (
                      <details className="mt-3 rounded border border-slate-800 p-2">
                        <summary className="cursor-pointer text-xs text-slate-300">Intake evidence</summary>
                        <ul className="mt-2 grid gap-1 text-[10px] text-slate-400">
                          {(Array.isArray(intakeTransitions) ? intakeTransitions : [])
                            .filter((entry): entry is IntakeTransition => Boolean(entry) && typeof entry === "object")
                            .map((entry, index) => (
                              <li key={`${entry.recorded_at ?? "transition"}-${index}`}>
                                {entry.from ?? "unknown"} → {entry.to ?? "unknown"}
                                {entry.actor ? ` · ${entry.actor}` : ""}
                                {entry.recorded_at ? ` · ${entry.recorded_at}` : ""}
                                {safeEvidenceText(entry.evidence?.operator_note ?? entry.evidence?.reason) ? <span className="block pl-2">Evidence: {safeEvidenceText(entry.evidence?.operator_note ?? entry.evidence?.reason)}</span> : null}
                              </li>
                            ))}
                        </ul>
                      </details>
                    )}

                    {!operatorAccessDenied && nextStage && (
                      <div className="mt-3 flex flex-wrap items-end gap-2">
                        <label className="min-w-48 flex-1 text-[10px] text-slate-400">Stage evidence note (optional)
                          <input value={stageEvidence} onChange={(event) => setStageEvidence(event.target.value)} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white" />
                        </label>
                        <button type="button" disabled={busy} onClick={() => void advanceCandidate(candidate, nextStage)} className="min-h-11 rounded border border-cyan-800 px-3 py-2 text-xs text-cyan-200 disabled:opacity-50">Advance to {nextStage}</button>
                      </div>
                    )}
                    {!operatorAccessDenied && candidate.intake_status === "CERTIFYING" && (
                      <div className="mt-3 flex flex-wrap items-end gap-2">
                        {certification?.passed ? (
                          <>
                            <label className="min-w-48 flex-1 text-[10px] text-slate-400">Independent approval reason *
                              <input value={approvalReasons[candidate.candidate_id] ?? ""} onChange={(event) => setApprovalReasons((current) => ({ ...current, [candidate.candidate_id]: event.target.value }))} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white" />
                            </label>
                            <button type="button" disabled={busy || !approvalReasons[candidate.candidate_id]?.trim()} onClick={() => void approveCandidate(candidate)} className="min-h-11 rounded border border-emerald-800 px-3 py-2 text-xs text-emerald-200 disabled:opacity-50">Approve candidate</button>
                          </>
                        ) : (
                          <button type="button" disabled={busy} onClick={() => void certifyCandidate(candidate)} className="min-h-11 rounded border border-amber-800 px-3 py-2 text-xs text-amber-200 disabled:opacity-50">Run server-owned certification</button>
                        )}
                      </div>
                    )}
                    {!operatorAccessDenied && canBlock && (
                      <div className="mt-3 flex flex-wrap items-end gap-2">
                        <label className="min-w-48 flex-1 text-[10px] text-slate-400">Block reason *
                          <input value={blockedReasons[candidate.candidate_id] ?? ""} onChange={(event) => setBlockedReasons((current) => ({ ...current, [candidate.candidate_id]: event.target.value }))} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white" />
                        </label>
                        <button type="button" disabled={busy || !blockedReasons[candidate.candidate_id]?.trim()} onClick={() => void blockCandidate(candidate)} className="min-h-11 rounded border border-rose-800 px-3 py-2 text-xs text-rose-200 disabled:opacity-50">Block with reason</button>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>

            <section className="rounded border border-slate-800 bg-slate-950/50 p-3" aria-labelledby="staged-rollout-heading">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 id="staged-rollout-heading" className="text-sm font-medium text-white">Staged rollout</h3>
                  <p className="mt-1 max-w-2xl text-xs text-slate-400">The control plane selects an eligible approved candidate. This screen never fabricates a rollout or infers completed rollout history.</p>
                </div>
                {!operatorAccessDenied && approvedCandidates.length === 1 && !rollout && (detail?.pending_rollouts ?? 0) === 0 && (
                  <button type="button" disabled={busy} onClick={() => void startRollout()} className="min-h-11 rounded bg-cyan-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">Start rollout for the sole approved candidate</button>
                )}
              </div>
              <p className="mt-2 text-xs text-amber-200/80" role="note">The existing API has no rollout-history read endpoint. This view displays the exact rollout returned during this browser session plus the canonical pending count; past completed records are not inferred.</p>
              {!operatorAccessDenied && approvedCandidates.length > 1 && <p className="mt-2 text-xs text-amber-200" role="status">Rollout start is hidden because multiple candidates are approved and the existing API does not accept an explicit candidate ID.</p>}
              {!operatorAccessDenied && approvedCandidates.length === 0 && <p className="mt-2 text-xs text-slate-500">Approve one certified candidate before starting a rollout.</p>}
              {rollout && (
                <div className="mt-3 rounded border border-cyan-900/60 bg-cyan-950/10 p-3" aria-label={`Rollout ${rollout.rollout_id}`}>
                  <dl className="grid gap-2 sm:grid-cols-2">
                    <Scalar label="Rollout ID" value={rollout.rollout_id} />
                    <Scalar label="Candidate ID" value={rollout.candidate_id} />
                    <Scalar label="Status" value={rollout.status} />
                    <Scalar label="Sample count" value={String(rollout.sample_count ?? 0)} />
                    <Scalar label="Eligible task classes" value={rollout.eligible_task_classes?.join(", ")} />
                    <Scalar label="Rollback threshold" value={rollout.rollback_thresholds?.regression_fraction == null ? undefined : String(rollout.rollback_thresholds.regression_fraction)} />
                  </dl>
                  {rolloutTargets && <p className="mt-2 text-[10px] text-slate-400">Targets · shadow {rolloutTargets.shadow ?? "n/a"} · read-only canary {rolloutTargets.readonly_canary ?? "n/a"} · live canary {rolloutTargets.live_canary ?? "n/a"}</p>}
                  {!operatorAccessDenied && rolloutNext && (
                    <div className="mt-3 grid gap-2 md:grid-cols-2">
                      <Field label="Observed sample count" value={sampleCount} onChange={setSampleCount} required />
                      <Field label="Regression fraction" value={regressionFraction} onChange={setRegressionFraction} />
                      <Field label="Irreversible side effects" value={sideEffects} onChange={setSideEffects} />
                      <button type="button" disabled={busy} onClick={() => void advanceRollout(rolloutNext)} className="min-h-11 self-end rounded border border-cyan-800 px-3 py-2 text-xs text-cyan-200 disabled:opacity-50">Advance to {rolloutNext}</button>
                    </div>
                  )}
                  {!operatorAccessDenied && !["ROLLED_BACK", "ROLLING_BACK"].includes(rollout.status) && (
                    <div className="mt-3 flex flex-wrap items-end gap-2">
                      <label className="min-w-48 flex-1 text-[10px] text-slate-400">Rollback reason *
                        <input value={rollbackReason} onChange={(event) => setRollbackReason(event.target.value)} className="mt-1 min-h-11 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white" />
                      </label>
                      <button type="button" disabled={busy || !rollbackReason.trim()} onClick={() => void rollbackRollout()} className="min-h-11 rounded border border-rose-800 px-3 py-2 text-xs text-rose-200 disabled:opacity-50">Roll back rollout</button>
                    </div>
                  )}
                </div>
              )}
              {!operatorAccessDenied && !rollout && approvedCandidates.length === 1 && (detail?.pending_rollouts ?? 0) > 0 && <p className="mt-2 text-xs text-amber-200" role="status">A rollout is pending, but the existing API exposes only its count. Full history and action controls are unavailable until a canonical rollout read endpoint exists.</p>}
            </section>
          </div>
        </div>
      )}
    </section>
  );
}
