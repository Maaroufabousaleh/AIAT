import { NextResponse } from "next/server";

import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

/** Forward a governed CTO worker dispatch without exposing worker output. */
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await orchestratorFetch("/executive/actions/cto/worker-runs", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(result, { status: 202 });
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json(
      { error: error instanceof Error ? error.message : String(error) },
      { status: 400 },
    );
  }
}
