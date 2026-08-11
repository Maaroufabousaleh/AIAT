"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Plus,
  Key,
  Shield,
  Eye,
  EyeOff,
  Trash2,
  RefreshCw,
  Copy,
  Check,
  ScrollText,
  ExternalLink,
} from "lucide-react";
import { clsx } from "clsx";
import { BulkActionBar, RowCheckbox, SelectAllCheckbox } from "@/components/ui/BulkActionBar";
import { useBulkSelection } from "@/lib/use-bulk-selection";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { formatLocaleInTz } from "@/lib/datetime";

/** Tiny utility: copy a string to the clipboard with a graceful fallback. */
async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    // Fallback for older browsers / non-secure contexts
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

type SecretPolicy = {
  allowed_requesters: string[];
  allowed_contexts: string[];
  rate_limit_per_minute: number;
  require_approval: boolean;
  enabled: boolean;
  expires_at: string | null;
};

type Credential = {
  id: string;
  name: string;
  description: string;
  secret_type: string;
  policy: SecretPolicy;
  usage_count: number;
  last_used_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  placeholder: string;
};

const SECRET_TYPES = ["api_key", "token", "password", "certificate", "connection_string", "other"];

function Badge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    api_key: "bg-blue-500/20 text-blue-300 border border-blue-500/20",
    token: "bg-purple-500/20 text-purple-300 border border-purple-500/20",
    password: "bg-red-500/20 text-red-300 border border-red-500/20",
    certificate: "bg-yellow-500/20 text-yellow-300 border border-yellow-500/20",
    connection_string: "bg-green-500/20 text-green-300 border border-green-500/20",
    other: "bg-slate-500/20 text-slate-300 border border-slate-500/20",
  };
  return (
    <span className={clsx("px-2 py-0.5 rounded text-xs font-mono", colors[type] ?? colors.other)}>
      {type.replace("_", " ")}
    </span>
  );
}

function PolicyEditor({
  policy,
  onChange,
}: {
  policy: SecretPolicy;
  onChange: (p: SecretPolicy) => void;
}) {
  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs text-slate-400 mb-1">
          Allowed Requesters (comma-separated, empty = any)
        </label>
        <input
          className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-white focus-visible:border-blue-500 transition-colors"
          value={policy.allowed_requesters.join(", ")}
          onChange={(e) =>
            onChange({
              ...policy,
              allowed_requesters: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="llm-gateway, tool-service, ceo"
        />
      </div>
      <div>
        <label className="block text-xs text-slate-400 mb-1">
          Allowed Contexts (comma-separated, empty = any)
        </label>
        <input
          className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-white focus-visible:border-blue-500 transition-colors"
          value={policy.allowed_contexts.join(", ")}
          onChange={(e) =>
            onChange({
              ...policy,
              allowed_contexts: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="llm-call, tool-exec"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-slate-400 mb-1">Rate limit / min (0 = unlimited)</label>
          <input
            type="number"
            min={0}
            className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-white focus-visible:border-blue-500 transition-colors"
            value={policy.rate_limit_per_minute}
            onChange={(e) => onChange({ ...policy, rate_limit_per_minute: Number(e.target.value) })}
          />
        </div>
        <div className="flex flex-col justify-end gap-2">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={policy.enabled}
              onChange={(e) => onChange({ ...policy, enabled: e.target.checked })}
              className="accent-blue-500"
            />
            <span className="text-sm text-slate-300">Enabled</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={policy.require_approval}
              onChange={(e) => onChange({ ...policy, require_approval: e.target.checked })}
              className="accent-blue-500"
            />
            <span className="text-sm text-slate-300">Require approval</span>
          </label>
        </div>
      </div>
    </div>
  );
}

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({
    name: "",
    value: "",
    description: "",
    secret_type: "api_key",
    created_by: "human",
  });
  const [policy, setPolicy] = useState<SecretPolicy>({
    allowed_requesters: [],
    allowed_contexts: [],
    rate_limit_per_minute: 0,
    require_approval: false,
    enabled: true,
    expires_at: null,
  });
  const [showValue, setShowValue] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (!form.name || !form.value) {
      setError("Name and value are required");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, policy }),
      });
      if (!res.ok) {
        const d = await res.json();
        setError(d.detail ?? "Failed to create credential");
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
      <div className="dashboard-surface-strong w-full max-w-lg shadow-xl">
        <div className="p-5 border-b border-slate-700 flex items-center gap-3">
          <Key className="w-5 h-5 text-blue-400" />
          <h2 className="text-white font-semibold">New Credential</h2>
        </div>
        <div className="p-5 space-y-4">
          {error && (
            <div className="bg-red-500/10 border border-red-500/30 rounded px-3 py-2 text-red-400 text-sm">
              {error}
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Name *</label>
              <input
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-white focus-visible:border-blue-500 transition-colors"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value.toUpperCase().replace(/\s/g, "_") })}
                placeholder="OPENAI_API_KEY"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Type</label>
              <select
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-white focus-visible:border-blue-500 transition-colors"
                value={form.secret_type}
                onChange={(e) => setForm({ ...form, secret_type: e.target.value })}
              >
                {SECRET_TYPES.map((t) => (
                  <option key={t} value={t}>{t.replace("_", " ")}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Value *</label>
            <div className="relative">
              <input
                type={showValue ? "text" : "password"}
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-white pr-10 focus-visible:border-blue-500 transition-colors"
                value={form.value}
                onChange={(e) => setForm({ ...form, value: e.target.value })}
                placeholder="sk-..."
              />
              <button
                type="button"
                aria-label={showValue ? "Hide secret value" : "Show secret value"}
                className="absolute right-2 top-1.5 text-slate-400 hover:text-white p-1 rounded transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70"
                onClick={() => setShowValue(!showValue)}
              >
                {showValue ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Description</label>
            <input
              className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm text-white focus-visible:border-blue-500 transition-colors"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="OpenAI API key for LLM gateway"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-2 flex items-center gap-2">
              <Shield className="w-3.5 h-3.5" /> Access Policy
            </label>
            <PolicyEditor policy={policy} onChange={setPolicy} />
          </div>
        </div>
        <div className="p-5 border-t border-slate-700 flex justify-end gap-3">
          <button
            className="px-4 py-2 text-sm text-slate-300 hover:text-white rounded transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white rounded-lg disabled:opacity-50 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            onClick={submit}
            disabled={loading}
          >
            {loading ? "Saving…" : "Save Credential"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Inline copy-to-clipboard control used in the placeholder column.
 * Briefly shows a checkmark + "Copied" label to confirm the action.
 */
function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const handleClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const ok = await copyToClipboard(value);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  return (
    <span className="inline-flex items-center gap-1.5 group/copy">
      <code className="bg-slate-800 px-2 py-0.5 rounded text-blue-300 text-xs font-mono">
        {value}
      </code>
      <button
        type="button"
        onClick={handleClick}
        aria-label={copied ? `Copied ${value}` : `Copy placeholder ${value}`}
        title={copied ? "Copied" : "Copy placeholder"}
        className={clsx(
          "inline-flex items-center justify-center w-6 h-6 rounded transition-all duration-150",
          "focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-1 focus-visible:ring-offset-slate-900",
          copied
            ? "bg-emerald-500/15 text-emerald-300"
            : "text-white/80 hover:text-blue-100 hover:bg-blue-500/10 opacity-60 group-hover/copy:opacity-100"
        )}
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </span>
  );
}

export default function CredentialsPage() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const credentialsRef = useRef<Credential[] | null>(null);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loadStale, setLoadStale] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const credentialIds = useMemo(() => credentials.map((c) => c.id), [credentials]);
  const selection = useBulkSelection(credentialIds);
  useEffect(() => {
    selection.prune();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [credentialIds.join(",")]);

  async function handleBulkDelete() {
    if (selection.selectedCount === 0) return;
    const targets = credentials.filter((c) => selection.selected.has(c.id));
    setBulkDeleting(true);
    let failed = 0;
    try {
      const results = await Promise.allSettled(
        targets.map(async (c) => {
          const res = await fetch(`/api/credentials/${encodeURIComponent(c.name)}`, { method: "DELETE" });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
        })
      );
      for (const r of results) if (r.status === "rejected") failed++;
      if (failed > 0) {
        setError(`Deleted ${targets.length - failed} of ${targets.length} credentials (${failed} failed).`);
      }
      await load();
      selection.clear();
    } finally {
      setBulkDeleting(false);
    }
  }

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const res = await fetch("/api/credentials", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next = await res.json();
      if (!Array.isArray(next)) throw new Error("Invalid credentials response");
      credentialsRef.current = next;
      setCredentials(next);
      setHasLoaded(true);
      setLoadStale(false);
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : "Failed to load credentials");
      setLoadStale(credentialsRef.current !== null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const requestRefresh = () => {
    if (loading) return;
    void load();
  };

  async function deleteCredential(name: string) {
    if (!confirm(`Delete credential "${name}"? This cannot be undone.`)) return;
    setDeleting(name);
    try {
      const res = await fetch(`/api/credentials/${name}`, { method: "DELETE" });
      if (!res.ok) {
        const msg = await res.text().catch(() => `HTTP ${res.status}`);
        setError(`Failed to delete "${name}": ${msg}`);
        return;
      }
      load();
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="dashboard-page">
      <PageHeader
        icon="lock"
        title="Credentials Manager"
        description={`${credentials.length} secrets · Centralised store with policy gates`}
        actions={
          <>
            <button
              onClick={requestRefresh}
              disabled={loading}
              title="Refresh"
              aria-label="Refresh credentials"
              className="p-2 rounded-lg border border-slate-700 hover:bg-slate-800 text-slate-400 hover:text-slate-100 hover:border-slate-500 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <RefreshCw className={clsx("w-4 h-4", loading && "animate-spin")} />
            </button>
            <button
              onClick={() => setShowCreate(true)}
              aria-label="Create new credential"
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white rounded-lg text-sm font-medium shadow-sm shadow-blue-500/10 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            >
              <Plus className="w-4 h-4" />
              New Secret
            </button>
          </>
        }
      />

      <div className="flex items-start gap-3 p-4 rounded-lg border border-blue-500/30 bg-blue-500/5">
        <Shield className="w-5 h-5 text-blue-400 mt-0.5 shrink-0" />
        <div className="flex-1 text-sm text-blue-200/90">
          <strong className="text-blue-100">Security model:</strong> Real secret values are never
          logged or exposed in the UI. Each resolve attempt is audited. Agents and LLMs receive
          only placeholder references (e.g.{" "}
          <code className="bg-blue-900/40 px-1.5 py-0.5 rounded text-blue-200 text-xs font-mono">
            {"<OPENAI_API_KEY>"}
          </code>
          ). The credentials manager resolves values only inside approved execution contexts.
          <a
            href="/audit"
            className="mt-2 inline-flex items-center gap-1.5 text-blue-300 hover:text-blue-100 underline-offset-2 hover:underline transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded px-1"
            aria-label="View credential resolve audit log"
          >
            <ScrollText className="w-3.5 h-3.5" />
            View resolve audit log
            <ExternalLink className="w-3 h-3 opacity-70" />
          </a>
        </div>
      </div>

      {loadError && (
        <ErrorBanner
          tone={loadStale ? "warning" : "error"}
          title={loadStale ? "Showing last known credentials" : "Credentials load failed"}
          action={(
            <button type="button" onClick={requestRefresh} disabled={loading} className="rounded border border-current px-2.5 py-1 text-xs font-medium hover:bg-white/10 disabled:opacity-50">
              Retry
            </button>
          )}
        >
          {loadStale ? `${loadError}. The latest credentials refresh failed; retained metadata remains visible.` : loadError}
        </ErrorBanner>
      )}

      {error && (
        <ErrorBanner tone="error" title="Credential action failed">
          {error}
        </ErrorBanner>
      )}

      {/* Bulk action bar */}
      {selection.selectedCount > 0 && (
        <BulkActionBar
          selectedCount={selection.selectedCount}
          totalCount={credentials.length}
          loading={bulkDeleting}
          action="delete"
          onAction={handleBulkDelete}
          onClear={selection.clear}
        />
      )}

      {/* Table */}
      <div className="dashboard-surface overflow-hidden">
        <table className="dashboard-table">
          <thead>
            <tr className="border-b border-slate-800 text-slate-500">
              <th className="px-4 py-3 w-10">
                <SelectAllCheckbox
                  checked={selection.isAllSelected}
                  indeterminate={selection.isIndeterminate}
                  onChange={selection.toggleAll}
                  ariaLabel="Select all credentials"
                />
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider">Name</th>
              <th className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider">Type</th>
              <th className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider">Description</th>
              <th className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider">Placeholder</th>
              <th className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider">Status</th>
              <th className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider">Uses</th>
              <th className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider">Last Used</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {loading && !hasLoaded ? (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center text-slate-500">
                  Loading…
                </td>
              </tr>
            ) : loadError && !hasLoaded ? (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center text-slate-400">
                  Unable to load credentials. Use Retry to try again.
                </td>
              </tr>
            ) : credentials.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-0">
                  <EmptyState
                    icon="key"
                    title="No credentials yet"
                    description="Add a secret to give agents scoped access to external systems. The dashboard never exposes the raw value."
                    action={
                      <button
                        onClick={() => setShowCreate(true)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                      >
                        <Plus size={14} />
                        New Secret
                      </button>
                    }
                    className="!border-0 !bg-transparent"
                  />
                </td>
              </tr>
            ) : (
              credentials.map((c) => {
                const isSelected = selection.selected.has(c.id);
                const isDeleting = deleting === c.name;
                return (
                  <tr
                    key={c.id}
                    className={clsx(
                      "border-b border-slate-800/70 hover:bg-slate-800/40 transition-colors",
                      isSelected && "bg-blue-950/30 hover:bg-blue-950/40"
                    )}
                  >
                    <td className="px-4 py-3">
                      <RowCheckbox
                        checked={isSelected}
                        onChange={() => selection.toggle(c.id)}
                        ariaLabel={`Select ${c.name}`}
                      />
                    </td>
                    <td className="px-4 py-3 font-mono text-white font-medium">{c.name}</td>
                    <td className="px-4 py-3">
                      <Badge type={c.secret_type} />
                    </td>
                    <td className="px-4 py-3 text-slate-300 max-w-xs truncate">{c.description || "—"}</td>
                    <td className="px-4 py-3">
                      <CopyButton value={c.placeholder} />
                    </td>
                    <td className="px-4 py-3">
                      {c.policy.enabled ? (
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium text-emerald-300 bg-emerald-500/10 border border-emerald-500/20">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                          Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium text-slate-400 bg-slate-700/30 border border-slate-600/30">
                          <span className="w-1.5 h-1.5 rounded-full bg-slate-500" />
                          Disabled
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-400 font-mono">{c.usage_count}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {c.last_used_at
                        ? formatLocaleInTz(c.last_used_at)
                        : "Never"}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => deleteCredential(c.name)}
                        disabled={isDeleting}
                        aria-label={`Delete ${c.name}`}
                        className={clsx(
                          "p-1.5 rounded transition-colors focus-visible:ring-2 focus-visible:ring-red-400/70 focus-visible:ring-offset-1 focus-visible:ring-offset-slate-900 disabled:cursor-not-allowed",
                          isDeleting
                            ? "text-red-400 bg-red-500/10"
                            : "text-white/80 hover:text-red-100 hover:bg-red-500/10"
                        )}
                        title={isDeleting ? "Deleting…" : "Delete"}
                      >
                        <Trash2 className={clsx("w-4 h-4", isDeleting && "animate-pulse")} />
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={load}
        />
      )}
    </div>
  );
}
