const BASE = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";
const OPERATOR_KEY = process.env.AIAT_OPERATOR_API_KEY ?? "";
// Dashboard-to-control-plane calls are always the human operator principal.
// Do not silently fall back to the shared service credential: a missing
// operator key must fail closed rather than collapsing section boundaries.
const AUTH_KEY = OPERATOR_KEY;

/**
 * Dashboard pages are composed from several orchestrator endpoints.  Sending
 * one bounded section name lets the API apply the persisted section ACL to
 * every request without trusting a user-supplied role header.
 */
function dashboardSectionForPath(path: string): string | undefined {
  const pathname = path.split("?", 1)[0].replace(/^\/api\/v1(?=\/)/, "");
  if (pathname === "/metrics" || pathname.startsWith("/analytics")) return "analytics";
  if (pathname.startsWith("/ceo")) return "ceo";
  if (pathname.startsWith("/credentials")) return "credentials";
  if (pathname.startsWith("/flows")) return "flows";
  if (pathname.startsWith("/evaluations") || pathname.startsWith("/tools")) return "governance";
  if (pathname.startsWith("/stewards") || pathname.startsWith("/runtimes") || pathname.startsWith("/model-profiles")) {
    return "governance";
  }
  if (pathname.startsWith("/executive")) return "governance";
  if (pathname.startsWith("/identity")) return "identity";
  if (pathname.startsWith("/integrations")) return "integrations";
  if (pathname.startsWith("/system/shutdown") || pathname.startsWith("/system/resume") || pathname.startsWith("/system/logs") || pathname.startsWith("/system/schedule")) {
    return "operations";
  }
  if (pathname.startsWith("/dead-letters") || pathname.startsWith("/dlq") || pathname.startsWith("/logs") || pathname.startsWith("/streams")) {
    return "operations";
  }
  if (pathname.startsWith("/system/permissions")) return "governance";
  if (pathname.startsWith("/system/") || pathname.startsWith("/companies") || pathname.startsWith("/teams")) return "system";
  if (pathname.startsWith("/projects")) return "projects";
  if (pathname.startsWith("/capabilities/workers") || pathname.startsWith("/workers")) return "workers";
  return undefined;
}

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
  const section = dashboardSectionForPath(path);
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      "X-API-Key": AUTH_KEY,
      "X-AIAT-Actor-ID": "dashboard-operator",
      ...(section ? { "X-AIAT-Dashboard-Section": section } : {}),
      "Content-Type": "application/json",
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
