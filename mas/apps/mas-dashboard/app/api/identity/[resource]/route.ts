import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const ALLOWED = new Set([
  "identities", "mail-domains", "mailboxes", "outbound-mail", "mail-relay",
  "external-accounts", "auth-sessions", "identity-approvals", "identity-audit",
]);

const SENSITIVE = /(password|secret|token|api[_-]?key|credential|cookie|refresh|totp|recovery|body|content_ref)/i;

function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) =>
      [key, SENSITIVE.test(key) ? "[REDACTED]" : redact(item)]
    )
  );
}

export async function GET(_: Request, { params }: { params: Promise<{ resource: string }> }) {
  const { resource } = await params;
  if (!ALLOWED.has(resource)) return NextResponse.json({ error: "Not found" }, { status: 404 });
  try {
    const data = await orchestratorFetch(`/identity/dashboard/${resource}`);
    return NextResponse.json(redact(data));
  } catch (error) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json({ error: "Identity service data is unavailable" }, { status });
  }
}

const ACTIONS: Record<string, Set<string>> = {
  "identity-approvals": new Set(["approval.approve", "approval.reject"]),
  identities: new Set(["identity.suspend", "identity.archive"]),
  mailboxes: new Set(["identity.suspend", "identity.archive"]),
  "external-accounts": new Set(["external.rotate_credentials", "external.suspend", "external.close"]),
  "auth-sessions": new Set(["session.revoke"]),
};

export async function POST(request: Request, { params }: { params: Promise<{ resource: string }> }) {
  const { resource } = await params;
  const input = await request.json().catch(() => null) as Record<string, unknown> | null;
  const action = typeof input?.action === "string" ? input.action : "";
  if (!ACTIONS[resource]?.has(action)) return NextResponse.json({ error: "Action is not permitted" }, { status: 403 });
  const payload = {
    action,
    id: typeof input?.id === "string" ? input.id : null,
    worker_id: typeof input?.worker_id === "string" ? input.worker_id : null,
    service: typeof input?.service === "string" ? input.service : null,
    service_category: typeof input?.service_category === "string" ? input.service_category : "development_test",
    reason: typeof input?.reason === "string" ? input.reason.slice(0, 500) : "dashboard operator decision",
  };
  try {
    const data = await orchestratorFetch("/identity/dashboard/action", { method: "POST", body: JSON.stringify(payload) });
    return NextResponse.json(redact(data));
  } catch (error) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json({ error: "Identity action could not be completed" }, { status });
  }
}
