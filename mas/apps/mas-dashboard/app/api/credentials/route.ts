import { NextRequest, NextResponse } from "next/server";

const ORCHESTRATOR = process.env.ORCHESTRATOR_URL ?? "http://orchestrator-api:8000";
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
    const res = await fetch(`${ORCHESTRATOR}/credentials`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(stripCredentialSecrets(data), { status: res.status });
  } catch {
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status: 502 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${ORCHESTRATOR}/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(stripCredentialSecrets(data), { status: res.status });
  } catch {
    return NextResponse.json({ error: "Failed to reach orchestrator" }, { status: 502 });
  }
}
