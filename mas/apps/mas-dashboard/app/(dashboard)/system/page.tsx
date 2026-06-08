'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import {
  RefreshCw,
  Power,
  PlayCircle,
  Clock,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
  HelpCircle,
  Timer,
} from 'lucide-react';
import clsx from 'clsx';
import { PageHeader } from '@/components/ui/PageHeader';
import { ErrorBanner } from '@/components/ui/ErrorBanner';

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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
    >
      <div className="dashboard-surface-strong p-6 w-full max-w-md shadow-2xl">
        <h3 id="confirm-dialog-title" className="text-lg font-semibold text-white mb-2">{title}</h3>
        <p className="text-sm text-slate-400 mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors"
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

/**
 * Human-friendly uptime formatter. Always shows two units (largest + next) for
 * context, e.g. "2 days 4 hours", "3 hours 12 minutes", "45 seconds". Days and
 * hours are fully spelled out so the value reads at a glance.
 */
function formatUptime(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.floor(seconds));
  const d = Math.floor(wholeSeconds / 86400);
  const h = Math.floor((wholeSeconds % 86400) / 3600);
  const m = Math.floor((wholeSeconds % 3600) / 60);
  const s = wholeSeconds % 60;
  if (d > 0) {
    // Always show days + hours so the value is unambiguous.
    return `${d} ${d === 1 ? 'day' : 'days'} ${h} ${h === 1 ? 'hour' : 'hours'}`;
  }
  if (h > 0) return `${h} ${h === 1 ? 'hour' : 'hours'} ${m} ${m === 1 ? 'minute' : 'minutes'}`;
  if (m > 0) return `${m} ${m === 1 ? 'minute' : 'minutes'} ${s} ${s === 1 ? 'second' : 'seconds'}`;
  return `${s} ${s === 1 ? 'second' : 'seconds'}`;
}

/**
 * Parses a scheduled event value. The orchestrator may return either a cron
 * expression (e.g. "0 22 * * *") or an absolute ISO date string. We try to
 * detect ISO dates and return a Date if possible; otherwise the raw string is
 * returned so the UI can fall back to displaying the cron text.
 */
function parseScheduledValue(value: string | null | undefined): { date: Date | null; raw: string } {
  if (!value) return { date: null, raw: '' };
  // ISO 8601 detection — anything Date.parse can interpret AND contains a '-'
  // or 'T' (so we don't accidentally parse "0 22 * * *" as a number).
  const looksLikeDate = /[-T:Z]/.test(value) && !/^[\d\s*\/,\-]+$/.test(value);
  if (looksLikeDate) {
    const ts = Date.parse(value);
    if (!Number.isNaN(ts)) {
      return { date: new Date(ts), raw: value };
    }
  }
  return { date: null, raw: value };
}

/**
 * Formats the difference between `now` and a target date as a human-readable
 * countdown like "in 2 days 4 hours" or "12 minutes ago".
 */
function formatCountdown(target: Date, now: Date = new Date()): string {
  const diffMs = target.getTime() - now.getTime();
  const abs = Math.abs(diffMs);
  const future = diffMs >= 0;

  const d = Math.floor(abs / 86_400_000);
  const h = Math.floor((abs % 86_400_000) / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const s = Math.floor((abs % 60_000) / 1000);

  const parts: string[] = [];
  if (d > 0) parts.push(`${d} ${d === 1 ? 'day' : 'days'}`);
  if (h > 0) parts.push(`${h} ${h === 1 ? 'hour' : 'hours'}`);
  if (m > 0) parts.push(`${m} ${m === 1 ? 'minute' : 'minutes'}`);
  if (parts.length === 0) parts.push(`${s} ${s === 1 ? 'second' : 'seconds'}`);

  const phrase = parts.slice(0, 2).join(' ');
  return future ? `in ${phrase}` : `${phrase} ago`;
}

const CRON_HELP =
  'Standard 5-field cron expression: minute hour day-of-month month day-of-week. ' +
  'Examples — "0 22 * * *" = 10pm daily, "0 8 * * 1-5" = 8am on weekdays, "*/15 * * * *" = every 15 minutes.';

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

  // Live "now" used to drive the scheduled-event countdown. Ticks every second
  // so the countdown stays fresh even if status polling stalls.
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const iv = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(iv);
  }, []);

  // Parse scheduled events once per status update. Either may resolve to a
  // concrete Date (used for the countdown) or fall back to a cron string.
  const scheduledShutdown = useMemo(
    () => parseScheduledValue(status?.scheduled_shutdown),
    [status?.scheduled_shutdown]
  );
  const scheduledResume = useMemo(
    () => parseScheduledValue(status?.scheduled_resume),
    [status?.scheduled_resume]
  );

  const statusColor = {
    running: 'text-emerald-300 bg-emerald-950/30 border-emerald-800/70',
    shutdown: 'text-rose-300 bg-rose-950/30 border-rose-800/70',
    degraded: 'text-amber-300 bg-amber-950/30 border-amber-800/70',
  }[status?.status ?? ''] ?? 'text-slate-400 bg-slate-900/60 border-slate-800';

  const StatusIcon = status?.status === 'running'
    ? CheckCircle2
    : status?.status === 'degraded'
    ? AlertTriangle
    : XCircle;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-live="polite">
        <RefreshCw className="w-6 h-6 animate-spin text-blue-400" />
        <span className="sr-only">Loading system status</span>
      </div>
    );
  }

  return (
    <div className="dashboard-page">
      <PageHeader
        icon="settings"
        title="System Control"
        description="Monitor and control MAS runtime state"
        actions={
          <button
            onClick={() => { setLoading(true); fetchStatus(); }}
            aria-label="Refresh system status"
            className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-sm border border-slate-700/80 transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        }
      />

      {error && (
        <ErrorBanner tone="warning" title="Could not reach the orchestrator">
          {error}
        </ErrorBanner>
      )}

      {status && (
        <>
          {/* Status Banner */}
          <div
            className={clsx(
              'flex flex-col sm:flex-row sm:items-center gap-4 p-5 border rounded-xl shadow-sm shadow-black/10',
              statusColor
            )}
            role="status"
            aria-live="polite"
          >
            <StatusIcon className="w-8 h-8 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-xl font-bold uppercase tracking-widest">
                {status.status}
              </div>
              {status.paused_reason && (
                <p className="text-sm mt-0.5 opacity-80">{status.paused_reason}</p>
              )}
            </div>
            <div className="text-left sm:text-right text-sm opacity-80 space-y-1">
              {status.uptime_seconds != null && (
                <div>
                  Uptime:{' '}
                  <span className="font-mono" title={`${status.uptime_seconds.toLocaleString()} seconds`}>
                    {formatUptime(status.uptime_seconds)}
                  </span>
                </div>
              )}
              {status.active_projects != null && (
                <div>
                  Active projects: <span className="font-mono">{status.active_projects}</span>
                </div>
              )}
            </div>
          </div>

          {/* Schedule Info — shows pending shutdown/resume plus a live countdown
              when the orchestrator returns an absolute date; falls back to the
              raw cron expression otherwise. */}
          {(status.scheduled_shutdown || status.scheduled_resume) && (
            <div
              className="dashboard-surface p-4 space-y-3"
              role="region"
              aria-label="Scheduled system events"
            >
              <h3 className="text-sm font-medium text-slate-300 flex items-center gap-2">
                <Clock className="w-4 h-4 text-slate-400" />
                Scheduled Events
              </h3>
              {status.scheduled_shutdown && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <div className="flex items-center gap-2 text-amber-300">
                    <Power className="w-4 h-4" />
                    <span>Shutdown scheduled</span>
                  </div>
                  <span className="font-mono text-amber-200 bg-amber-950/30 border border-amber-900/60 rounded px-1.5 py-0.5">
                    {scheduledShutdown.raw}
                  </span>
                  {scheduledShutdown.date && (
                    <span
                      className="inline-flex items-center gap-1.5 text-xs text-amber-200 bg-amber-950/40 border border-amber-900/60 rounded-full px-2 py-0.5"
                      aria-live="polite"
                      title={scheduledShutdown.date.toLocaleString()}
                    >
                      <Timer className="w-3 h-3" />
                      {formatCountdown(scheduledShutdown.date, now)}
                    </span>
                  )}
                </div>
              )}
              {status.scheduled_resume && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <div className="flex items-center gap-2 text-emerald-300">
                    <PlayCircle className="w-4 h-4" />
                    <span>Resume scheduled</span>
                  </div>
                  <span className="font-mono text-emerald-200 bg-emerald-950/30 border border-emerald-900/60 rounded px-1.5 py-0.5">
                    {scheduledResume.raw}
                  </span>
                  {scheduledResume.date && (
                    <span
                      className="inline-flex items-center gap-1.5 text-xs text-emerald-200 bg-emerald-950/40 border border-emerald-900/60 rounded-full px-2 py-0.5"
                      aria-live="polite"
                      title={scheduledResume.date.toLocaleString()}
                    >
                      <Timer className="w-3 h-3" />
                      {formatCountdown(scheduledResume.date, now)}
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Control Buttons */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Shutdown */}
            <div className="dashboard-surface p-5">
              <div className="flex items-start gap-3 mb-4">
                <div className="flex-shrink-0 w-9 h-9 rounded-lg border border-rose-900/60 bg-rose-950/30 flex items-center justify-center">
                  <Power className="w-5 h-5 text-rose-300" />
                </div>
                <div>
                  <h3 className="font-semibold text-white">Shutdown System</h3>
                  <p className="text-xs text-slate-400 mt-1">
                    Gracefully halts all running workflows and stops task processing.
                  </p>
                </div>
              </div>
              {actionError.shutdown && (
                <p className="text-xs text-rose-300 mb-3" role="alert">{actionError.shutdown}</p>
              )}
              <button
                onClick={() => setShowShutdownConfirm(true)}
                disabled={status.status === 'shutdown' || actionState.shutdown === 'loading'}
                aria-label="Shutdown the MAS runtime"
                className={clsx(
                  'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70',
                  actionState.shutdown === 'success'
                    ? 'bg-emerald-700 text-white'
                    : status.status === 'shutdown'
                    ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                    : 'bg-rose-700 hover:bg-rose-600 text-white'
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
            <div className="dashboard-surface p-5">
              <div className="flex items-start gap-3 mb-4">
                <div className="flex-shrink-0 w-9 h-9 rounded-lg border border-emerald-900/60 bg-emerald-950/30 flex items-center justify-center">
                  <PlayCircle className="w-5 h-5 text-emerald-300" />
                </div>
                <div>
                  <h3 className="font-semibold text-white">Resume System</h3>
                  <p className="text-xs text-slate-400 mt-1">
                    Resumes task processing and restarts paused workflows.
                  </p>
                </div>
              </div>
              {actionError.resume && (
                <p className="text-xs text-rose-300 mb-3" role="alert">{actionError.resume}</p>
              )}
              <button
                onClick={() => setShowResumeConfirm(true)}
                disabled={status.status === 'running' || actionState.resume === 'loading'}
                aria-label="Resume the MAS runtime"
                className={clsx(
                  'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70',
                  actionState.resume === 'success'
                    ? 'bg-emerald-700 text-white'
                    : status.status === 'running'
                    ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                    : 'bg-emerald-700 hover:bg-emerald-600 text-white'
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
          <div className="dashboard-surface p-5">
            <div className="flex items-center gap-2 mb-1">
              <Clock className="w-5 h-5 text-blue-400" />
              <h3 className="font-semibold text-white">Schedule (Cron)</h3>
              <span
                className="inline-flex items-center text-slate-500 hover:text-slate-300 transition-colors"
                title={CRON_HELP}
                aria-label={CRON_HELP}
                tabIndex={0}
                role="img"
              >
                <HelpCircle className="w-4 h-4" />
              </span>
            </div>
            <p className="text-xs text-slate-500 mb-4">
              Set cron expressions for automatic shutdown/resume (e.g. <code className="font-mono bg-slate-800 text-slate-200 px-1 rounded">0 22 * * *</code> for 10pm daily). Hover the help icon for syntax details.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
              <div>
                <label htmlFor="cron-shutdown" className="block text-xs text-slate-400 mb-1.5">
                  Shutdown cron
                </label>
                <input
                  id="cron-shutdown"
                  type="text"
                  value={scheduleShutdown}
                  onChange={e => setScheduleShutdown(e.target.value)}
                  placeholder="e.g. 0 22 * * *"
                  title={CRON_HELP}
                  aria-describedby="cron-help"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-white font-mono placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/50 transition-colors"
                />
              </div>
              <div>
                <label htmlFor="cron-resume" className="block text-xs text-slate-400 mb-1.5">
                  Resume cron
                </label>
                <input
                  id="cron-resume"
                  type="text"
                  value={scheduleResume}
                  onChange={e => setScheduleResume(e.target.value)}
                  placeholder="e.g. 0 8 * * *"
                  title={CRON_HELP}
                  aria-describedby="cron-help"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-white font-mono placeholder-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/50 transition-colors"
                />
              </div>
            </div>
            <p id="cron-help" className="sr-only">{CRON_HELP}</p>
            <div className="flex items-center gap-3">
              <button
                onClick={saveSchedule}
                disabled={scheduleSaving || (!scheduleShutdown && !scheduleResume)}
                aria-label="Save cron schedule"
                className={clsx(
                  'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/70',
                  scheduleSaveResult === 'ok'
                    ? 'bg-emerald-700 text-white'
                    : scheduleSaveResult === 'err'
                    ? 'bg-rose-700 text-white'
                    : 'bg-blue-600 hover:bg-blue-500 text-white disabled:bg-slate-700 disabled:text-slate-500'
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
          confirmClass="bg-rose-700 hover:bg-rose-600"
          onConfirm={() => { setShowShutdownConfirm(false); doAction('shutdown'); }}
          onCancel={() => setShowShutdownConfirm(false)}
        />
      )}
      {showResumeConfirm && (
        <ConfirmDialog
          title="Resume System?"
          message="This will restart task processing and resume all paused workflows."
          confirmLabel="Resume"
          confirmClass="bg-emerald-700 hover:bg-emerald-600"
          onConfirm={() => { setShowResumeConfirm(false); doAction('resume'); }}
          onCancel={() => setShowResumeConfirm(false)}
        />
      )}
    </div>
  );
}
