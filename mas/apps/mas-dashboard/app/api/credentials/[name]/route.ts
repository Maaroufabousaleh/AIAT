import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const SENSITIVE_RESPONSE_KEYS = new Set([
  "value",
  "encrypted_value",
  "secret",
  "token",
  "password",
  "api_key",
]);

function stripCredentialSecrets(data: unknown): unknown {
  if (Array.isArray(data)) return data.map(stripCredentialSecrets);
  if (!data || typeof data !== "object") return data;
  return Object.fromEntries(
    Object.entries(data as Record<string, unknown>)
      .filter(([key]) => !SENSITIVE_RESPONSE_KEYS.has(key.toLowerCase()))
      .map(([key, value]) => [key, stripCredentialSecrets(value)]),
  );
}

type Params = { params: Promise<{ name: string }> };

export async function GET(_req: NextRequest, props: Params) {
  const params = await props.params;
  try {
    const data = await orchestratorFetch(`/credentials/${encodeURIComponent(params.name)}`);
    return NextResponse.json(stripCredentialSecrets(data));
  } catch (error) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status });
  }
}

export async function PATCH(req: NextRequest, props: Params) {
  const params = await props.params;
  try {
    const body = await req.json();
    const data = await orchestratorFetch(`/credentials/${encodeURIComponent(params.name)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    return NextResponse.json(stripCredentialSecrets(data));
  } catch (error) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status });
  }
}

export async function DELETE(_req: NextRequest, props: Params) {
  const params = await props.params;
  try {
    await orchestratorFetch(`/credentials/${encodeURIComponent(params.name)}`, {
      method: "DELETE",
    });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status });
  }
}
