"use client";

import { useState, useEffect, useCallback } from "react";
import { Plus, Key, Shield, Eye, EyeOff, Trash2, Edit, RefreshCw, Lock, AlertTriangle, CheckCircle } from "lucide-react";

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
    api_key: "bg-blue-500/20 text-blue-300",
    token: "bg-purple-500/20 text-purple-300",
    password: "bg-red-500/20 text-red-300",
    certificate: "bg-yellow-500/20 text-yellow-300",
    connection_string: "bg-green-500/20 text-green-300",
    other: "bg-gray-500/20 text-gray-300",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono ${colors[type] ?? colors.other}`}>
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
        <label className="block text-xs text-gray-400 mb-1">
          Allowed Requesters (comma-separated, empty = any)
        </label>
        <input
          className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
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
        <label className="block text-xs text-gray-400 mb-1">
          Allowed Contexts (comma-separated, empty = any)
        </label>
        <input
          className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
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
          <label className="block text-xs text-gray-400 mb-1">Rate limit / min (0 = unlimited)</label>
          <input
            type="number"
            min={0}
            className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
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
            <span className="text-sm text-gray-300">Enabled</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={policy.require_approval}
              onChange={(e) => onChange({ ...policy, require_approval: e.target.checked })}
              className="accent-blue-500"
            />
            <span className="text-sm text-gray-300">Require approval</span>
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
      <div className="bg-gray-800 rounded-xl border border-gray-700 w-full max-w-lg shadow-xl">
        <div className="p-5 border-b border-gray-700 flex items-center gap-3">
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
              <label className="block text-xs text-gray-400 mb-1">Name *</label>
              <input
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value.toUpperCase().replace(/\s/g, "_") })}
                placeholder="OPENAI_API_KEY"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Type</label>
              <select
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
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
            <label className="block text-xs text-gray-400 mb-1">Value *</label>
            <div className="relative">
              <input
                type={showValue ? "text" : "password"}
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white pr-10"
                value={form.value}
                onChange={(e) => setForm({ ...form, value: e.target.value })}
                placeholder="sk-..."
              />
              <button
                type="button"
                className="absolute right-2 top-1.5 text-gray-400 hover:text-white"
                onClick={() => setShowValue(!showValue)}
              >
                {showValue ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Description</label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="OpenAI API key for LLM gateway"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-2 flex items-center gap-2">
              <Shield className="w-3.5 h-3.5" /> Access Policy
            </label>
            <PolicyEditor policy={policy} onChange={setPolicy} />
          </div>
        </div>
        <div className="p-5 border-t border-gray-700 flex justify-end gap-3">
          <button
            className="px-4 py-2 text-sm text-gray-300 hover:text-white"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50"
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

export default function CredentialsPage() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/credentials");
      if (!res.ok) throw new Error(await res.text());
      setCredentials(await res.json());
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <Lock className="w-6 h-6 text-blue-400" />
            Credentials Manager
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Centralised secret store. Agents see only named references like{" "}
            <code className="bg-gray-800 px-1.5 py-0.5 rounded text-blue-300 text-xs">
              &lt;SECRET_NAME&gt;
            </code>
            ; real values are resolved only through approved execution paths.
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
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm"
          >
            <Plus className="w-4 h-4" />
            New Secret
          </button>
        </div>
      </div>

      {/* Info banner */}
      <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4 flex gap-3">
        <Shield className="w-5 h-5 text-blue-400 mt-0.5 shrink-0" />
        <div className="text-sm text-blue-300">
          <strong>Security model:</strong> Real secret values are never logged or exposed in the UI.
          Each resolve attempt is audited. Agents and LLMs receive only placeholder references
          (e.g.{" "}
          <code className="bg-blue-900/30 px-1 rounded">{"<OPENAI_API_KEY>"}</code>). The credentials
          manager resolves values only inside approved execution contexts.
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      {/* Table */}
      <div className="bg-gray-800/50 border border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-700 text-gray-400">
              <th className="text-left px-4 py-3 font-medium">Name</th>
              <th className="text-left px-4 py-3 font-medium">Type</th>
              <th className="text-left px-4 py-3 font-medium">Description</th>
              <th className="text-left px-4 py-3 font-medium">Placeholder</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-left px-4 py-3 font-medium">Uses</th>
              <th className="text-left px-4 py-3 font-medium">Last Used</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                  Loading…
                </td>
              </tr>
            ) : credentials.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                  No credentials stored yet. Click{" "}
                  <strong className="text-white">New Secret</strong> to add one.
                </td>
              </tr>
            ) : (
              credentials.map((c) => (
                <tr key={c.id} className="border-b border-gray-700/50 hover:bg-gray-800/50">
                  <td className="px-4 py-3 font-mono text-white font-medium">{c.name}</td>
                  <td className="px-4 py-3">
                    <Badge type={c.secret_type} />
                  </td>
                  <td className="px-4 py-3 text-gray-300 max-w-xs truncate">{c.description || "—"}</td>
                  <td className="px-4 py-3">
                    <code className="bg-gray-900 px-2 py-0.5 rounded text-blue-300 text-xs">
                      {c.placeholder}
                    </code>
                  </td>
                  <td className="px-4 py-3">
                    {c.policy.enabled ? (
                      <span className="flex items-center gap-1 text-green-400 text-xs">
                        <CheckCircle className="w-3.5 h-3.5" /> Active
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-red-400 text-xs">
                        <AlertTriangle className="w-3.5 h-3.5" /> Disabled
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-400 font-mono">{c.usage_count}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs">
                    {c.last_used_at
                      ? new Date(c.last_used_at).toLocaleString()
                      : "Never"}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => deleteCredential(c.name)}
                      disabled={deleting === c.name}
                      className="p-1.5 text-gray-500 hover:text-red-400 transition-colors disabled:opacity-40"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))
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
