"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import { Plus, RefreshCw, PlayCircle, PauseCircle, XCircle, CheckCircle, AlertCircle } from "lucide-react";
import type { Flow, FlowInstanceStatus } from "@/lib/flow-types";

const STATUS_ICONS: Record<FlowInstanceStatus, typeof PlayCircle> = {
  NOT_STARTED: PauseCircle,
  RUNNING: PlayCircle,
  WAITING_APPROVAL: AlertCircle,
  PAUSED: PauseCircle,
  CANCELLED: XCircle,
  COMPLETED: CheckCircle,
  FAILED: XCircle,
};

const STATUS_COLORS: Record<string, string> = {
  NOT_STARTED: "bg-gray-500",
  RUNNING: "bg-blue-500",
  WAITING_APPROVAL: "bg-amber-500",
  PAUSED: "bg-yellow-500",
  CANCELLED: "bg-stone-500",
  COMPLETED: "bg-emerald-500",
  FAILED: "bg-rose-500",
};

export default function FlowsPage() {
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "active" | "inactive">("all");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = filter !== "all" ? `?is_active=${filter === "active"}` : "";
      const res = await fetch(`/api/flows${params}`);
      if (!res.ok) { setFlows([]); return; }
      const data = await res.json();
      setFlows(Array.isArray(data) ? data : []);
    } catch {
      setFlows([]);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Orchestration Flows</h1>
          <p className="text-sm text-gray-500 mt-0.5">{flows.length} flows</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            disabled={loading}
            className="p-2 rounded-lg border border-gray-700 text-gray-400 hover:text-gray-100 hover:border-gray-500"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          </button>
          <Link
            href="/flows/new"
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg"
          >
            <Plus size={14} />
            New Flow
          </Link>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {(["all", "active", "inactive"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={clsx(
              "px-2.5 py-1 rounded-full text-xs font-medium transition-colors",
              filter === f ? "bg-gray-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            )}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500 text-sm">Loading...</div>
        ) : flows.length === 0 ? (
          <div className="p-8 text-center text-gray-500 text-sm">No flows found</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500">Name</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500">Version</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500">Nodes</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 hidden sm:table-cell">Updated</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {flows.map((flow) => {
                const nodeCount = flow.definition_json?.nodes?.length || 0;
                return (
                  <tr key={flow.id} className="hover:bg-gray-800/50">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-100">{flow.name}</div>
                      {flow.description && (
                        <div className="text-xs text-gray-500 mt-0.5 truncate max-w-xs">{flow.description}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-gray-400">v{flow.version}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={clsx(
                        "inline-flex px-2 py-0.5 rounded-full text-xs font-medium text-white",
                        flow.is_active ? "bg-green-600" : "bg-gray-600"
                      )}>
                        {flow.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-400">{nodeCount}</td>
                    <td className="px-4 py-3 text-gray-500 text-xs hidden sm:table-cell">
                      {formatDistanceToNow(new Date(flow.updated_at), { addSuffix: true })}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex gap-2 justify-end">
                        <Link
                          href={`/flows/${flow.id}`}
                          className="text-xs text-blue-400 hover:text-blue-300"
                        >
                          Edit →
                        </Link>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
