const BASE = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";
const KEY = process.env.MAS_API_KEY ?? "";
const OPERATOR_KEY = process.env.AIAT_OPERATOR_API_KEY ?? "";
const AUTH_KEY = OPERATOR_KEY || KEY;

export class OrchestratorError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "OrchestratorError";
  }
}

export async function orchestratorFetch<T = unknown>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "X-API-Key": AUTH_KEY,
      "X-AIAT-Actor-ID": "dashboard-operator",
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    // Disable Next.js cache for API proxy routes
    cache: "no-store",
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new OrchestratorError(res.status, text);
  }

  // 204 No Content
  if (res.status === 204) return {} as T;
  return res.json() as Promise<T>;
}
