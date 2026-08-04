const BASE = process.env.PROMETHEUS_URL ?? "http://localhost:9090";

// Prometheus is an optional observability dependency.  It must never hold
// the dashboard's server-rendered pages hostage when it is stopped or
// unreachable.  Keep the timeout short; the overview page renders a useful
// degraded state when these queries fail.
const configuredTimeout = Number.parseInt(
  process.env.PROMETHEUS_TIMEOUT_MS ?? "750",
  10,
);
const PROMETHEUS_TIMEOUT_MS = Number.isFinite(configuredTimeout)
  ? Math.min(Math.max(configuredTimeout, 100), 5000)
  : 750;

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

async function fetchPrometheusJson(url: string): Promise<PrometheusResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROMETHEUS_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Prometheus request failed: ${response.status}`);
    }
    // Keep the abort timer active while the response body is consumed too;
    // a healthy TCP connection must not be able to hold SSR indefinitely.
    return (await response.json()) as PrometheusResponse;
  } finally {
    clearTimeout(timer);
  }
}

export async function promQuery(query: string): Promise<PrometheusResult[]> {
  const url = new URL(`${BASE}/api/v1/query`);
  url.searchParams.set("query", query);
  const json = await fetchPrometheusJson(url.toString());
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
  const json = await fetchPrometheusJson(url.toString());
  if (json.status !== "success") throw new Error(json.error ?? "Prometheus error");
  return json.data.result;
}
