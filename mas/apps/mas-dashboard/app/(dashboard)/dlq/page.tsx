'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { RefreshCw, Play, ChevronDown, ChevronRight, AlertTriangle, CheckSquare, Square } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import clsx from 'clsx';

interface DeadLetter {
  id: string;
  stream: string;
  message_type: string;
  failure_reason: string;
  retry_count: number;
  created_at: string;
  envelope: Record<string, unknown>;
}

interface DLQResponse {
  dead_letters: DeadLetter[];
  total: number;
}

export default function DLQPage() {
  const [data, setData] = useState<DLQResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [replaying, setReplaying] = useState<Set<string>>(new Set());
  const [replayResults, setReplayResults] = useState<Record<string, 'ok' | 'err'>>({});
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchDLQ = useCallback(async () => {
    try {
      const res = await fetch('/api/dlq');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: DLQResponse = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch DLQ');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDLQ();
    intervalRef.current = setInterval(fetchDLQ, 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchDLQ]);

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (!data) return;
    if (selected.size === data.dead_letters.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(data.dead_letters.map(d => d.id)));
    }
  };

  const replayOne = async (id: string) => {
    setReplaying(prev => new Set(prev).add(id));
    try {
      const res = await fetch(`/api/dlq/${id}/replay`, { method: 'POST' });
      setReplayResults(prev => ({ ...prev, [id]: res.ok ? 'ok' : 'err' }));
      if (res.ok) {
        setTimeout(() => fetchDLQ(), 1000);
      }
    } catch {
      setReplayResults(prev => ({ ...prev, [id]: 'err' }));
    } finally {
      setReplaying(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const replaySelected = async () => {
    const ids = Array.from(selected);
    await Promise.all(ids.map(id => replayOne(id)));
    setSelected(new Set());
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
      </div>
    );
  }

  const letters = data?.dead_letters ?? [];
  const allSelected = letters.length > 0 && selected.size === letters.length;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dead Letter Queue</h1>
          <p className="text-sm text-zinc-400 mt-1">
            {data?.total ?? 0} message{data?.total !== 1 ? 's' : ''} in queue — auto-refresh every 30s
          </p>
        </div>
        <div className="flex items-center gap-3">
          {selected.size > 0 && (
            <button
              onClick={replaySelected}
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
            >
              <Play className="w-4 h-4" />
              Replay {selected.size} selected
            </button>
          )}
          <button
            onClick={() => { setLoading(true); fetchDLQ(); }}
            className="flex items-center gap-2 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-3 p-4 bg-red-900/30 border border-red-700 rounded-lg text-red-300">
          <AlertTriangle className="w-5 h-5 flex-shrink-0" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      {letters.length === 0 && !error ? (
        <div className="flex flex-col items-center justify-center h-64 text-zinc-500 gap-3">
          <CheckSquare className="w-12 h-12 text-green-500/50" />
          <p className="text-lg font-medium text-green-400">Queue is empty</p>
          <p className="text-sm">No dead letters — all messages processed successfully.</p>
        </div>
      ) : (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-400 text-xs uppercase tracking-wider">
                <th className="px-4 py-3 text-left w-10">
                  <button onClick={toggleSelectAll} className="text-zinc-400 hover:text-white">
                    {allSelected
                      ? <CheckSquare className="w-4 h-4 text-indigo-400" />
                      : <Square className="w-4 h-4" />
                    }
                  </button>
                </th>
                <th className="px-4 py-3 text-left w-8"></th>
                <th className="px-4 py-3 text-left">Stream</th>
                <th className="px-4 py-3 text-left">Message Type</th>
                <th className="px-4 py-3 text-left">Failure Reason</th>
                <th className="px-4 py-3 text-center">Retries</th>
                <th className="px-4 py-3 text-left">Age</th>
                <th className="px-4 py-3 text-center">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {letters.map(letter => (
                <React.Fragment key={letter.id}>
                  <tr
                    className="hover:bg-zinc-800/30 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <button onClick={() => toggleSelect(letter.id)} className="text-zinc-400 hover:text-white">
                        {selected.has(letter.id)
                          ? <CheckSquare className="w-4 h-4 text-indigo-400" />
                          : <Square className="w-4 h-4" />
                        }
                      </button>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => toggleExpand(letter.id)}
                        className="text-zinc-500 hover:text-zinc-300"
                      >
                        {expanded.has(letter.id)
                          ? <ChevronDown className="w-4 h-4" />
                          : <ChevronRight className="w-4 h-4" />
                        }
                      </button>
                    </td>
                    <td className="px-4 py-3 font-mono text-indigo-300 text-xs">{letter.stream}</td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-0.5 bg-zinc-800 rounded text-zinc-300 font-mono text-xs">
                        {letter.message_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-red-300 text-xs max-w-xs truncate" title={letter.failure_reason}>
                      {letter.failure_reason}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={clsx(
                        'px-2 py-0.5 rounded-full text-xs font-medium',
                        letter.retry_count >= 3 ? 'bg-red-900/50 text-red-300' :
                        letter.retry_count >= 1 ? 'bg-yellow-900/50 text-yellow-300' :
                        'bg-zinc-800 text-zinc-400'
                      )}>
                        {letter.retry_count}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-zinc-400 text-xs whitespace-nowrap">
                      {formatDistanceToNow(new Date(letter.created_at), { addSuffix: true })}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {replayResults[letter.id] === 'ok' ? (
                        <span className="text-xs text-green-400 font-medium">Replayed</span>
                      ) : replayResults[letter.id] === 'err' ? (
                        <span className="text-xs text-red-400 font-medium">Failed</span>
                      ) : (
                        <button
                          onClick={() => replayOne(letter.id)}
                          disabled={replaying.has(letter.id)}
                          className="flex items-center gap-1.5 mx-auto px-3 py-1 bg-zinc-800 hover:bg-indigo-700 text-zinc-300 hover:text-white rounded text-xs transition-colors disabled:opacity-50"
                        >
                          {replaying.has(letter.id)
                            ? <RefreshCw className="w-3 h-3 animate-spin" />
                            : <Play className="w-3 h-3" />
                          }
                          Replay
                        </button>
                      )}
                    </td>
                  </tr>
                  {expanded.has(letter.id) && (
                    <tr key={`${letter.id}-expand`} className="bg-zinc-950">
                      <td colSpan={8} className="px-4 py-3">
                        <div className="rounded-lg overflow-auto max-h-64">
                          <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap break-all">
                            {JSON.stringify(letter.envelope, null, 2)}
                          </pre>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
