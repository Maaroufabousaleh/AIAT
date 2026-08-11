import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const SENSITIVE_RESPONSE_KEYS = new Set(["value", "encrypted_value", "secret", "token", "password", "api_key"]);

function stripCredentialSecrets(data: unknown): unknown {
  if (Array.isArray(data)) return data.map(stripCredentialSecrets);
  if (!data || typeof data !== "object") return data;
  return Object.fromEntries(
    Object.entries(data as Record<string, unknown>)
      .filter(([key]) => !SENSITIVE_RESPONSE_KEYS.has(key.toLowerCase()))
      .map(([key, value]) => [key, stripCredentialSecrets(value)])
  );
}

export async function GET() {
  try {
    const data = await orchestratorFetch("/credentials");
    return NextResponse.json(stripCredentialSecrets(data));
  } catch (error) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const data = await orchestratorFetch("/credentials", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(stripCredentialSecrets(data));
  } catch (error) {
    const status = error instanceof OrchestratorError ? error.status : 502;
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status });
  }
}
