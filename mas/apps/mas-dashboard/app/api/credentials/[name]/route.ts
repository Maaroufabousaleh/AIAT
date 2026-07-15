import { NextRequest, NextResponse } from "next/server";

const ORCHESTRATOR =
  process.env.ORCHESTRATOR_URL ?? "http://orchestrator-api:8000";
const ORCHESTRATOR_HEADERS = {
  "X-API-Key": process.env.MAS_API_KEY ?? "",
};
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
    const res = await fetch(`${ORCHESTRATOR}/credentials/${params.name}`, {
      cache: "no-store",
      headers: ORCHESTRATOR_HEADERS,
    });
    const data = await res.json();
    return NextResponse.json(stripCredentialSecrets(data), {
      status: res.status,
    });
  } catch {
    return NextResponse.json(
      { error: "Failed to reach orchestrator" },
      { status: 502 },
    );
  }
}

export async function PATCH(req: NextRequest, props: Params) {
  const params = await props.params;
  try {
    const body = await req.json();
    const res = await fetch(`${ORCHESTRATOR}/credentials/${params.name}`, {
      method: "PATCH",
      headers: {
        ...ORCHESTRATOR_HEADERS,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(stripCredentialSecrets(data), {
      status: res.status,
    });
  } catch {
    return NextResponse.json(
      { error: "Failed to reach orchestrator" },
      { status: 502 },
    );
  }
}

export async function DELETE(_req: NextRequest, props: Params) {
  const params = await props.params;
  try {
    const res = await fetch(`${ORCHESTRATOR}/credentials/${params.name}`, {
      method: "DELETE",
      headers: ORCHESTRATOR_HEADERS,
    });
    if (res.status === 204) return new NextResponse(null, { status: 204 });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to reach orchestrator" },
      { status: 502 },
    );
  }
}
