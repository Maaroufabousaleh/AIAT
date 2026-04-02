"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { clsx } from "clsx";
import { WORKFLOW_STATES, STATE_COLORS, TERMINAL_STATES, type WorkflowState } from "@/lib/constants";
import { formatDistanceToNow, format } from "date-fns";
import { ArrowLeft, RefreshCw, CheckCircle, XCircle, RotateCcw, Archive } from "lucide-react";

interface StateHistoryEntry {
  state: WorkflowState;
  entered_at: string;
  transitioned_by?: string;
}

interface Decision {
  id: string;
  decision_type: string;
  prompt: string;
  context?: unknown;
  created_at: string;
}

interface Project {
  id: string;
  name: string;
  description?: string;
  state: WorkflowState;
  created_at: string;
  updated_at: string;
}

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [history, setHistory] = useState<StateHistoryEntry[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [allowedTransitions, setAllowedTransitions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [expandedDecision, setExpandedDecision] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [proj, hist, dec, trans] = await Promise.allSettled([
        fetch(`/api/projects/${id}`).then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`/api/projects/${id}/state-history`).then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch(`/api/projects/${id}/decisions`).then((r) => r.ok ? r.json() : []).catch(() => []),
        fetch(`/api/projects/${id}/transition`).then((r) => r.ok ? r.json() : []).catch(() => []),
      ]);
      if (proj.status === "fulfilled" && proj.value) setProject(proj.value);
      if (hist.status === "fulfilled") setHistory(Array.isArray(hist.value) ? hist.value : hist.value?.history ?? []);
      if (dec.status === "fulfilled") setDecisions(Array.isArray(dec.value) ? dec.value : dec.value?.decisions ?? []);
      if (trans.status === "fulfilled") setAllowedTransitions(Array.isArray(trans.value) ? trans.value : trans.value?.transitions ?? []);
    } catch {
      // leave existing state intact
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh for active projects
  useEffect(() => {
    if (!project || TERMINAL_STATES.includes(project.state)) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [project, load]);

  async function handleDecision(decisionId: string, approved: boolean) {
    setActionLoading(decisionId);
    try {
      await fetch(`/api/projects/${id}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision_id: decisionId, approved }),
      });
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleAction(action: "retry" | "archive") {
    setActionLoading(action);
    try {
      await fetch(`/api/projects/${id}/${action}`, { method: "POST" });
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  async function handleTransition(event: string) {
    setActionLoading(event);
    try {
      await fetch(`/api/projects/${id}/transition`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event }),
      });
      await load();
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center h-full">
        <div className="text-gray-500">Loading...</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="p-6">
        <div className="text-red-400">Project not found</div>
        <Link href="/projects" className="text-blue-400 text-sm mt-2 inline-block">← Back to projects</Link>
      </div>
    );
  }

  const isFailed = project.state === "FAILED";
  const isTerminal = TERMINAL_STATES.includes(project.state);

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start gap-4">
        <Link
          href="/projects"
          className="mt-1 p-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
        >
          <ArrowLeft size={16} />
        </Link>
        <div className="flex-1">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold text-white">{project.name}</h1>
            <span className={clsx(
              "px-2 py-0.5 rounded-full text-xs font-medium text-white",
              STATE_COLORS[project.state] ?? "bg-gray-600"
            )}>
              {project.state?.replace(/_/g, " ")}
            </span>
          </div>
          {project.description && (
            <p className="text-sm text-gray-500 mt-1">{project.description}</p>
          )}
          <p className="text-xs text-gray-600 mt-1">
            ID: {project.id} · Created {formatDistanceToNow(new Date(project.created_at), { addSuffix: true })}
          </p>
        </div>
        <button onClick={load} className="p-2 text-gray-500 hover:text-gray-300">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Actions row */}
      <div className="flex flex-wrap gap-2">
        {allowedTransitions.map((event) => (
          <button
            key={event}
            onClick={() => handleTransition(event)}
            disabled={!!actionLoading}
            className="px-3 py-1.5 text-xs font-medium bg-blue-600/20 text-blue-400
                       border border-blue-700 hover:bg-blue-600/40 rounded-lg transition-colors
                       disabled:opacity-50"
          >
            {actionLoading === event ? "..." : event.replace(/_/g, " ")}
          </button>
        ))}
        {isFailed && (
          <>
            <button
              onClick={() => handleAction("retry")}
              disabled={!!actionLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium
                         bg-amber-600/20 text-amber-400 border border-amber-700
                         hover:bg-amber-600/40 rounded-lg transition-colors disabled:opacity-50"
            >
              <RotateCcw size={12} />
              {actionLoading === "retry" ? "..." : "Retry"}
            </button>
            <button
              onClick={() => handleAction("archive")}
              disabled={!!actionLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium
                         bg-gray-600/20 text-gray-400 border border-gray-700
                         hover:bg-gray-600/40 rounded-lg transition-colors disabled:opacity-50"
            >
              <Archive size={12} />
              {actionLoading === "archive" ? "..." : "Archive"}
            </button>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* State History Timeline */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">State History</h2>
          <div className="space-y-0">
            {WORKFLOW_STATES.map((state, i) => {
              const entry = history.find((h) => h.state === state);
              const isCurrent = project.state === state;
              const isPast = entry !== undefined;
              return (
                <div key={state} className="flex gap-3 group">
                  <div className="flex flex-col items-center">
                    <div className={clsx(
                      "w-3 h-3 rounded-full flex-shrink-0 mt-0.5",
                      isCurrent ? STATE_COLORS[state] :
                      isPast ? "bg-gray-600" : "bg-gray-800 border border-gray-700"
                    )} />
                    {i < WORKFLOW_STATES.length - 1 && (
                      <div className={clsx("w-px flex-1 my-0.5", isPast ? "bg-gray-700" : "bg-gray-800")} />
                    )}
                  </div>
                  <div className="pb-3">
                    <div className={clsx(
                      "text-xs font-medium",
                      isCurrent ? "text-white" : isPast ? "text-gray-400" : "text-gray-700"
                    )}>
                      {state.replace(/_/g, " ")}
                    </div>
                    {entry && (
                      <div className="text-xxs text-gray-600">
                        {format(new Date(entry.entered_at), "MMM d HH:mm:ss")}
                        {entry.transitioned_by && ` · ${entry.transitioned_by}`}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Pending Decisions */}
        <div className="space-y-4">
          {decisions.length > 0 && (
            <div className="bg-amber-950/40 border border-amber-800 rounded-xl p-4">
              <h2 className="text-sm font-medium text-amber-300 mb-3">
                Pending Decisions ({decisions.length})
              </h2>
              <div className="space-y-3">
                {decisions.map((d) => (
                  <div key={d.id} className="bg-gray-900 rounded-lg p-3 border border-gray-800">
                    <div className="text-xs font-medium text-gray-200 mb-1">{d.decision_type}</div>
                    <p className="text-xs text-gray-400 mb-2">{d.prompt}</p>
                    {Boolean(d.context) && (
                      <button
                        onClick={() => setExpandedDecision(expandedDecision === d.id ? null : d.id)}
                        className="text-xxs text-blue-400 hover:text-blue-300 mb-2"
                      >
                        {expandedDecision === d.id ? "Hide context" : "Show context"}
                      </button>
                    )}
                    {expandedDecision === d.id && (
                      <pre className="text-xxs text-gray-500 bg-gray-950 rounded p-2 overflow-x-auto mb-2">
                        {JSON.stringify(d.context, null, 2)}
                      </pre>
                    )}
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleDecision(d.id, true)}
                        disabled={actionLoading === d.id}
                        className="flex items-center gap-1 px-2 py-1 bg-green-600/20 text-green-400
                                   border border-green-800 text-xs rounded hover:bg-green-600/40
                                   transition-colors disabled:opacity-50"
                      >
                        <CheckCircle size={11} />
                        {actionLoading === d.id ? "..." : "Approve"}
                      </button>
                      <button
                        onClick={() => handleDecision(d.id, false)}
                        disabled={actionLoading === d.id}
                        className="flex items-center gap-1 px-2 py-1 bg-red-600/20 text-red-400
                                   border border-red-800 text-xs rounded hover:bg-red-600/40
                                   transition-colors disabled:opacity-50"
                      >
                        <XCircle size={11} />
                        Reject
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Stream links */}
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
            <h2 className="text-sm font-medium text-gray-300 mb-3">Monitor</h2>
            <div className="space-y-2">
              <Link
                href="/ceo"
                className="flex items-center gap-2 text-xs text-blue-400 hover:text-blue-300"
              >
                → CEO Live Feed
              </Link>
              <Link
                href="/streams"
                className="flex items-center gap-2 text-xs text-blue-400 hover:text-blue-300"
              >
                → All Agent Streams
              </Link>
              <Link
                href="/logs"
                className="flex items-center gap-2 text-xs text-blue-400 hover:text-blue-300"
              >
                → Container Logs
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
