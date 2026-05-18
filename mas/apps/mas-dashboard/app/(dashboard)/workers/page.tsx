"use client";

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Users, CheckCircle, AlertTriangle, XCircle, Package, Plus, ChevronDown, ChevronRight, ExternalLink, Settings, Power } from "lucide-react";
import { clsx } from "clsx";

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

const STATUS_CONFIG: Record<string, { label: string; icon: typeof CheckCircle; cls: string }> = {
  ACTIVE: { label: "Active", icon: CheckCircle, cls: "text-green-400 bg-green-400/10 border-green-700" },
  INACTIVE: { label: "Inactive", icon: XCircle, cls: "text-gray-400 bg-gray-400/10 border-gray-700" },
  DEREGISTERED: { label: "Deregistered", icon: XCircle, cls: "text-red-400 bg-red-400/10 border-red-700" },
  PENDING: { label: "Pending", icon: AlertTriangle, cls: "text-yellow-400 bg-yellow-400/10 border-yellow-700" },
  ERROR: { label: "Error", icon: AlertTriangle, cls: "text-red-400 bg-red-400/10 border-red-700" },
};

const EVAL_COLORS: Record<string, string> = {
  approved: "text-green-400",
  pending: "text-yellow-400",
  rejected: "text-red-400",
  unknown: "text-gray-400",
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

function WorkerRow({ worker, onStatusChange }: { worker: Worker; onStatusChange: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [transitioning, setTransitioning] = useState(false);

  async function toggleStatus() {
    const newStatus = worker.status === "ACTIVE" ? "INACTIVE" : "ACTIVE";
    setTransitioning(true);
    try {
      await fetch(`/api/workers/${worker.id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      onStatusChange();
    } finally {
      setTransitioning(false);
    }
  }

  return (
    <>
      <tr
        className="border-b border-gray-800 hover:bg-gray-800/30 cursor-pointer transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="px-4 py-3 text-gray-500 w-8">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </td>
        <td className="px-4 py-3">
          <div className="font-mono text-sm text-white font-medium">{worker.worker_id}</div>
          {worker.description && (
            <div className="text-xs text-gray-500 truncate max-w-xs">{worker.description}</div>
          )}
        </td>
        <td className="px-4 py-3 text-xs text-gray-400">{worker.name}</td>
        <td className="px-4 py-3 text-xs text-gray-400">{worker.team_id ?? "—"}</td>
        <td className="px-4 py-3">
          <StatusBadge status={worker.status} />
        </td>
        <td className="px-4 py-3">
          <span className={clsx("text-xs", EVAL_COLORS[worker.evaluation_status ?? "unknown"])}>
            {worker.evaluation_status ?? "unknown"}
          </span>
        </td>
        <td className="px-4 py-3 text-xs text-gray-400 font-mono">
          {worker.version ?? "—"}
        </td>
        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={toggleStatus}
            disabled={transitioning}
            title={worker.status === "ACTIVE" ? "Deactivate" : "Activate"}
            className={clsx(
              "p-1.5 rounded transition-colors disabled:opacity-40",
              worker.status === "ACTIVE"
                ? "text-green-400 hover:text-red-400 hover:bg-red-400/10"
                : "text-gray-500 hover:text-green-400 hover:bg-green-400/10"
            )}
          >
            <Power size={14} />
          </button>
        </td>
      </tr>

      {expanded && (
        <tr className="bg-gray-900/50 border-b border-gray-800">
          <td colSpan={8} className="px-6 py-4">
            <div className="grid grid-cols-2 gap-6 text-sm">
              <div className="space-y-2">
                <h4 className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-3">Integration</h4>
                <div className="flex justify-between">
                  <span className="text-gray-500">Transport</span>
                  <span className="text-white font-mono">{worker.transport_mode ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Adapter</span>
                  <span className="text-white font-mono">{worker.adapter_entrypoint ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Sandbox</span>
                  <span className="text-white font-mono">{worker.sandbox_profile ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Max Concurrent</span>
                  <span className="text-white font-mono">{worker.max_concurrent_tasks ?? "—"}</span>
                </div>
              </div>
              <div className="space-y-2">
                <h4 className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-3">Source</h4>
                <div className="flex justify-between items-center">
                  <span className="text-gray-500">Repository</span>
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
                    <span className="text-gray-400 font-mono">{worker.source_repo ?? "local"}</span>
                  )}
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Revision</span>
                  <span className="text-white font-mono text-xs">{worker.source_revision ?? "—"}</span>
                </div>
                {worker.capability_ids && worker.capability_ids.length > 0 && (
                  <div className="mt-3">
                    <span className="text-gray-500 text-xs">Capabilities</span>
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
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-800 rounded-xl border border-gray-700 w-full max-w-lg shadow-xl">
        <div className="p-5 border-b border-gray-700 flex items-center gap-3">
          <Package className="w-5 h-5 text-blue-400" />
          <h2 className="text-white font-semibold">Register Worker</h2>
        </div>
        <div className="p-5 space-y-4">
          {error && (
            <div className="bg-red-500/10 border border-red-500/30 rounded px-3 py-2 text-red-400 text-sm">{error}</div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Worker ID *</label>
              <input
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.worker_id}
                onChange={(e) => setForm({ ...form, worker_id: e.target.value })}
                placeholder="my_worker_1"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Name *</label>
              <input
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="My Worker Agent"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Description</label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What this worker does"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Team ID</label>
              <input
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.team_id}
                onChange={(e) => setForm({ ...form, team_id: e.target.value })}
                placeholder="dept_production"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Transport Mode</label>
              <select
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.transport_mode}
                onChange={(e) => setForm({ ...form, transport_mode: e.target.value })}
              >
                {["process", "http", "grpc", "redis"].map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">GitHub Repository URL</label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
              value={form.source_repo}
              onChange={(e) => setForm({ ...form, source_repo: e.target.value })}
              placeholder="https://github.com/org/repo"
            />
            <p className="text-xs text-gray-500 mt-1">
              Optional. Upstream repo will be evaluated for fit, security, and compatibility before activation.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Adapter Entrypoint</label>
              <input
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.adapter_entrypoint}
                onChange={(e) => setForm({ ...form, adapter_entrypoint: e.target.value })}
                placeholder="WorkerAgent"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Sandbox Profile</label>
              <select
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.sandbox_profile}
                onChange={(e) => setForm({ ...form, sandbox_profile: e.target.value })}
              >
                {["restricted", "standard", "elevated", "none"].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
        <div className="p-5 border-t border-gray-700 flex justify-end gap-3">
          <button className="px-4 py-2 text-sm text-gray-300 hover:text-white" onClick={onClose}>
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50"
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
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [showRegister, setShowRegister] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/workers");
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setWorkers(Array.isArray(data) ? data : []);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
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

  const activeCount = workers.filter((w) => w.status === "ACTIVE").length;
  const inactiveCount = workers.filter((w) => w.status === "INACTIVE").length;
  const errorCount = workers.filter((w) => w.status === "ERROR").length;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <Users className="w-6 h-6 text-blue-400" />
            Workers
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Configuration-driven integration units. Workers are YAML-defined contracts that describe
            how capabilities are integrated into the MAS.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            className="p-2 rounded-lg border border-gray-700 hover:bg-gray-800 text-gray-400"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setShowRegister(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm"
          >
            <Plus className="w-4 h-4" />
            Register Worker
          </button>
        </div>
      </div>

      {/* Info banner */}
      <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4 flex gap-3">
        <Settings className="w-5 h-5 text-blue-400 mt-0.5 shrink-0" />
        <div className="text-sm text-blue-300 space-y-1">
          <p>
            <strong>Adapter model:</strong> Workers integrate via thin compatibility layers.
            Upstream repositories are ingested and evaluated for fit, security, and architectural
            compatibility before activation.
          </p>
          <p className="text-blue-400/70">
            Shared tools (browser, web access, search, storage) are provided centrally by the
            tool-service — workers consume them through a common interface rather than bundling
            their own dependencies.
          </p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total Workers", value: workers.length, cls: "text-white", border: "border-gray-800" },
          { label: "Active", value: activeCount, cls: "text-green-400", border: "border-green-900/40" },
          { label: "Inactive", value: inactiveCount, cls: "text-gray-400", border: "border-gray-800" },
          { label: "Error", value: errorCount, cls: "text-red-400", border: "border-red-900/40" },
        ].map((s) => (
          <div key={s.label} className={clsx("bg-gray-900 rounded-xl p-4 border", s.border)}>
            <div className={clsx("text-2xl font-bold", s.cls)}>{s.value}</div>
            <div className="text-xs text-gray-400">{s.label}</div>
          </div>
        ))}
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search workers..."
          className="flex-1 px-4 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
        />
        <div className="flex gap-1 bg-gray-900 border border-gray-700 rounded-lg p-1">
          {["ALL", "ACTIVE", "INACTIVE", "ERROR"].map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={clsx(
                "px-3 py-1 rounded text-xs transition-colors",
                statusFilter === s
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-white"
              )}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-400 text-xs uppercase tracking-wider">
              <th className="w-8 px-4 py-3" />
              <th className="text-left px-4 py-3">Worker ID</th>
              <th className="text-left px-4 py-3">Name</th>
              <th className="text-left px-4 py-3">Team</th>
              <th className="text-left px-4 py-3">Status</th>
              <th className="text-left px-4 py-3">Evaluation</th>
              <th className="text-left px-4 py-3">Version</th>
              <th className="w-12 px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-500">
                  <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
                  Loading workers…
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-500">
                  <Package className="w-8 h-8 mx-auto mb-2 opacity-40" />
                  {workers.length === 0
                    ? "No workers registered. Workers are seeded from YAML manifests on startup."
                    : "No workers match your filters."}
                </td>
              </tr>
            ) : (
              filtered.map((w) => (
                <WorkerRow key={w.id ?? w.worker_id} worker={w} onStatusChange={load} />
              ))
            )}
          </tbody>
        </table>
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
