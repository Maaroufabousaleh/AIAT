"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { clsx } from "clsx";
import {
  BarChart, Bar, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
  type TooltipProps,
} from "recharts";
import { formatInTz, formatLocaleInTz } from "@/lib/datetime";
import { RefreshCw } from "lucide-react";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

const RANGES = [
  { label: "15m", minutes: 15, step: 30 },
  { label: "1h",  minutes: 60, step: 60 },
  { label: "6h",  minutes: 360, step: 300 },
  { label: "24h", minutes: 1440, step: 900 },
] as const;

interface DataPoint { time: string; [key: string]: number | string }

interface PrometheusResult {
  metric: Record<string, string>;
  values?: [number, string][];
  value?: [number, string];
}

async function fetchMetric(
  query: string,
  start: number,
  end: number,
  step: number
): Promise<PrometheusResult[]> {
  const url = `/api/metrics?type=range&query=${encodeURIComponent(query)}&start=${start}&end=${end}&step=${step}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const { results } = await res.json();
  return results ?? [];
}

async function fetchInstant(query: string): Promise<PrometheusResult[]> {
  const url = `/api/metrics?query=${encodeURIComponent(query)}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const { results } = await res.json();
  return results ?? [];
}

function toTimeSeries(results: PrometheusResult[], labelKey = "model"): DataPoint[] {
  if (!results.length || !results[0].values) return [];
  // merge all series by timestamp
  const map = new Map<number, DataPoint>();
  for (const r of results) {
    const label = r.metric[labelKey] ?? r.metric.team ?? r.metric.tool_name ?? "value";
    for (const [ts, val] of r.values ?? []) {
      if (!map.has(ts)) map.set(ts, { time: formatInTz(ts * 1000, "HH:mm") });
      map.get(ts)![label] = parseFloat(val);
    }
  }
  return Array.from(map.values()).sort((a, b) => a.time > b.time ? 1 : -1);
}

function seriesKeys(results: PrometheusResult[], labelKey = "model"): string[] {
  return Array.from(new Set(results.map((r) => r.metric[labelKey] ?? r.metric.team ?? r.metric.tool_name ?? "value")));
}

// Chart palette — slate-friendly with high contrast on dark surfaces.
const COLORS = ["#60a5fa", "#34d399", "#f59e0b", "#f87171", "#a78bfa", "#fb7185", "#38bdf8", "#4ade80", "#fbbf24", "#e879f9"];

/** Unique gradient id factory — keeps SVG <defs> ids deterministic per series. */
function gradientId(idx: number, key: string): string {
  return `metrics-grad-${idx}-${key.replace(/[^a-z0-9]/gi, "_")}`;
}

interface MetricCardProps {
  title: string;
  description?: string;
  children: React.ReactNode;
}

/** Small wrapper around `dashboard-surface` for chart tiles. */
function MetricCard({ title, description, children }: MetricCardProps) {
  return (
    <section
      className="dashboard-surface p-4 transition-colors hover:border-slate-700"
      aria-label={title}
    >
      <header className="flex items-baseline justify-between gap-2 mb-3">
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
        {description && (
          <p className="text-xxs uppercase tracking-wider text-slate-500">{description}</p>
        )}
      </header>
      {children}
    </section>
  );
}

/** Custom recharts tooltip — styled to match the dark surface. */
function ChartTooltip({ active, payload, label, unit }: TooltipProps<number | string, string> & { unit?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div
      role="tooltip"
      className="rounded-lg border border-slate-700 bg-slate-900/95 px-3 py-2 shadow-lg shadow-black/40 backdrop-blur-sm"
    >
      <div className="text-xxs font-semibold uppercase tracking-wider text-slate-500">{label}</div>
      <ul className="mt-1 space-y-0.5">
        {payload.map((p) => (
          <li key={String(p.dataKey)} className="flex items-center gap-2 text-xs">
            <span
              className="inline-block w-2 h-2 rounded-sm"
              style={{ background: p.color }}
              aria-hidden="true"
            />
            <span className="text-slate-300 truncate max-w-[10rem]">{p.name}</span>
            <span className="ml-auto font-mono text-slate-100">
              {typeof p.value === "number" ? p.value.toFixed(2) : p.value}
              {unit ? <span className="ml-1 text-slate-500">{unit}</span> : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function MetricsPage() {
  const [mounted, setMounted] = useState(false);
  const [rangeIdx, setRangeIdx] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const hasLoadedRef = useRef(false);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [llmData, setLlmData] = useState<DataPoint[]>([]);
  const [llmKeys, setLlmKeys] = useState<string[]>([]);
  const [toolData, setToolData] = useState<DataPoint[]>([]);
  const [toolKeys, setToolKeys] = useState<string[]>([]);
  const [msgData, setMsgData] = useState<DataPoint[]>([]);
  const [msgKeys, setMsgKeys] = useState<string[]>([]);
  const [dlqData, setDlqData] = useState<{ stream: string; depth: number }[]>([]);
  const [budgetData, setBudgetData] = useState<{ agent: string; exhaustions: number }[]>([]);
  const [circuitData, setCircuitData] = useState<{ tool: string; state: number }[]>([]);

  const range = RANGES[rangeIdx];

  const load = useCallback(async () => {
    setLoading(true);
    const now = Math.floor(Date.now() / 1000);
    const start = now - range.minutes * 60;
    const step = range.step;

    try {
      const [llm, tools, msgs, dlq, budget, circuits] = await Promise.allSettled([
        fetchMetric(`sum by (model) (rate(mas_llm_calls_total[5m]))`, start, now, step),
        fetchMetric(`topk(10, sum by (tool_name) (rate(mas_tool_calls_total[5m])))`, start, now, step),
        fetchMetric(`sum by (direction) (rate(mas_messages_total[5m]))`, start, now, step),
        fetchInstant(`mas_dlq_depth`),
        fetchInstant(`increase(mas_budget_exhausted_total[${range.minutes}m])`),
        fetchInstant(`mas_tool_circuit_state`),
      ]);

      if (llm.status === "fulfilled") {
        hasLoadedRef.current = true;
        setLlmKeys(seriesKeys(llm.value));
        setLlmData(toTimeSeries(llm.value));
      }
      if (tools.status === "fulfilled") {
        hasLoadedRef.current = true;
        setToolKeys(seriesKeys(tools.value, "tool_name"));
        setToolData(toTimeSeries(tools.value, "tool_name"));
      }
      if (msgs.status === "fulfilled") {
        hasLoadedRef.current = true;
        setMsgKeys(seriesKeys(msgs.value, "direction"));
        setMsgData(toTimeSeries(msgs.value, "direction"));
      }
      if (dlq.status === "fulfilled") {
        hasLoadedRef.current = true;
        setDlqData(dlq.value.map((r) => ({
          stream: r.metric.stream ?? "unknown",
          depth: parseFloat(r.value?.[1] ?? "0"),
        })));
      }
      if (budget.status === "fulfilled") {
        hasLoadedRef.current = true;
        setBudgetData(budget.value
          .map((r) => ({ agent: r.metric.agent_id ?? "?", exhaustions: parseFloat(r.value?.[1] ?? "0") }))
          .filter((d) => d.exhaustions > 0)
        );
      }
      if (circuits.status === "fulfilled") {
        hasLoadedRef.current = true;
        setCircuitData(circuits.value.map((r) => ({
          tool: r.metric.tool_name ?? "?",
          state: parseFloat(r.value?.[1] ?? "0"),
        })).filter((d) => d.state > 0));
      }
      const metricResults: Array<[string, PromiseSettledResult<PrometheusResult[]>]> = [
        ["LLM calls", llm],
        ["tool calls", tools],
        ["messages", msgs],
        ["DLQ depth", dlq],
        ["budget alerts", budget],
        ["circuit breakers", circuits],
      ];
      const failures = metricResults
        .filter(([, result]) => result.status === "rejected")
        .map(([name]) => name);
      if (failures.length > 0) {
        setError(`Metrics refresh failed for ${failures.join(", ")}.`);
        setStale(hasLoadedRef.current);
      } else {
        setError(null);
        setStale(false);
        setLastUpdated(Date.now());
      }
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => { setMounted(true); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const requestRefresh = () => {
    if (loading) return;
    void load();
  };

  const totalDlqDepth = dlqData.reduce((sum, d) => sum + d.depth, 0);

  // "No data" = at least one query returned an empty payload for the chosen range.
  const noData =
    mounted &&
    llmData.length === 0 &&
    toolData.length === 0 &&
    msgData.length === 0 &&
    dlqData.length === 0;

  return (
    <main className="dashboard-page" aria-label="Metrics dashboard">
      <PageHeader
        icon="bar-chart"
        title="Metrics"
        description={
          <>
            Prometheus · refreshes every 30s
            {lastUpdated && (
              <span className="ml-2 text-slate-500">
                · last successful refresh{" "}
                <time
                  dateTime={new Date(lastUpdated).toISOString()}
                  className="text-slate-400 font-mono"
                >
                  {formatLocaleInTz(lastUpdated, {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </time>
              </span>
            )}
          </>
        }
        actions={
          <>
            {/* Time-range segmented control */}
            <div
              role="group"
              aria-label="Time range"
              className="inline-flex rounded-lg border border-slate-800 overflow-hidden bg-slate-950/45"
            >
              {RANGES.map((r, i) => (
                <button
                  key={r.label}
                  type="button"
                  onClick={() => setRangeIdx(i)}
                  aria-pressed={i === rangeIdx}
                  className={clsx(
                    "min-h-11 min-w-11 px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none",
                    i === rangeIdx
                      ? "bg-blue-500/20 text-blue-100 border-blue-400/40 shadow-inner shadow-blue-950/40"
                      : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/70 border-transparent",
                    i > 0 && "border-l border-slate-800"
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={requestRefresh}
              disabled={loading}
              aria-label="Refresh metrics"
              title="Refresh"
              className="min-h-11 min-w-11 p-2 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-100 hover:bg-slate-800/70 hover:border-slate-500 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>
          </>
        }
      />

      {error && (
        <ErrorBanner
          tone={stale ? "warning" : "error"}
          title={stale ? "Showing last known metrics" : "Metrics unavailable"}
          action={(
            <button type="button" onClick={requestRefresh} disabled={loading} className="min-h-11 px-3 rounded border border-current text-xs font-medium hover:bg-white/10 disabled:opacity-50">
              Retry
            </button>
          )}
        >
          {stale ? `${error} Retained series remain visible while the metrics source recovers.` : error}
        </ErrorBanner>
      )}

      <section className="grid grid-cols-2 lg:grid-cols-4 gap-4" aria-label="Metric summaries">
        <KpiCard
          label="Series loaded"
          value={llmKeys.length + toolKeys.length + msgKeys.length}
          hint={`${range.label} selected`}
          icon="activity"
          tone="info"
        />
        <KpiCard
          label="DLQ depth"
          value={totalDlqDepth}
          hint={totalDlqDepth === 0 ? "all streams clear" : "needs replay review"}
          icon="inbox"
          tone={totalDlqDepth > 0 ? "negative" : "positive"}
        />
        <KpiCard
          label="Budget alerts"
          value={budgetData.length}
          hint="agents over budget"
          icon="wallet"
          tone={budgetData.length > 0 ? "warning" : "positive"}
        />
        <KpiCard
          label="Circuit breakers"
          value={circuitData.length}
          hint="open or half-open"
          icon="zap"
          tone={circuitData.length > 0 ? "negative" : "positive"}
        />
      </section>

      {noData && (
        <EmptyState
          icon="alert"
          tone="neutral"
          title="No metrics available"
          description={`The Prometheus query returned no data for the ${range.label} window. The metrics endpoint may be down, or the operator has not reported any series yet.`}
          action={
            <button
              type="button"
              onClick={requestRefresh}
              disabled={loading}
              className="min-h-11 inline-flex items-center gap-2 px-3 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium shadow-sm shadow-blue-500/20 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              Reconnect
            </button>
          }
        />
      )}
      <section className="grid grid-cols-1 xl:grid-cols-2 gap-4" aria-label="Metric charts">
        {!mounted ? (
          <div className="col-span-2 h-48 flex items-center justify-center text-slate-500 text-sm">
            Loading charts...
          </div>
        ) : (<>
        <MetricCard title="LLM Calls/min by Model" description="calls · 5m rate">
          {llmData.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={llmData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <defs>
                  {llmKeys.map((k, i) => (
                    <linearGradient
                      key={k}
                      id={gradientId(0, k)}
                      x1="0" y1="0" x2="0" y2="1"
                    >
                      <stop offset="0%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.45} />
                      <stop offset="100%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis
                  dataKey="time"
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  label={{ value: "time", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 10 }}
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  width={48}
                  label={{ value: "calls/min", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 10 }}
                />
                <Tooltip content={<ChartTooltip unit="cpm" />} cursor={{ stroke: "#475569", strokeWidth: 1, strokeDasharray: "3 3" }} />
                <Legend
                  verticalAlign="top"
                  align="right"
                  height={24}
                  iconType="circle"
                  iconSize={8}
                  wrapperStyle={{ fontSize: 10, color: "#cbd5e1" }}
                />
                {llmKeys.map((k, i) => (
                  <Area
                    key={k}
                    type="monotone"
                    dataKey={k}
                    stroke={COLORS[i % COLORS.length]}
                    fill={`url(#${gradientId(0, k)})`}
                    strokeWidth={1.5}
                    isAnimationActive
                    animationDuration={600}
                    animationEasing="ease-out"
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-44 flex items-center justify-center text-slate-600 text-xs">
              No model data in this window
            </div>
          )}
        </MetricCard>

        <MetricCard title="Tool Calls/min (top 10)" description="calls · 5m rate">
          {toolData.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={toolData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <defs>
                  {toolKeys.map((k, i) => (
                    <linearGradient
                      key={k}
                      id={gradientId(1, k)}
                      x1="0" y1="0" x2="0" y2="1"
                    >
                      <stop offset="0%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.45} />
                      <stop offset="100%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis
                  dataKey="time"
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  label={{ value: "time", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 10 }}
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  width={48}
                  label={{ value: "calls/min", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 10 }}
                />
                <Tooltip content={<ChartTooltip unit="cpm" />} cursor={{ stroke: "#475569", strokeWidth: 1, strokeDasharray: "3 3" }} />
                <Legend
                  verticalAlign="top"
                  align="right"
                  height={24}
                  iconType="circle"
                  iconSize={8}
                  wrapperStyle={{ fontSize: 10, color: "#cbd5e1" }}
                />
                {toolKeys.map((k, i) => (
                  <Area
                    key={k}
                    type="monotone"
                    dataKey={k}
                    stroke={COLORS[i % COLORS.length]}
                    fill={`url(#${gradientId(1, k)})`}
                    strokeWidth={1.5}
                    isAnimationActive
                    animationDuration={600}
                    animationEasing="ease-out"
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-44 flex items-center justify-center text-slate-600 text-xs">
              No tool data in this window
            </div>
          )}
        </MetricCard>

        <MetricCard title="Messages/min by Direction" description="msgs · 5m rate">
          {msgData.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={msgData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis
                  dataKey="time"
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  label={{ value: "time", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 10 }}
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  width={48}
                  label={{ value: "msgs/min", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 10 }}
                />
                <Tooltip content={<ChartTooltip unit="mpm" />} cursor={{ fill: "#1e293b" }} />
                <Legend
                  verticalAlign="top"
                  align="right"
                  height={24}
                  iconType="circle"
                  iconSize={8}
                  wrapperStyle={{ fontSize: 10, color: "#cbd5e1" }}
                />
                {msgKeys.map((k, i) => (
                  <Bar
                    key={k}
                    dataKey={k}
                    fill={COLORS[i % COLORS.length]}
                    radius={[3, 3, 0, 0]}
                    isAnimationActive
                    animationDuration={600}
                    animationEasing="ease-out"
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-44 flex items-center justify-center text-slate-600 text-xs">
              No message data in this window
            </div>
          )}
        </MetricCard>

        <MetricCard title="DLQ Depth by Stream" description="backlog">
          {dlqData.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={dlqData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <defs>
                  <linearGradient id="dlq-bar-grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#f87171" stopOpacity={0.95} />
                    <stop offset="100%" stopColor="#f87171" stopOpacity={0.45} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis
                  dataKey="stream"
                  tick={{ fill: "#64748b", fontSize: 9 }}
                  stroke="#334155"
                  label={{ value: "stream", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 10 }}
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  width={48}
                  label={{ value: "depth", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 10 }}
                />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: "#1e293b" }} />
                <Bar
                  dataKey="depth"
                  fill="url(#dlq-bar-grad)"
                  radius={[3, 3, 0, 0]}
                  isAnimationActive
                  animationDuration={600}
                  animationEasing="ease-out"
                />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-44 flex items-center justify-center text-emerald-400 text-xs">
              All DLQ depths are 0
            </div>
          )}
        </MetricCard>

        {budgetData.length > 0 && (
          <MetricCard title="Budget Exhaustions" description="agents over budget">
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={budgetData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <defs>
                  <linearGradient id="budget-bar-grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.95} />
                    <stop offset="100%" stopColor="#f59e0b" stopOpacity={0.45} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis
                  dataKey="agent"
                  tick={{ fill: "#64748b", fontSize: 9 }}
                  stroke="#334155"
                  label={{ value: "agent", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 10 }}
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 10 }}
                  stroke="#334155"
                  width={48}
                  label={{ value: "count", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 10 }}
                />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: "#1e293b" }} />
                <Bar
                  dataKey="exhaustions"
                  fill="url(#budget-bar-grad)"
                  radius={[3, 3, 0, 0]}
                  isAnimationActive
                  animationDuration={600}
                  animationEasing="ease-out"
                />
              </BarChart>
            </ResponsiveContainer>
          </MetricCard>
        )}

        {circuitData.length > 0 && (
          <MetricCard title="Open Circuit Breakers" description={`${circuitData.length} tools`}>
            <div className="space-y-1.5">
              {circuitData.map((d) => (
                <div
                  key={d.tool}
                  className="flex items-center gap-2 text-xs px-2 py-1.5 rounded-md bg-slate-950/40 border border-slate-800/70 transition-colors hover:border-rose-500/40 hover:bg-rose-950/15"
                >
                  <span className="w-2 h-2 rounded-full bg-rose-500 flex-shrink-0" aria-hidden="true" />
                  <span className="text-slate-300 flex-1 truncate">{d.tool}</span>
                  <span className="text-rose-400 font-mono">{d.state}</span>
                </div>
              ))}
            </div>
          </MetricCard>
        )}
        </>)}
      </section>
    </main>
  );
}
