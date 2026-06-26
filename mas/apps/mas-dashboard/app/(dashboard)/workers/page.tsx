"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { RefreshCw, Users, CheckCircle, AlertTriangle, XCircle, Package, Plus, ChevronDown, ChevronRight, ChevronUp, ExternalLink, Settings, Power, ClipboardCheck } from "lucide-react";
import { clsx } from "clsx";
import { BulkActionBar, RowCheckbox, SelectAllCheckbox } from "@/components/ui/BulkActionBar";
import { useBulkSelection } from "@/lib/use-bulk-selection";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { KpiCard } from "@/components/ui/KpiCard";
import { FilterChip } from "@/components/ui/FilterChips";

// localStorage keys for filter persistence — keyed by page to avoid collisions
const STORAGE_KEYS = {
  search: "mas.workers.search",
  status: "mas.workers.statusFilter",
} as const;

interface WorkerCapability {
  name: string;
  version: string;
  description?: string;
  risk_level?: string;
}

interface Worker {
  id: string;
  worker_id: string;
  name: string;
  version?: string;
  description?: string;
  team_id?: string;
  transport_mode?: string;
  adapter_entrypoint?: string;
  source_repo?: string;
  source_revision?: string;
  evaluation_status?: string;
  status: string;
  capability_ids?: string[];
  sandbox_profile?: string;
  max_concurrent_tasks?: number;
  created_at?: string;
  updated_at?: string;
}

interface EvaluationReport {
  id: string;
  verdict: string;
  overall_score?: number;
  evaluated_at?: string;
  risk_tier?: string;
  checks?: Record<string, { passed?: boolean; score?: number; status?: string; details?: string }>;
  blocked_reasons?: string[];
  recommended_status?: string;
  requires_human_approval?: boolean;
}

const STATUS_CONFIG: Record<string, { label: string; icon: typeof CheckCircle; cls: string }> = {
  ACTIVE: { label: "Active", icon: CheckCircle, cls: "text-emerald-400 bg-emerald-400/10 border-emerald-700" },
  INACTIVE: { label: "Inactive", icon: XCircle, cls: "text-slate-400 bg-slate-400/10 border-slate-700" },
  DRAINING: { label: "Draining", icon: AlertTriangle, cls: "text-cyan-400 bg-cyan-400/10 border-cyan-700" },
  DEREGISTERED: { label: "Deregistered", icon: XCircle, cls: "text-rose-400 bg-rose-400/10 border-rose-700" },
  PENDING: { label: "Pending", icon: AlertTriangle, cls: "text-amber-400 bg-amber-400/10 border-amber-700" },
  PENDING_EVALUATION: { label: "Pending Eval", icon: AlertTriangle, cls: "text-amber-400 bg-amber-400/10 border-amber-700" },
  REJECTED: { label: "Rejected", icon: XCircle, cls: "text-rose-400 bg-rose-400/10 border-rose-700" },
  ERROR: { label: "Error", icon: AlertTriangle, cls: "text-rose-400 bg-rose-400/10 border-rose-700" },
};

const EVAL_COLORS: Record<string, string> = {
  approved: "text-emerald-400",
  conditional: "text-amber-400",
  pending: "text-amber-400",
  rejected: "text-rose-400",
  unknown: "text-slate-400",
};

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.INACTIVE;
  const Icon = cfg.icon;
  return (
    <span className={clsx("inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-medium", cfg.cls)}>
      <Icon size={11} />
      {cfg.label}
    </span>
  );
}

function WorkerRow({
  worker,
  onStatusChange,
  selected,
  onSelectChange,
}: {
  worker: Worker;
  onStatusChange: () => void;
  selected: boolean;
  onSelectChange: (checked: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [evaluations, setEvaluations] = useState<EvaluationReport[]>([]);
  const [actionError, setActionError] = useState("");
  // Track which evaluation check details are expanded (per check name).
  // Default: all collapsed to keep the row dense.
  const [expandedChecks, setExpandedChecks] = useState<Record<string, boolean>>({});

  const latestEvaluation = evaluations[0];
  const activationBlocked =
    Boolean(worker.source_repo) && (worker.evaluation_status ?? "pending") !== "approved";

  function toggleCheck(name: string) {
    setExpandedChecks((prev) => ({ ...prev, [name]: !prev[name] }));
  }

  async function loadEvaluations() {
    const res = await fetch(`/api/workers/${worker.id}/evaluations`);
    if (res.ok) {
      const data = await res.json();
      setEvaluations(Array.isArray(data) ? data : []);
    }
  }

  async function toggleExpanded() {
    const next = !expanded;
    setExpanded(next);
    if (next && worker.source_repo) {
      await loadEvaluations();
    }
  }

  async function toggleStatus() {
    const newStatus = worker.status === "ACTIVE" ? "INACTIVE" : "ACTIVE";
    const action = worker.status === "ACTIVE" ? "DEACTIVATE" : "ACTIVATE";
    setTransitioning(true);
    setActionError("");
    try {
      const res = await fetch(`/api/workers/${worker.id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, new_status: newStatus }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        setActionError(payload.error ?? payload.detail ?? "Status transition failed");
        return;
      }
      onStatusChange();
    } finally {
      setTransitioning(false);
    }
  }

  async function drainWorker() {
    setTransitioning(true);
    setActionError("");
    try {
      const res = await fetch(`/api/workers/${worker.id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "DRAIN" }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        setActionError(payload.error ?? payload.detail ?? "Drain failed");
        return;
      }
      onStatusChange();
    } finally {
      setTransitioning(false);
    }
  }

  async function evaluateWorker() {
    setEvaluating(true);
    setActionError("");
    try {
      const res = await fetch(`/api/workers/${worker.id}/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        setActionError(payload.error ?? payload.detail ?? "Evaluation failed");
        return;
      }
      await loadEvaluations();
      onStatusChange();
    } finally {
      setEvaluating(false);
    }
  }

  return (
    <>
      <tr
        className={clsx(
          "border-b border-slate-800 hover:bg-slate-800/35 cursor-pointer transition-colors",
          selected && "bg-blue-950/30 hover:bg-blue-950/40"
        )}
        onClick={() => void toggleExpanded()}
      >
        <td className="px-4 py-3 w-10" onClick={(e) => e.stopPropagation()}>
          <RowCheckbox
            checked={selected}
            onChange={onSelectChange}
            ariaLabel={`Select ${worker.worker_id}`}
            stopPropagation
          />
        </td>
        <td className="px-4 py-3 text-slate-500 w-8">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </td>
        <td className="px-4 py-3">
          <div className="font-mono text-sm text-white font-medium">{worker.worker_id}</div>
          {worker.description && (
            <div className="text-xs text-slate-500 truncate max-w-xs">{worker.description}</div>
          )}
        </td>
        <td className="px-4 py-3 text-xs text-slate-400">{worker.name}</td>
        <td className="px-4 py-3 text-xs text-slate-400">{worker.team_id ?? "—"}</td>
        <td className="px-4 py-3">
          <StatusBadge status={worker.status} />
        </td>
        <td className="px-4 py-3">
          <span className={clsx("text-xs", EVAL_COLORS[worker.evaluation_status ?? "unknown"])}>
            {worker.evaluation_status ?? "unknown"}
          </span>
        </td>
        <td className="px-4 py-3 text-xs text-slate-400 font-mono">
          {worker.version ?? "—"}
        </td>
        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-1">
          {worker.source_repo && (
            <button
              onClick={evaluateWorker}
              disabled={evaluating}
              title="Evaluate"
              aria-label={`Evaluate ${worker.worker_id}`}
              className="p-1.5 rounded text-slate-500 hover:text-blue-400 hover:bg-blue-400/10 transition-colors disabled:opacity-40"
            >
              <ClipboardCheck size={14} />
            </button>
          )}
          <button
            onClick={toggleStatus}
            disabled={transitioning}
            title={worker.status === "ACTIVE" ? "Deactivate" : "Activate"}
            aria-label={worker.status === "ACTIVE" ? `Deactivate ${worker.worker_id}` : `Activate ${worker.worker_id}`}
            className={clsx(
              "p-1.5 rounded transition-colors disabled:opacity-40",
              worker.status === "ACTIVE"
                ? "text-emerald-400 hover:text-rose-400 hover:bg-rose-400/10"
                : "text-slate-500 hover:text-emerald-400 hover:bg-emerald-400/10"
            )}
          >
            <Power size={14} />
          </button>
          <button
            onClick={drainWorker}
            disabled={transitioning || worker.status !== "ACTIVE"}
            title="Drain"
            aria-label={`Drain ${worker.worker_id}`}
            className="p-1.5 rounded text-slate-500 hover:text-cyan-400 hover:bg-cyan-400/10 transition-colors disabled:opacity-30"
          >
            <RefreshCw size={14} />
          </button>
          </div>
        </td>
      </tr>

      {expanded && (
        <tr className="bg-slate-950/50 border-b border-slate-800">
          <td colSpan={9} className="px-6 py-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-sm">
              <div className="space-y-2">
                <h4 className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">Integration</h4>
                <div className="flex justify-between">
                  <span className="text-slate-500">Transport</span>
                  <span className="text-white font-mono">{worker.transport_mode ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Adapter</span>
                  <span className="text-white font-mono">{worker.adapter_entrypoint ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Sandbox</span>
                  <span className="text-white font-mono">{worker.sandbox_profile ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Max Concurrent</span>
                  <span className="text-white font-mono">{worker.max_concurrent_tasks ?? "—"}</span>
                </div>
              </div>
              <div className="space-y-2">
                <h4 className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">Source</h4>
                <div className="flex justify-between items-center">
                  <span className="text-slate-500">Repository</span>
                  {worker.source_repo && worker.source_repo !== "local" ? (
                    <a
                      href={worker.source_repo}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-400 hover:text-blue-300 flex items-center gap-1 text-xs"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {worker.source_repo.replace("https://github.com/", "")}
                      <ExternalLink size={10} />
                    </a>
                  ) : (
                    <span className="text-slate-400 font-mono">{worker.source_repo ?? "local"}</span>
                  )}
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Revision</span>
                  <span className="text-white font-mono text-xs">{worker.source_revision ?? "—"}</span>
                </div>
                {worker.capability_ids && worker.capability_ids.length > 0 && (
                  <div className="mt-3">
                    <span className="text-slate-500 text-xs">Capabilities</span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {worker.capability_ids.map((cap) => (
                        <span key={cap} className="px-1.5 py-0.5 bg-blue-900/30 text-blue-300 border border-blue-800 rounded text-xs font-mono">
                          {cap}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
            {(activationBlocked || actionError || latestEvaluation) && (
              <div className="mt-4 border-t border-slate-800 pt-4 text-sm">
                {activationBlocked && (
                  <div className="mb-3 rounded border border-amber-700 bg-amber-400/10 px-3 py-2 text-amber-300" role="alert">
                    Blocked until approval: this external worker needs an approved evaluation before activation.
                  </div>
                )}
                {actionError && (
                  <div className="mb-3 rounded border border-rose-700 bg-rose-400/10 px-3 py-2 text-rose-300" role="alert">
                    {actionError}
                  </div>
                )}
                {latestEvaluation && (
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="text-xs font-medium text-slate-400 uppercase tracking-wider">Latest Evaluation</h4>
                      {latestEvaluation.evaluated_at && (
                        <span className="text-xxs text-slate-500">
                          {new Date(latestEvaluation.evaluated_at).toLocaleString()}
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                      <div>
                        <div className="text-slate-500 text-xs">Verdict</div>
                        <div className="text-white font-mono">{latestEvaluation.verdict}</div>
                      </div>
                      <div>
                        <div className="text-slate-500 text-xs">Risk</div>
                        <div className="text-white font-mono">{latestEvaluation.risk_tier ?? "unknown"}</div>
                      </div>
                      <div>
                        <div className="text-slate-500 text-xs">Score</div>
                        <div className="text-white font-mono">{latestEvaluation.overall_score ?? "-"}</div>
                      </div>
                      <div>
                        <div className="text-slate-500 text-xs">Recommended</div>
                        <div className="text-white font-mono">{latestEvaluation.recommended_status ?? "-"}</div>
                      </div>
                    </div>
                    {latestEvaluation.blocked_reasons && latestEvaluation.blocked_reasons.length > 0 && (
                      <div className="mb-3 rounded border border-rose-800 bg-rose-500/5 px-3 py-2 text-xs text-rose-300">
                        <span className="font-semibold">Blocked reasons: </span>
                        {latestEvaluation.blocked_reasons.join("; ")}
                      </div>
                    )}
                    <div className="space-y-2">
                      {Object.entries(latestEvaluation.checks ?? {}).map(([name, check]) => {
                        const isOpen = expandedChecks[name] ?? false;
                        const hasDetails = Boolean(check.details);
                        return (
                          <div
                            key={name}
                            className="rounded border border-slate-800 bg-slate-950 px-3 py-2"
                          >
                            <button
                              type="button"
                              onClick={() => hasDetails && toggleCheck(name)}
                              disabled={!hasDetails}
                              aria-expanded={isOpen}
                              aria-label={`${name} evaluation check`}
                              className={clsx(
                                "w-full flex items-center justify-between gap-2 text-left",
                                hasDetails && "cursor-pointer hover:text-white"
                              )}
                            >
                              <div className="flex items-center gap-2 min-w-0">
                                {hasDetails ? (
                                  isOpen ? (
                                    <ChevronDown size={12} className="text-slate-500 flex-shrink-0" />
                                  ) : (
                                    <ChevronRight size={12} className="text-slate-500 flex-shrink-0" />
                                  )
                                ) : (
                                  <span className="w-3" />
                                )}
                                <span className="text-slate-300 font-mono text-xs truncate">{name}</span>
                              </div>
                              <span
                                className={clsx(
                                  "text-xs flex-shrink-0",
                                  check.passed ? "text-emerald-400" : "text-rose-400"
                                )}
                              >
                                {check.status ?? (check.passed ? "PASSED" : "FAILED")}
                                {typeof check.score === "number" && (
                                  <span className="ml-1.5 text-slate-500">({check.score})</span>
                                )}
                              </span>
                            </button>
                            {isOpen && hasDetails && (
                              <div className="text-xs text-slate-500 mt-2 pl-5 border-l border-slate-800">
                                {check.details}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                    {latestEvaluation.requires_human_approval && (
                      <div className="mt-3 text-xxs text-amber-300/80">
                        Note: this evaluation requires explicit human approval before activation.
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function RegisterWorkerModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({
    worker_id: "",
    name: "",
    description: "",
    team_id: "",
    source_repo: "",
    transport_mode: "process",
    adapter_entrypoint: "",
    sandbox_profile: "restricted",
    evaluation_status: "pending",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (!form.worker_id || !form.name) {
      setError("Worker ID and name are required");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/workers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!res.ok) {
        const d = await res.json();
        setError(d.error ?? d.detail ?? "Failed to register worker");
        return;
      }
      onCreated();
      onClose();
    } catch {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" role="dialog" aria-modal="true" aria-labelledby="register-worker-title">
      <div className="bg-slate-800 rounded-xl border border-slate-700 w-full max-w-lg shadow-xl">
        <div className="p-5 border-b border-slate-700 flex items-center gap-3">
          <Package className="w-5 h-5 text-blue-400" />
          <h2 id="register-worker-title" className="text-white font-semibold">Register Worker</h2>
        </div>
        <div className="p-5 space-y-4">
          {error && (
            <div className="bg-rose-500/10 border border-rose-500/30 rounded px-3 py-2 text-rose-400 text-sm" role="alert">{error}</div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Worker ID *</label>
              <input
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.worker_id}
                onChange={(e) => setForm({ ...form, worker_id: e.target.value })}
                placeholder="my_worker_1"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Name *</label>
              <input
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="My Worker Agent"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Description</label>
            <input
              className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What this worker does"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Team ID</label>
              <input
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.team_id}
                onChange={(e) => setForm({ ...form, team_id: e.target.value })}
                placeholder="office_chrm"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Transport Mode</label>
              <select
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.transport_mode}
                onChange={(e) => setForm({ ...form, transport_mode: e.target.value })}
              >
                {["process", "http", "mcp", "oci", "human"].map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">GitHub Repository URL</label>
            <input
              className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
              value={form.source_repo}
              onChange={(e) => setForm({ ...form, source_repo: e.target.value })}
              placeholder="https://github.com/org/repo"
            />
            <p className="text-xs text-slate-500 mt-1">
              Optional. Upstream repo will be evaluated for provenance, scans, sandbox policy, and compatibility before activation.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Adapter Entrypoint</label>
              <input
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.adapter_entrypoint}
                onChange={(e) => setForm({ ...form, adapter_entrypoint: e.target.value })}
                placeholder="WorkerAgent"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Sandbox Profile</label>
              <select
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.sandbox_profile}
                onChange={(e) => setForm({ ...form, sandbox_profile: e.target.value })}
              >
                {["restricted", "standard", "gvisor", "firecracker"].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
        <div className="p-5 border-t border-slate-700 flex justify-end gap-3">
          <button className="px-4 py-2 text-sm text-slate-300 hover:text-white" onClick={onClose}>
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50 transition-colors"
            onClick={submit}
            disabled={loading}
          >
            {loading ? "Registering…" : "Register Worker"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function WorkersPage() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // Filter state — initialized from localStorage so user choices persist
  // across page reloads. Falls back to empty/ALL on first visit.
  const [search, setSearch] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return window.localStorage.getItem(STORAGE_KEYS.search) ?? "";
  });
  const [statusFilter, setStatusFilter] = useState<string>(() => {
    if (typeof window === "undefined") return "ALL";
    return window.localStorage.getItem(STORAGE_KEYS.status) ?? "ALL";
  });
  const [showRegister, setShowRegister] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState("");
  // Ref to the search input so keyboard shortcuts can focus it.
  const searchRef = useRef<HTMLInputElement | null>(null);

  // Persist filter values whenever they change. Safe to run on the client only.
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEYS.search, search);
    } catch {
      // localStorage may be unavailable (private mode, quota); silently ignore.
    }
  }, [search]);
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEYS.status, statusFilter);
    } catch {
      // ignore
    }
  }, [statusFilter]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const workersRes = await fetch("/api/workers");
      if (!workersRes.ok) throw new Error(await workersRes.text());
      const workersData = await workersRes.json();
      setWorkers(Array.isArray(workersData) ? workersData : []);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Global keyboard shortcuts:
  //   "/" — focus search
  //   "r" — refresh list (when not typing in a form field)
  //   "n" — open the Register Worker modal (when not typing in a form field)
  //   "Escape" — clear search (if the input is focused)
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const inField =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        (target?.isContentEditable ?? false);
      if (e.key === "Escape" && document.activeElement === searchRef.current) {
        setSearch("");
        searchRef.current?.blur();
        e.preventDefault();
        return;
      }
      if (inField) return;
      if (e.key === "/") {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
        return;
      }
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        void load();
        return;
      }
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        setShowRegister(true);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [load]);

  const filtered = workers.filter((w) => {
    const workerId = w.worker_id ?? "";
    const workerName = w.name ?? "";
    const teamId = w.team_id ?? "";
    const matchSearch =
      !search ||
      workerId.toLowerCase().includes(search.toLowerCase()) ||
      workerName.toLowerCase().includes(search.toLowerCase()) ||
      teamId.toLowerCase().includes(search.toLowerCase());
    const matchStatus = statusFilter === "ALL" || w.status === statusFilter;
    return matchSearch && matchStatus;
  });

  // Per-status counts power the chip badges. Computed from the unfiltered list
  // so the chips always show the full population, not just what passes the
  // current search query.
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { ALL: workers.length };
    for (const w of workers) {
      counts[w.status] = (counts[w.status] ?? 0) + 1;
    }
    return counts;
  }, [workers]);

  const STATUS_FILTERS = [
    { id: "ALL", label: "All", tone: "blue" as const },
    { id: "ACTIVE", label: "Active", tone: "emerald" as const },
    { id: "INACTIVE", label: "Inactive", tone: "gray" as const },
    { id: "DRAINING", label: "Draining", tone: "amber" as const },
    { id: "PENDING_EVALUATION", label: "Pending Eval", tone: "amber" as const },
    { id: "REJECTED", label: "Rejected", tone: "amber" as const },
    { id: "ERROR", label: "Error", tone: "amber" as const },
  ];

  const workerIds = useMemo(() => filtered.map((w) => w.id).filter((id): id is string => Boolean(id)), [filtered]);
  const selection = useBulkSelection(workerIds);
  // Drop selections when the filter or list changes.
  useEffect(() => {
    selection.prune();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workerIds.join(",")]);

  async function handleBulkDelete() {
    if (selection.selectedCount === 0) return;
    const targets = workers.filter((w) => w.id != null && selection.selected.has(w.id));
    setBulkDeleting(true);
    setBulkError("");
    let failed = 0;
    try {
      const results = await Promise.allSettled(
        targets.map(async (w) => {
          // Workers use the worker_id (slug) on the API path, not the numeric id.
          const res = await fetch(`/api/workers/${encodeURIComponent(w.worker_id)}`, { method: "DELETE" });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
        })
      );
      for (const r of results) if (r.status === "rejected") failed++;
      if (failed > 0) {
        setBulkError(
          `Deleted ${targets.length - failed} of ${targets.length} worker${targets.length === 1 ? "" : "s"} (${failed} failed).`
        );
      }
      await load();
      selection.clear();
    } finally {
      setBulkDeleting(false);
    }
  }

  const activeCount = workers.filter((w) => w.status === "ACTIVE").length;
  const inactiveCount = workers.filter((w) => w.status === "INACTIVE").length;
  const errorCount = workers.filter((w) => w.status === "ERROR").length;
  const pendingCount = workers.filter(
    (w) => w.evaluation_status === "pending" || w.evaluation_status === "conditional"
  ).length;

  return (
    <div className="dashboard-page">
      <PageHeader
        icon="users"
        title="Hiring Board"
        description="Evaluate worker candidates, inspect guarded checks, and control activation state."
        actions={
          <>
            <button
              onClick={load}
              title="Refresh (R)"
              aria-label="Refresh workers"
              className="p-2 rounded-lg border border-slate-700 hover:bg-slate-800 text-slate-400 hover:text-slate-100 hover:border-slate-500 transition-colors"
            >
              <RefreshCw className={clsx("w-4 h-4", loading && "animate-spin")} />
            </button>
            <button
              onClick={() => setShowRegister(true)}
              title="Register Worker (N)"
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium shadow-sm shadow-blue-500/10 transition-colors"
            >
              <Plus className="w-4 h-4" />
              Register Worker
            </button>
          </>
        }
      />

      <div className="flex items-start gap-3 p-4 rounded-lg border border-blue-500/30 bg-blue-500/5">
        <Settings className="w-5 h-5 text-blue-400 mt-0.5 shrink-0" />
        <div className="text-sm text-blue-200/90 space-y-1">
          <p>
            <strong className="text-blue-100">Adapter model:</strong> Workers integrate via thin
            compatibility layers. Upstream repositories are ingested and evaluated for provenance,
            security scans, sandbox policy, budget posture, and compatibility before activation.
          </p>
          <p className="text-blue-200/70">
            Approved tools are provided centrally by the tool-service. Workers consume them
            through a common interface, and activation stays blocked until the evaluation verdict
            is approved.
          </p>
          <p className="text-blue-200/70 text-xs">
            Shortcuts: <kbd className="px-1.5 py-0.5 rounded border border-blue-400/30 bg-blue-500/10 text-blue-200 font-mono text-xxs">/</kbd> focus search
            {" · "}
            <kbd className="px-1.5 py-0.5 rounded border border-blue-400/30 bg-blue-500/10 text-blue-200 font-mono text-xxs">R</kbd> refresh
            {" · "}
            <kbd className="px-1.5 py-0.5 rounded border border-blue-400/30 bg-blue-500/10 text-blue-200 font-mono text-xxs">N</kbd> new worker
            {" · "}
            <kbd className="px-1.5 py-0.5 rounded border border-blue-400/30 bg-blue-500/10 text-blue-200 font-mono text-xxs">Esc</kbd> clear search
          </p>
        </div>
      </div>

      {/* Stats — use KpiCard for visual consistency with the rest of the dashboard */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Candidates"
          value={workers.length}
          icon="users"
          tone="info"
          hint={pendingCount > 0 ? `${pendingCount} pending review` : `${filtered.length} visible`}
        />
        <KpiCard
          label="Active"
          value={activeCount}
          icon="check-circle"
          tone="positive"
          hint={activeCount > 0 ? "Serving traffic" : "No workers serving"}
        />
        <KpiCard
          label="Inactive"
          value={inactiveCount}
          icon="x-circle"
          tone="neutral"
        />
        <KpiCard
          label="Error"
          value={errorCount}
          icon="alert-triangle"
          tone={errorCount > 0 ? "negative" : "neutral"}
          hint={errorCount > 0 ? "Investigate before drain" : "All healthy"}
        />
      </div>

      {error && (
        <ErrorBanner tone="error" title="Workers load failed">
          {error}
        </ErrorBanner>
      )}

      {bulkError && <ErrorBanner tone="warning">{bulkError}</ErrorBanner>}

      {selection.selectedCount > 0 && (
        <BulkActionBar
          selectedCount={selection.selectedCount}
          totalCount={filtered.length}
          loading={bulkDeleting}
          action="delete"
          actionLabel={`Delete ${selection.selectedCount} selected`}
          confirmMessage={`Delete ${selection.selectedCount} worker${selection.selectedCount === 1 ? "" : "s"}? This deregisters the worker from the registry and cannot be undone.`}
          onAction={handleBulkDelete}
          onClear={selection.clear}
        />
      )}

      {/* Filters */}
      <div className="dashboard-toolbar flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1 min-w-0">
          <input
            ref={searchRef}
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search workers by id, name, or team…"
            aria-label="Search workers"
            className="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800 transition-colors"
            >
              <XCircle size={14} />
            </button>
          )}
        </div>
        <div
          role="group"
          aria-label="Filter by status"
          className="flex flex-wrap items-center gap-1.5"
        >
          {STATUS_FILTERS.map((s) => (
            <FilterChip
              key={s.id}
              active={statusFilter === s.id}
              onClick={() => setStatusFilter(s.id)}
              activeTone={s.tone}
              count={statusCounts[s.id] ?? 0}
            >
              {s.label}
            </FilterChip>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="dashboard-table">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase tracking-wider">
                <th className="w-10 px-4 py-3">
                  <SelectAllCheckbox
                    checked={selection.isAllSelected}
                    indeterminate={selection.isIndeterminate}
                    onChange={selection.toggleAll}
                    ariaLabel="Select all workers"
                  />
                </th>
                <th className="w-8 px-4 py-3" aria-label="Expand" />
                <th className="text-left px-4 py-3">Worker ID</th>
                <th className="text-left px-4 py-3">Name</th>
                <th className="text-left px-4 py-3">Team</th>
                <th className="text-left px-4 py-3">Status</th>
                <th className="text-left px-4 py-3">Evaluation</th>
                <th className="text-left px-4 py-3">Version</th>
                <th className="w-12 px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={9} className="px-4 py-10 text-center text-slate-500">
                    <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
                    Loading workers…
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={9} className="p-0">
                    {workers.length === 0 ? (
                      <EmptyState
                        icon="users"
                        title="No workers registered"
                        description="Workers are seeded from YAML manifests on startup, or you can register one manually."
                        action={
                          <button
                            onClick={() => setShowRegister(true)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
                          >
                            <Plus size={14} />
                            Register Worker
                          </button>
                        }
                        className="!border-0 !bg-transparent"
                      />
                    ) : (
                      <EmptyState
                        icon="package"
                        title="No workers match your filters"
                        description="Try a broader status filter or clear the search."
                        action={
                          <button
                            onClick={() => { setStatusFilter("ALL"); setSearch(""); }}
                            className="text-xs text-blue-400 hover:text-blue-300"
                          >
                            Clear filters
                          </button>
                        }
                        className="!border-0 !bg-transparent"
                      />
                    )}
                  </td>
                </tr>
              ) : (
                filtered.map((w) => (
                  <WorkerRow
                    key={w.id ?? w.worker_id}
                    worker={w}
                    onStatusChange={load}
                    selected={w.id != null && selection.selected.has(w.id)}
                    onSelectChange={() => w.id != null && selection.toggle(w.id)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showRegister && (
        <RegisterWorkerModal
          onClose={() => setShowRegister(false)}
          onCreated={load}
        />
      )}
    </div>
  );
}
