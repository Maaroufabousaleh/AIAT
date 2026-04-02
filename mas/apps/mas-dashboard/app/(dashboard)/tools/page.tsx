'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { RefreshCw, ChevronDown, ChevronRight, AlertTriangle, Wrench, Zap } from 'lucide-react';
import clsx from 'clsx';

interface CircuitBreakerState {
  state: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
  failure_count: number;
  last_failure_time?: string | null;
}

interface ToolSchema {
  type: string;
  properties?: Record<string, unknown>;
  required?: string[];
}

interface Tool {
  name: string;
  description: string;
  group?: string;
  input_schema?: ToolSchema;
  circuit_breaker?: CircuitBreakerState;
}

interface ToolsResponse {
  tools: Tool[];
  groups?: string[];
}

const CB_BADGE: Record<string, { label: string; cls: string }> = {
  CLOSED: { label: 'CLOSED', cls: 'bg-green-900/50 text-green-300 border border-green-700' },
  OPEN: { label: 'OPEN', cls: 'bg-red-900/50 text-red-300 border border-red-700' },
  HALF_OPEN: { label: 'HALF-OPEN', cls: 'bg-yellow-900/50 text-yellow-300 border border-yellow-700' },
};

function groupTools(tools: Tool[]): Record<string, Tool[]> {
  const groups: Record<string, Tool[]> = {};
  for (const tool of tools) {
    const group = tool.group ?? inferGroup(tool.name);
    if (!groups[group]) groups[group] = [];
    groups[group].push(tool);
  }
  return groups;
}

function inferGroup(name: string): string {
  if (name.startsWith('file_') || name.includes('read_file') || name.includes('write_file')) return 'File System';
  if (name.startsWith('web_') || name.includes('http') || name.includes('fetch')) return 'Web / HTTP';
  if (name.startsWith('git_') || name.includes('github')) return 'Version Control';
  if (name.startsWith('code_') || name.includes('python') || name.includes('shell') || name.includes('exec')) return 'Code Execution';
  if (name.startsWith('db_') || name.includes('database') || name.includes('sql')) return 'Database';
  if (name.includes('search') || name.includes('browse') || name.includes('scrape')) return 'Search / Browse';
  return 'General';
}

export default function ToolsPage() {
  const [data, setData] = useState<ToolsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchTools = useCallback(async () => {
    try {
      const res = await fetch('/api/tools');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: ToolsResponse = await res.json();
      setData(json);
      setError(null);
      // Default: expand all groups
      const groups = Object.keys(groupTools(json.tools ?? []));
      setExpandedGroups(prev => {
        if (prev.size === 0) return new Set(groups);
        return prev;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch tools');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTools();
    intervalRef.current = setInterval(fetchTools, 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchTools]);

  const toggleTool = (name: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  };

  const toggleGroup = (group: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      next.has(group) ? next.delete(group) : next.add(group);
      return next;
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
      </div>
    );
  }

  const tools = data?.tools ?? [];
  const filteredTools = search.trim()
    ? tools.filter(t =>
        t.name.toLowerCase().includes(search.toLowerCase()) ||
        t.description?.toLowerCase().includes(search.toLowerCase())
      )
    : tools;

  const grouped = groupTools(filteredTools);
  const groupNames = Object.keys(grouped).sort();

  // Stats
  const openCount = tools.filter(t => t.circuit_breaker?.state === 'OPEN').length;
  const halfOpenCount = tools.filter(t => t.circuit_breaker?.state === 'HALF_OPEN').length;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Tools</h1>
          <p className="text-sm text-zinc-400 mt-1">
            {tools.length} tools across {groupNames.length} groups — auto-refresh every 30s
          </p>
        </div>
        <button
          onClick={() => { setLoading(true); fetchTools(); }}
          className="flex items-center gap-2 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 flex items-center gap-3">
          <Wrench className="w-8 h-8 text-indigo-400" />
          <div>
            <div className="text-2xl font-bold text-white">{tools.length}</div>
            <div className="text-xs text-zinc-400">Total tools</div>
          </div>
        </div>
        <div className="bg-zinc-900 border border-red-900/40 rounded-xl p-4 flex items-center gap-3">
          <Zap className="w-8 h-8 text-red-400" />
          <div>
            <div className="text-2xl font-bold text-red-400">{openCount}</div>
            <div className="text-xs text-zinc-400">Circuit breakers open</div>
          </div>
        </div>
        <div className="bg-zinc-900 border border-yellow-900/40 rounded-xl p-4 flex items-center gap-3">
          <Zap className="w-8 h-8 text-yellow-400" />
          <div>
            <div className="text-2xl font-bold text-yellow-400">{halfOpenCount}</div>
            <div className="text-xs text-zinc-400">Half-open breakers</div>
          </div>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-3 p-4 bg-red-900/30 border border-red-700 rounded-lg text-red-300">
          <AlertTriangle className="w-5 h-5 flex-shrink-0" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      {/* Search */}
      <input
        type="text"
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="Search tools..."
        className="w-full px-4 py-2.5 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
      />

      {/* Groups */}
      <div className="space-y-3">
        {groupNames.map(group => {
          const groupTools = grouped[group];
          const groupOpen = groupTools.filter(t => t.circuit_breaker?.state === 'OPEN').length;
          const isExpanded = expandedGroups.has(group);

          return (
            <div key={group} className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
              {/* Group header */}
              <button
                onClick={() => toggleGroup(group)}
                className="w-full flex items-center gap-3 px-4 py-3 hover:bg-zinc-800/40 transition-colors text-left"
              >
                {isExpanded
                  ? <ChevronDown className="w-4 h-4 text-zinc-500 flex-shrink-0" />
                  : <ChevronRight className="w-4 h-4 text-zinc-500 flex-shrink-0" />
                }
                <span className="font-semibold text-white flex-1">{group}</span>
                <span className="text-xs text-zinc-500 mr-3">{groupTools.length} tools</span>
                {groupOpen > 0 && (
                  <span className="text-xs px-2 py-0.5 bg-red-900/50 text-red-300 border border-red-700 rounded-full">
                    {groupOpen} open
                  </span>
                )}
              </button>

              {/* Tools table */}
              {isExpanded && (
                <table className="w-full text-sm border-t border-zinc-800">
                  <thead>
                    <tr className="text-zinc-500 text-xs uppercase tracking-wider border-b border-zinc-800">
                      <th className="px-4 py-2 text-left w-8"></th>
                      <th className="px-4 py-2 text-left">Tool Name</th>
                      <th className="px-4 py-2 text-left">Description</th>
                      <th className="px-4 py-2 text-center">Circuit Breaker</th>
                      <th className="px-4 py-2 text-center">Failures</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/40">
                    {groupTools.map(tool => {
                      const cb = tool.circuit_breaker;
                      const badge = cb ? (CB_BADGE[cb.state] ?? CB_BADGE.CLOSED) : null;
                      const isToolExpanded = expanded.has(tool.name);

                      return (
                        <React.Fragment key={tool.name}>
                          <tr
                            className={clsx(
                              'hover:bg-zinc-800/20 transition-colors cursor-pointer',
                              cb?.state === 'OPEN' && 'bg-red-950/10'
                            )}
                            onClick={() => toggleTool(tool.name)}
                          >
                            <td className="px-4 py-2.5 text-zinc-500">
                              {isToolExpanded
                                ? <ChevronDown className="w-3.5 h-3.5" />
                                : <ChevronRight className="w-3.5 h-3.5" />
                              }
                            </td>
                            <td className="px-4 py-2.5 font-mono text-indigo-300 text-xs whitespace-nowrap">
                              {tool.name}
                            </td>
                            <td className="px-4 py-2.5 text-zinc-400 text-xs max-w-xs truncate" title={tool.description}>
                              {tool.description ?? '—'}
                            </td>
                            <td className="px-4 py-2.5 text-center">
                              {badge ? (
                                <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', badge.cls)}>
                                  {badge.label}
                                </span>
                              ) : (
                                <span className="text-zinc-600 text-xs">—</span>
                              )}
                            </td>
                            <td className="px-4 py-2.5 text-center text-xs">
                              {cb?.failure_count != null ? (
                                <span className={clsx(
                                  cb.failure_count > 0 ? 'text-yellow-400' : 'text-zinc-500'
                                )}>
                                  {cb.failure_count}
                                </span>
                              ) : (
                                <span className="text-zinc-600">—</span>
                              )}
                            </td>
                          </tr>
                          {isToolExpanded && (
                            <tr key={`${tool.name}-expand`} className="bg-zinc-950">
                              <td colSpan={5} className="px-4 py-3">
                                <div className="space-y-2">
                                  {cb?.last_failure_time && (
                                    <p className="text-xs text-zinc-400">
                                      Last failure: <span className="font-mono text-zinc-300">{cb.last_failure_time}</span>
                                    </p>
                                  )}
                                  {tool.input_schema && (
                                    <div>
                                      <p className="text-xs text-zinc-500 mb-1.5">Input Schema</p>
                                      <div className="rounded-lg overflow-auto max-h-48">
                                        <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap break-all">
                                          {JSON.stringify(tool.input_schema, null, 2)}
                                        </pre>
                                      </div>
                                    </div>
                                  )}
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}

        {groupNames.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center h-32 text-zinc-500 gap-2">
            <Wrench className="w-8 h-8" />
            <p className="text-sm">No tools match your search.</p>
          </div>
        )}
      </div>
    </div>
  );
}
