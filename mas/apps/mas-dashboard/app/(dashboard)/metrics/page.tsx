"use client";

import { useState, useEffect, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
} from "recharts";
import { format } from "date-fns";
import { RefreshCw } from "lucide-react";

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
  const res = await fetch(url);
  if (!res.ok) return [];
  const { results } = await res.json();
  return results ?? [];
}

async function fetchInstant(query: string): Promise<PrometheusResult[]> {
  const url = `/api/metrics?query=${encodeURIComponent(query)}`;
  const res = await fetch(url);
  if (!res.ok) return [];
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
      if (!map.has(ts)) map.set(ts, { time: format(ts * 1000, "HH:mm") });
      map.get(ts)![label] = parseFloat(val);
    }
  }
  return Array.from(map.values()).sort((a, b) => a.time > b.time ? 1 : -1);
}

function seriesKeys(results: PrometheusResult[], labelKey = "model"): string[] {
  return Array.from(new Set(results.map((r) => r.metric[labelKey] ?? r.metric.team ?? r.metric.tool_name ?? "value")));
}

const COLORS = ["#60a5fa", "#34d399", "#f59e0b", "#f87171", "#a78bfa", "#fb7185", "#38bdf8", "#4ade80", "#fbbf24", "#e879f9"];

function MetricCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-3">{title}</h3>
      {children}
    </div>
  );
}

function EmptyChart() {
  return (
    <div className="h-36 flex items-center justify-center text-gray-600 text-xs">
      No data available
    </div>
  );
}

export default function MetricsPage() {
  const [mounted, setMounted] = useState(false);
  const [rangeIdx, setRangeIdx] = useState(1);
  const [loading, setLoading] = useState(false);
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
        setLlmKeys(seriesKeys(llm.value));
        setLlmData(toTimeSeries(llm.value));
      }
      if (tools.status === "fulfilled") {
        setToolKeys(seriesKeys(tools.value, "tool_name"));
        setToolData(toTimeSeries(tools.value, "tool_name"));
      }
      if (msgs.status === "fulfilled") {
        setMsgKeys(seriesKeys(msgs.value, "direction"));
        setMsgData(toTimeSeries(msgs.value, "direction"));
      }
      if (dlq.status === "fulfilled") {
        setDlqData(dlq.value.map((r) => ({
          stream: r.metric.stream ?? "unknown",
          depth: parseFloat(r.value?.[1] ?? "0"),
        })));
      }
      if (budget.status === "fulfilled") {
        setBudgetData(budget.value
          .map((r) => ({ agent: r.metric.agent_id ?? "?", exhaustions: parseFloat(r.value?.[1] ?? "0") }))
          .filter((d) => d.exhaustions > 0)
        );
      }
      if (circuits.status === "fulfilled") {
        setCircuitData(circuits.value.map((r) => ({
          tool: r.metric.tool_name ?? "?",
          state: parseFloat(r.value?.[1] ?? "0"),
        })).filter((d) => d.state > 0));
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

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Metrics</h1>
          <p className="text-sm text-gray-500 mt-0.5">Prometheus · refreshes every 30s</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-gray-700 overflow-hidden">
            {RANGES.map((r, i) => (
              <button
                key={r.label}
                onClick={() => setRangeIdx(i)}
                className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                  i === rangeIdx
                    ? "bg-blue-600 text-white"
                    : "text-gray-400 hover:text-gray-100 hover:bg-gray-800"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-2 rounded-lg border border-gray-700 text-gray-400 hover:text-gray-100"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {!mounted ? (
          <div className="col-span-2 h-48 flex items-center justify-center text-gray-600 text-sm">
            Loading charts...
          </div>
        ) : (<>
        <MetricCard title="LLM Calls/min by Model">
          {llmData.length ? (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={llmData} margin={{ top: 0, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="time" tick={{ fill: "#6b7280", fontSize: 10 }} />
                <YAxis tick={{ fill: "#6b7280", fontSize: 10 }} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {llmKeys.map((k, i) => (
                  <Line key={k} type="monotone" dataKey={k} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={1.5} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </MetricCard>

        <MetricCard title="Tool Calls/min (top 10)">
          {toolData.length ? (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={toolData} margin={{ top: 0, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="time" tick={{ fill: "#6b7280", fontSize: 10 }} />
                <YAxis tick={{ fill: "#6b7280", fontSize: 10 }} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {toolKeys.map((k, i) => (
                  <Line key={k} type="monotone" dataKey={k} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={1.5} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </MetricCard>

        <MetricCard title="Messages/min by Direction">
          {msgData.length ? (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={msgData} margin={{ top: 0, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="time" tick={{ fill: "#6b7280", fontSize: 10 }} />
                <YAxis tick={{ fill: "#6b7280", fontSize: 10 }} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {msgKeys.map((k, i) => (
                  <Bar key={k} dataKey={k} fill={COLORS[i % COLORS.length]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </MetricCard>

        <MetricCard title="DLQ Depth by Stream">
          {dlqData.length ? (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={dlqData} margin={{ top: 0, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="stream" tick={{ fill: "#6b7280", fontSize: 9 }} />
                <YAxis tick={{ fill: "#6b7280", fontSize: 10 }} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }} />
                <Bar dataKey="depth" fill="#f87171" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-36 flex items-center justify-center text-green-400 text-xs">
              All DLQ depths are 0
            </div>
          )}
        </MetricCard>

        {budgetData.length > 0 && (
          <MetricCard title="Budget Exhaustions">
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={budgetData} margin={{ top: 0, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="agent" tick={{ fill: "#6b7280", fontSize: 9 }} />
                <YAxis tick={{ fill: "#6b7280", fontSize: 10 }} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }} />
                <Bar dataKey="exhaustions" fill="#f59e0b" />
              </BarChart>
            </ResponsiveContainer>
          </MetricCard>
        )}

        {circuitData.length > 0 && (
          <MetricCard title="Open Circuit Breakers">
            <div className="space-y-1.5">
              {circuitData.map((d) => (
                <div key={d.tool} className="flex items-center gap-2 text-xs">
                  <span className="w-2 h-2 rounded-full bg-red-500 flex-shrink-0" />
                  <span className="text-gray-300 flex-1 truncate">{d.tool}</span>
                  <span className="text-red-400 font-mono">{d.state}</span>
                </div>
              ))}
            </div>
          </MetricCard>
        )}
        </>)}
      </div>
    </div>
  );
}
