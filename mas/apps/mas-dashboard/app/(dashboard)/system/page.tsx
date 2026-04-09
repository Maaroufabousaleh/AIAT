'use client';

import { useEffect, useState, useCallback } from 'react';
import { RefreshCw, Power, PlayCircle, Clock, AlertTriangle, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import clsx from 'clsx';

interface SystemStatus {
  status: string;           // "running" | "shutdown" | "degraded"
  scheduled_shutdown?: string | null;
  scheduled_resume?: string | null;
  uptime_seconds?: number;
  active_projects?: number;
  paused_reason?: string | null;
}

type ActionState = 'idle' | 'loading' | 'success' | 'error';

function ConfirmDialog({
  title,
  message,
  confirmLabel,
  confirmClass,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  confirmClass: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h3 className="text-lg font-semibold text-white mb-2">{title}</h3>
        <p className="text-sm text-zinc-400 mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-zinc-300 hover:text-white bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={clsx('px-4 py-2 text-sm text-white rounded-lg transition-colors font-medium', confirmClass)}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export default function SystemPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showShutdownConfirm, setShowShutdownConfirm] = useState(false);
  const [showResumeConfirm, setShowResumeConfirm] = useState(false);
  const [actionState, setActionState] = useState<Record<string, ActionState>>({});
  const [actionError, setActionError] = useState<Record<string, string>>({});

  // Schedule form
  const [scheduleShutdown, setScheduleShutdown] = useState('');
  const [scheduleResume, setScheduleResume] = useState('');
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [scheduleSaveResult, setScheduleSaveResult] = useState<'ok' | 'err' | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/system/status');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: SystemStatus = await res.json();
      setStatus(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch system status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const iv = setInterval(fetchStatus, 15_000);
    return () => clearInterval(iv);
  }, [fetchStatus]);

  const doAction = async (action: 'shutdown' | 'resume') => {
    setActionState(prev => ({ ...prev, [action]: 'loading' }));
    setActionError(prev => { const n = { ...prev }; delete n[action]; return n; });
    try {
      const res = await fetch(`/api/system/${action}`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setActionState(prev => ({ ...prev, [action]: 'success' }));
      setTimeout(() => {
        setActionState(prev => ({ ...prev, [action]: 'idle' }));
        fetchStatus();
      }, 2000);
    } catch (e) {
      setActionState(prev => ({ ...prev, [action]: 'error' }));
      setActionError(prev => ({ ...prev, [action]: e instanceof Error ? e.message : 'Action failed' }));
      setTimeout(() => setActionState(prev => ({ ...prev, [action]: 'idle' })), 3000);
    }
  };

  const saveSchedule = async () => {
    setScheduleSaving(true);
    setScheduleSaveResult(null);
    try {
      const body: Record<string, string> = {};
      if (scheduleShutdown) body.shutdown_cron = scheduleShutdown;
      if (scheduleResume) body.resume_cron = scheduleResume;
      const res = await fetch('/api/system/schedule', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setScheduleSaveResult('ok');
      fetchStatus();
    } catch {
      setScheduleSaveResult('err');
    } finally {
      setScheduleSaving(false);
      setTimeout(() => setScheduleSaveResult(null), 3000);
    }
  };

  const statusColor = {
    running: 'text-green-400 bg-green-900/30 border-green-700',
    shutdown: 'text-red-400 bg-red-900/30 border-red-700',
    degraded: 'text-yellow-400 bg-yellow-900/30 border-yellow-700',
  }[status?.status ?? ''] ?? 'text-zinc-400 bg-zinc-800 border-zinc-700';

  const StatusIcon = status?.status === 'running'
    ? CheckCircle2
    : status?.status === 'degraded'
    ? AlertTriangle
    : XCircle;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">System Control</h1>
          <p className="text-sm text-zinc-400 mt-1">Monitor and control MAS runtime state</p>
        </div>
        <button
          onClick={() => { setLoading(true); fetchStatus(); }}
          className="flex items-center gap-2 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-3 p-4 bg-red-900/30 border border-red-700 rounded-lg text-red-300">
          <AlertTriangle className="w-5 h-5 flex-shrink-0" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      {status && (
        <>
          {/* Status Banner */}
          <div className={clsx('flex items-center gap-4 p-5 border rounded-xl', statusColor)}>
            <StatusIcon className="w-8 h-8 flex-shrink-0" />
            <div className="flex-1">
              <div className="text-xl font-bold uppercase tracking-widest">
                {status.status}
              </div>
              {status.paused_reason && (
                <p className="text-sm mt-0.5 opacity-80">{status.paused_reason}</p>
              )}
            </div>
            <div className="text-right text-sm opacity-70 space-y-1">
              {status.uptime_seconds != null && (
                <div>Uptime: <span className="font-mono">{formatUptime(status.uptime_seconds)}</span></div>
              )}
              {status.active_projects != null && (
                <div>Active projects: <span className="font-mono">{status.active_projects}</span></div>
              )}
            </div>
          </div>

          {/* Schedule Info */}
          {(status.scheduled_shutdown || status.scheduled_resume) && (
            <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-xl space-y-2">
              <h3 className="text-sm font-medium text-zinc-300 mb-3">Scheduled Events</h3>
              {status.scheduled_shutdown && (
                <div className="flex items-center gap-2 text-sm text-yellow-300">
                  <Clock className="w-4 h-4" />
                  Shutdown scheduled: <span className="font-mono text-yellow-200">{status.scheduled_shutdown}</span>
                </div>
              )}
              {status.scheduled_resume && (
                <div className="flex items-center gap-2 text-sm text-green-300">
                  <Clock className="w-4 h-4" />
                  Resume scheduled: <span className="font-mono text-green-200">{status.scheduled_resume}</span>
                </div>
              )}
            </div>
          )}

          {/* Control Buttons */}
          <div className="grid grid-cols-2 gap-4">
            {/* Shutdown */}
            <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <div className="flex items-start gap-3 mb-4">
                <Power className="w-5 h-5 text-red-400 mt-0.5" />
                <div>
                  <h3 className="font-semibold text-white">Shutdown System</h3>
                  <p className="text-xs text-zinc-400 mt-1">
                    Gracefully halts all running workflows and stops task processing.
                  </p>
                </div>
              </div>
              {actionError.shutdown && (
                <p className="text-xs text-red-400 mb-3">{actionError.shutdown}</p>
              )}
              <button
                onClick={() => setShowShutdownConfirm(true)}
                disabled={status.status === 'shutdown' || actionState.shutdown === 'loading'}
                className={clsx(
                  'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors',
                  actionState.shutdown === 'success'
                    ? 'bg-green-700 text-white'
                    : status.status === 'shutdown'
                    ? 'bg-zinc-800 text-zinc-600 cursor-not-allowed'
                    : 'bg-red-700 hover:bg-red-600 text-white'
                )}
              >
                {actionState.shutdown === 'loading' ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> Shutting down...</>
                ) : actionState.shutdown === 'success' ? (
                  <><CheckCircle2 className="w-4 h-4" /> Shutdown sent</>
                ) : (
                  <><Power className="w-4 h-4" /> Shutdown</>
                )}
              </button>
            </div>

            {/* Resume */}
            <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <div className="flex items-start gap-3 mb-4">
                <PlayCircle className="w-5 h-5 text-green-400 mt-0.5" />
                <div>
                  <h3 className="font-semibold text-white">Resume System</h3>
                  <p className="text-xs text-zinc-400 mt-1">
                    Resumes task processing and restarts paused workflows.
                  </p>
                </div>
              </div>
              {actionError.resume && (
                <p className="text-xs text-red-400 mb-3">{actionError.resume}</p>
              )}
              <button
                onClick={() => setShowResumeConfirm(true)}
                disabled={status.status === 'running' || actionState.resume === 'loading'}
                className={clsx(
                  'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors',
                  actionState.resume === 'success'
                    ? 'bg-green-700 text-white'
                    : status.status === 'running'
                    ? 'bg-zinc-800 text-zinc-600 cursor-not-allowed'
                    : 'bg-green-700 hover:bg-green-600 text-white'
                )}
              >
                {actionState.resume === 'loading' ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> Resuming...</>
                ) : actionState.resume === 'success' ? (
                  <><CheckCircle2 className="w-4 h-4" /> Resume sent</>
                ) : (
                  <><PlayCircle className="w-4 h-4" /> Resume</>
                )}
              </button>
            </div>
          </div>

          {/* Schedule Form */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
            <div className="flex items-center gap-2 mb-4">
              <Clock className="w-5 h-5 text-indigo-400" />
              <h3 className="font-semibold text-white">Schedule (Cron)</h3>
            </div>
            <p className="text-xs text-zinc-500 mb-4">
              Set cron expressions for automatic shutdown/resume (e.g. <code className="font-mono bg-zinc-800 px-1 rounded">0 22 * * *</code> for 10pm daily).
            </p>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5">Shutdown cron</label>
                <input
                  type="text"
                  value={scheduleShutdown}
                  onChange={e => setScheduleShutdown(e.target.value)}
                  placeholder="e.g. 0 22 * * *"
                  className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white font-mono placeholder-zinc-600 focus:outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5">Resume cron</label>
                <input
                  type="text"
                  value={scheduleResume}
                  onChange={e => setScheduleResume(e.target.value)}
                  placeholder="e.g. 0 8 * * *"
                  className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-white font-mono placeholder-zinc-600 focus:outline-none focus:border-indigo-500"
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={saveSchedule}
                disabled={scheduleSaving || (!scheduleShutdown && !scheduleResume)}
                className={clsx(
                  'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                  scheduleSaveResult === 'ok'
                    ? 'bg-green-700 text-white'
                    : scheduleSaveResult === 'err'
                    ? 'bg-red-700 text-white'
                    : 'bg-indigo-600 hover:bg-indigo-500 text-white disabled:bg-zinc-700 disabled:text-zinc-500'
                )}
              >
                {scheduleSaving
                  ? <><Loader2 className="w-4 h-4 animate-spin" /> Saving...</>
                  : scheduleSaveResult === 'ok'
                  ? <><CheckCircle2 className="w-4 h-4" /> Saved</>
                  : scheduleSaveResult === 'err'
                  ? <><XCircle className="w-4 h-4" /> Failed</>
                  : 'Save Schedule'
                }
              </button>
            </div>
          </div>
        </>
      )}

      {/* Confirm Dialogs */}
      {showShutdownConfirm && (
        <ConfirmDialog
          title="Shutdown System?"
          message="This will gracefully halt all running workflows and stop task processing. Active projects will be paused and can be resumed later."
          confirmLabel="Shutdown"
          confirmClass="bg-red-700 hover:bg-red-600"
          onConfirm={() => { setShowShutdownConfirm(false); doAction('shutdown'); }}
          onCancel={() => setShowShutdownConfirm(false)}
        />
      )}
      {showResumeConfirm && (
        <ConfirmDialog
          title="Resume System?"
          message="This will restart task processing and resume all paused workflows."
          confirmLabel="Resume"
          confirmClass="bg-green-700 hover:bg-green-600"
          onConfirm={() => { setShowResumeConfirm(false); doAction('resume'); }}
          onCancel={() => setShowResumeConfirm(false)}
        />
      )}
    </div>
  );
}
