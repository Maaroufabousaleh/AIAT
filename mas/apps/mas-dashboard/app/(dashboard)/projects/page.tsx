"use client";

import { useState, useEffect, FormEvent } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import { WORKFLOW_STATES, STATE_COLORS, type WorkflowState } from "@/lib/constants";
import { formatDistanceToNow } from "date-fns";
import { Plus, RefreshCw } from "lucide-react";

interface Project {
  id: string;
  name: string;
  description?: string;
  state: WorkflowState;
  created_at: string;
  updated_at: string;
}

interface Flow {
  id: string;
  name: string;
  version: number;
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("");
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [selectedFlowId, setSelectedFlowId] = useState<string>("");
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const [projRes, flowRes] = await Promise.all([
        fetch("/api/projects"),
        fetch("/api/flows?is_active=true"),
      ]);
      if (projRes.ok) {
        const projData = await projRes.json();
        setProjects(Array.isArray(projData) ? projData : projData.projects ?? []);
      }
      if (flowRes.ok) {
        const flowData = await flowRes.json();
        setFlows(Array.isArray(flowData) ? flowData : []);
      }
    } catch {
      setProjects([]);
      setFlows([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, description: newDesc }),
      });
      if (res.ok) {
        const project = await res.json();
        
        if (selectedFlowId) {
          try {
            await fetch("/api/flows/instances", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ flow_id: selectedFlowId, project_id: project.id }),
            });
          } catch {
            console.error("Failed to attach flow to project");
          }
        }
        
        setShowCreate(false);
        setNewName("");
        setNewDesc("");
        setSelectedFlowId("");
        await load();
      } else {
        const d = await res.json();
        setError(d.error ?? "Failed to create project");
      }
    } finally {
      setCreating(false);
    }
  }

  const filtered = filter
    ? projects.filter((p) => p.state === filter)
    : projects;

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Projects</h1>
          <p className="text-sm text-gray-500 mt-0.5">{projects.length} total</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            disabled={loading}
            className="p-2 rounded-lg border border-gray-700 text-gray-400 hover:text-gray-100
                       hover:border-gray-500 transition-colors"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500
                       text-white text-sm font-medium rounded-lg transition-colors"
          >
            <Plus size={14} />
            New Project
          </button>
        </div>
      </div>

      {/* Filter */}
      <div className="flex flex-wrap gap-1.5">
        <button
          onClick={() => setFilter("")}
          className={clsx(
            "px-2.5 py-1 rounded-full text-xs font-medium transition-colors",
            filter === "" ? "bg-gray-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
          )}
        >
          All
        </button>
        {WORKFLOW_STATES.filter((s) => projects.some((p) => p.state === s)).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={clsx(
              "px-2.5 py-1 rounded-full text-xs font-medium transition-colors text-white",
              filter === s ? STATE_COLORS[s] : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            )}
          >
            {s.replace(/_/g, " ")}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500 text-sm">Loading...</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center text-gray-500 text-sm">No projects found</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">State</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider hidden sm:table-cell">Created</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider hidden md:table-cell">Updated</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {filtered.map((p) => (
                <tr key={p.id} className="hover:bg-gray-800/50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-100">{p.name}</div>
                    {p.description && (
                      <div className="text-xs text-gray-500 mt-0.5 truncate max-w-xs">{p.description}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={clsx(
                      "inline-flex px-2 py-0.5 rounded-full text-xs font-medium text-white",
                      STATE_COLORS[p.state] ?? "bg-gray-600"
                    )}>
                      {p.state?.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs hidden sm:table-cell">
                    {formatDistanceToNow(new Date(p.created_at), { addSuffix: true })}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs hidden md:table-cell">
                    {formatDistanceToNow(new Date(p.updated_at), { addSuffix: true })}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/projects/${p.id}`}
                      className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                    >
                      View →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-semibold text-white mb-4">New Project</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1.5">Name</label>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  required
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                             text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="my-project"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1.5">Description</label>
                <textarea
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  rows={3}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                             text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                  placeholder="What should the agents build?"
                />
              </div>
              {flows.length > 0 && (
                <div>
                  <label className="block text-sm text-gray-400 mb-1.5">Attach Flow (optional)</label>
                  <select
                    value={selectedFlowId}
                    onChange={(e) => setSelectedFlowId(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                               text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">None — use default workflow</option>
                    {flows.map((flow) => (
                      <option key={flow.id} value={flow.id}>
                        {flow.name} (v{flow.version})
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-gray-500 mt-1">
                    The selected flow will replace the default 18-state workflow for this project.
                  </p>
                </div>
              )}
              {error && <p className="text-sm text-red-400">{error}</p>}
              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="flex-1 px-3 py-2 border border-gray-700 rounded-lg text-sm text-gray-400
                             hover:text-gray-100 hover:border-gray-500 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="flex-1 px-3 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900
                             text-white text-sm rounded-lg transition-colors"
                >
                  {creating ? "Creating..." : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
