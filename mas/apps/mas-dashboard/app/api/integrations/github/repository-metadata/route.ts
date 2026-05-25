import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

function stripSecretLikeValues(data: unknown): unknown {
  if (Array.isArray(data)) return data.map(stripSecretLikeValues);
  if (data && typeof data === "object") {
    return Object.fromEntries(
      Object.entries(data as Record<string, unknown>).map(([key, value]) => [
        key,
        /token|secret|password|authorization/i.test(key)
          ? "<masked>"
          : stripSecretLikeValues(value),
      ])
    );
  }
  return data;
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const data = await orchestratorFetch("/integrations/github/repository-metadata", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(stripSecretLikeValues(data));
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
