'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Search,
  Copy,
  Check,
  Maximize2,
  Minimize2,
  X,
} from 'lucide-react';
import clsx from 'clsx';
import { PageHeader } from '@/components/ui/PageHeader';
import { KpiCard } from '@/components/ui/KpiCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorBanner } from '@/components/ui/ErrorBanner';

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
  deprecated_alias_of?: string;
  input_schema?: ToolSchema;
  circuit_breaker?: CircuitBreakerState;
}

interface ToolsResponse {
  tools: Tool[];
  groups?: string[];
  health?: { tools_registered?: number };
}

const CB_BADGE: Record<string, { label: string; cls: string }> = {
  CLOSED: { label: 'CLOSED', cls: 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/30' },
  OPEN: { label: 'OPEN', cls: 'bg-rose-500/10 text-rose-300 border border-rose-500/30' },
  HALF_OPEN: { label: 'HALF-OPEN', cls: 'bg-amber-500/10 text-amber-300 border border-amber-500/30' },
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

/**
 * Highlight occurrences of `query` inside `text` with a <mark> tag. Used to
 * draw the eye to the matches inside tool names and descriptions while the
 * user types in the search field. Case-insensitive, safe against regex
 * metacharacters.
 */
function highlightMatches(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text;
  const escaped = query.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`(${escaped})`, 'ig');
  const parts = text.split(re);
  return parts.map((part, idx) =>
    idx % 2 === 1 ? (
      <mark
        key={idx}
        className="bg-blue-500/20 text-blue-200 rounded px-0.5"
      >
        {part}
      </mark>
    ) : (
      <React.Fragment key={idx}>{part}</React.Fragment>
    )
  );
}

export default function ToolsPage() {
  const [data, setData] = useState<ToolsResponse | null>(null);
  const dataRef = useRef<ToolsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [copiedTool, setCopiedTool] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchTools = useCallback(async () => {
    try {
      const res = await fetch('/api/tools', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: ToolsResponse = await res.json();
      dataRef.current = json;
      setData(json);
      setError(null);
      setStale(false);
      // Default: expand all groups on first load
      const groups = Object.keys(groupTools((json.tools ?? []).filter((t) => !t.deprecated_alias_of)));
      setExpandedGroups(prev => {
        if (prev.size === 0) return new Set(groups);
        return prev;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch tools');
      setStale(dataRef.current !== null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTools();
    intervalRef.current = setInterval(fetchTools, 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
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

  /** Expand or collapse every group in one go. */
  const setAllGroups = (open: boolean) => {
    if (!data) return;
    const allGroups = Object.keys(groupTools((data.tools ?? []).filter((t) => !t.deprecated_alias_of)));
    setExpandedGroups(open ? new Set(allGroups) : new Set());
  };

  const allExpanded =
    !!data &&
    expandedGroups.size ===
      Object.keys(groupTools((data.tools ?? []).filter((t) => !t.deprecated_alias_of))).length &&
      expandedGroups.size > 0;

  const requestRefresh = () => {
    if (dataRef.current === null) setLoading(true);
    void fetchTools();
  };

  /** Copy a tool's fully qualified name to the clipboard with brief feedback. */
  const copyToolName = async (name: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(name);
      } else {
        // Fallback for non-secure contexts
        const ta = document.createElement('textarea');
        ta.value = name;
        ta.setAttribute('readonly', '');
        ta.style.position = 'absolute';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopiedTool(name);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopiedTool(null), 1500);
    } catch {
      // Silently ignore — clipboard may be blocked by permissions.
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-live="polite">
        <RefreshCw className="w-6 h-6 animate-spin text-blue-400" />
      </div>
    );
  }

  const tools = data?.tools ?? [];
  const visibleTools = tools.filter((t) => !t.deprecated_alias_of);
  const registeredToolCount = data?.health?.tools_registered ?? visibleTools.length;
  const aliasCount = tools.length - visibleTools.length;
  const filteredTools = search.trim()
    ? visibleTools.filter(t =>
        t.name.toLowerCase().includes(search.toLowerCase()) ||
        t.description?.toLowerCase().includes(search.toLowerCase())
      )
    : visibleTools;

  const grouped = groupTools(filteredTools);
  const groupNames = Object.keys(grouped).sort();
  const hasSearch = search.trim().length > 0;
  const noMatches = groupNames.length === 0;

  // Stats
  const openCount = visibleTools.filter(t => t.circuit_breaker?.state === 'OPEN').length;
  const halfOpenCount = visibleTools.filter(t => t.circuit_breaker?.state === 'HALF_OPEN').length;

  return (
    <div className="dashboard-page">
      <PageHeader
        icon="wrench"
        title="Tools"
        description={
          <>
            <span className="text-slate-300 font-medium">{registeredToolCount}</span> registered tools
            <span className="mx-1.5 text-slate-600">·</span>
            <span className="text-slate-300 font-medium">{groupNames.length}</span> groups
            <span className="mx-1.5 text-slate-600">·</span>
            <span className="text-slate-300 font-medium">{aliasCount}</span> hidden aliases
            <span className="mx-1.5 text-slate-600">·</span>
            <span className="text-slate-500">auto-refresh 30s</span>
          </>
        }
        actions={
          <>
            <button
              type="button"
              onClick={requestRefresh}
              disabled={loading}
              aria-label="Refresh tools"
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => setAllGroups(!allExpanded)}
              disabled={visibleTools.length === 0}
              aria-label={allExpanded ? 'Collapse all groups' : 'Expand all groups'}
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {allExpanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
              {allExpanded ? 'Collapse all' : 'Expand all'}
            </button>
          </>
        }
      />

      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <KpiCard
          label="Registered tools"
          value={visibleTools.length}
          icon="wrench"
          tone="info"
          hint={aliasCount > 0 ? `${aliasCount} deprecated alias${aliasCount === 1 ? '' : 'es'} hidden` : 'No aliases'}
        />
        <KpiCard
          label="Circuit breakers open"
          value={openCount}
          icon="zap"
          tone={openCount > 0 ? 'negative' : 'neutral'}
          hint={openCount > 0 ? 'Tool is refusing calls' : 'All healthy'}
        />
        <KpiCard
          label="Half-open breakers"
          value={halfOpenCount}
          icon="zap"
          tone={halfOpenCount > 0 ? 'warning' : 'neutral'}
          hint={halfOpenCount > 0 ? 'Probing for recovery' : 'None probing'}
        />
      </div>

      {error && (
        <ErrorBanner tone="warning" title={stale ? "Showing last known tool catalogue" : "Could not load tools"} action={
          <button
            type="button"
            onClick={requestRefresh}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-900/40 hover:bg-amber-800/60 text-amber-200 text-xs font-medium transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            Retry
          </button>
        }>
          {stale ? `${error}. The latest tools refresh failed; retained catalogue data remains visible.` : error}
        </ErrorBanner>
      )}

      {/* Search */}
      <div className="dashboard-toolbar relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search tools by name or description..."
          aria-label="Search tools"
          className="w-full pl-9 pr-9 py-2 bg-transparent text-sm text-white placeholder-slate-500 focus:outline-none"
        />
        {hasSearch && (
          <button
            type="button"
            onClick={() => setSearch('')}
            aria-label="Clear search"
            className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 text-slate-500 hover:text-slate-300 rounded transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Groups */}
      <div className="space-y-3">
        {groupNames.map(group => {
          const groupTools = grouped[group];
          const groupOpen = groupTools.filter(t => t.circuit_breaker?.state === 'OPEN').length;
          const isExpanded = expandedGroups.has(group);
          const headerId = `group-header-${group.replace(/\s+/g, '-')}`;
          const panelId = `group-panel-${group.replace(/\s+/g, '-')}`;

          return (
            <div key={group} className="dashboard-surface overflow-hidden">
              {/* Group header */}
              <button
                id={headerId}
                onClick={() => toggleGroup(group)}
                aria-expanded={isExpanded}
                aria-controls={panelId}
                className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-800/40 active:bg-slate-800/60 transition-colors text-left"
              >
                {isExpanded
                  ? <ChevronDown className="w-4 h-4 text-slate-500 flex-shrink-0" />
                  : <ChevronRight className="w-4 h-4 text-slate-500 flex-shrink-0" />
                }
                <span className="font-semibold text-white flex-1 truncate">{group}</span>
                <span className="text-xs text-slate-500 mr-3 whitespace-nowrap">{groupTools.length} tool{groupTools.length === 1 ? '' : 's'}</span>
                {groupOpen > 0 && (
                  <span className="text-xs px-2 py-0.5 bg-rose-500/10 text-rose-300 border border-rose-500/30 rounded-full whitespace-nowrap">
                    {groupOpen} open
                  </span>
                )}
              </button>

              {/* Tools table */}
              {isExpanded && (
                <div
                  id={panelId}
                  role="region"
                  aria-labelledby={headerId}
                  className="border-t border-slate-800/70 overflow-x-auto"
                >
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-slate-950/40 text-slate-500 text-xs uppercase tracking-wider border-b border-slate-800/70">
                        <th scope="col" className="px-4 py-2 text-left w-8"></th>
                        <th scope="col" className="px-4 py-2 text-left">Tool Name</th>
                        <th scope="col" className="px-4 py-2 text-left">Description</th>
                        <th scope="col" className="px-4 py-2 text-center">Circuit Breaker</th>
                        <th scope="col" className="px-4 py-2 text-center">Failures</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/40">
                      {groupTools.map(tool => {
                        const cb = tool.circuit_breaker;
                        const badge = cb ? (CB_BADGE[cb.state] ?? CB_BADGE.CLOSED) : null;
                        const isToolExpanded = expanded.has(tool.name);
                        const isCopied = copiedTool === tool.name;
                        const rowId = `tool-row-${tool.name.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
                        const descId = `tool-desc-${tool.name.replace(/[^a-zA-Z0-9_-]/g, '_')}`;

                        return (
                          <React.Fragment key={tool.name}>
                            <tr
                              id={rowId}
                              className={clsx(
                                'hover:bg-slate-800/35 active:bg-slate-800/50 transition-colors cursor-pointer',
                                cb?.state === 'OPEN' && 'bg-rose-950/15'
                              )}
                              onClick={() => toggleTool(tool.name)}
                              aria-expanded={isToolExpanded}
                            >
                              <td className="px-4 py-2.5 text-slate-500">
                                {isToolExpanded
                                  ? <ChevronDown className="w-3.5 h-3.5" />
                                  : <ChevronRight className="w-3.5 h-3.5" />
                                }
                              </td>
                              <td className="px-4 py-2.5 font-mono text-blue-300 text-xs whitespace-nowrap">
                                <span className="inline-flex items-center gap-1.5">
                                  <span title={tool.name}>
                                    {hasSearch ? highlightMatches(tool.name, search) : tool.name}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      copyToolName(tool.name);
                                    }}
                                    aria-label={isCopied ? `Copied ${tool.name}` : `Copy ${tool.name} to clipboard`}
                                    title={isCopied ? 'Copied!' : 'Copy tool name'}
                                    className={clsx(
                                      'inline-flex items-center justify-center w-6 h-6 rounded transition-colors',
                                      'text-slate-500 hover:text-slate-200 hover:bg-slate-800/70',
                                      'focus-visible:ring-1 focus-visible:ring-blue-400/70',
                                      isCopied && 'text-emerald-400 hover:text-emerald-300'
                                    )}
                                  >
                                    {isCopied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                                  </button>
                                </span>
                              </td>
                              <td
                                className="px-4 py-2.5 text-slate-400 text-xs max-w-xs truncate"
                                title={tool.description}
                                aria-describedby={descId}
                              >
                                {tool.description ? (
                                  <span id={descId}>
                                    {hasSearch ? highlightMatches(tool.description, search) : tool.description}
                                  </span>
                                ) : (
                                  <span className="text-slate-600">—</span>
                                )}
                              </td>
                              <td className="px-4 py-2.5 text-center">
                                {badge ? (
                                  <span className={clsx('inline-block px-2 py-0.5 rounded-full text-xs font-medium', badge.cls)}>
                                    {badge.label}
                                  </span>
                                ) : (
                                  <span className="text-slate-600 text-xs" aria-label="No circuit breaker configured">—</span>
                                )}
                              </td>
                              <td className="px-4 py-2.5 text-center text-xs">
                                {cb?.failure_count != null ? (
                                  <span className={clsx(
                                    cb.failure_count > 0 ? 'text-amber-400' : 'text-slate-500'
                                  )}>
                                    {cb.failure_count}
                                  </span>
                                ) : (
                                  <span className="text-slate-600" aria-label="No failure data">—</span>
                                )}
                              </td>
                            </tr>
                            {isToolExpanded && (
                              <tr key={`${tool.name}-expand`} className="bg-slate-950/40">
                                <td colSpan={5} className="px-4 py-3">
                                  <div className="space-y-2">
                                    {tool.description && (
                                      <p className="text-xs text-slate-300 leading-relaxed">
                                        <span className="text-slate-500 font-semibold uppercase tracking-wider text-xxs">Description </span>
                                        {tool.description}
                                      </p>
                                    )}
                                    {cb?.last_failure_time && (
                                      <p className="text-xs text-slate-400">
                                        Last failure: <span className="font-mono text-slate-300">{cb.last_failure_time}</span>
                                      </p>
                                    )}
                                    {tool.input_schema && (
                                      <div>
                                        <p className="text-xs text-slate-500 mb-1.5 font-semibold uppercase tracking-wider text-xxs">Input Schema</p>
                                        <div className="rounded-lg overflow-auto max-h-48 bg-slate-950/60 border border-slate-800/70">
                                          <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-all p-3">
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
                </div>
              )}
            </div>
          );
        })}

        {noMatches && !error && (
          <EmptyState
            icon={hasSearch ? "inbox" : "package"}
            tone={hasSearch ? "neutral" : "muted"}
            title={hasSearch ? 'No tools match your search' : 'No tools registered yet'}
            description={
              hasSearch
                ? `No tools match "${search}". Try a different keyword or clear the search.`
                : 'Tools registered with the agent runtime will appear here once they connect to the control plane.'
            }
            action={
              hasSearch ? (
                <button
                  type="button"
                  onClick={() => setSearch('')}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                  Clear search
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => { setLoading(true); fetchTools(); }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition-colors"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Re-check
                </button>
              )
            }
          />
        )}
      </div>
    </div>
  );
}
