const BASE = process.env.PROMETHEUS_URL ?? "http://localhost:9090";

export interface PrometheusResult {
  metric: Record<string, string>;
  values?: [number, string][];  // range query
  value?: [number, string];     // instant query
}

export interface PrometheusResponse {
  status: "success" | "error";
  data: {
    resultType: string;
    result: PrometheusResult[];
  };
  error?: string;
}

export async function promQuery(query: string): Promise<PrometheusResult[]> {
  const url = new URL(`${BASE}/api/v1/query`);
  url.searchParams.set("query", query);
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) throw new Error(`Prometheus query failed: ${res.status}`);
  const json = (await res.json()) as PrometheusResponse;
  if (json.status !== "success") throw new Error(json.error ?? "Prometheus error");
  return json.data.result;
}

export async function promQueryRange(
  query: string,
  start: number,
  end: number,
  step: number
): Promise<PrometheusResult[]> {
  const url = new URL(`${BASE}/api/v1/query_range`);
  url.searchParams.set("query", query);
  url.searchParams.set("start", String(start));
  url.searchParams.set("end", String(end));
  url.searchParams.set("step", String(step));
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) throw new Error(`Prometheus range query failed: ${res.status}`);
  const json = (await res.json()) as PrometheusResponse;
  if (json.status !== "success") throw new Error(json.error ?? "Prometheus error");
  return json.data.result;
}
