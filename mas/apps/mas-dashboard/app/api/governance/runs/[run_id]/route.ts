import { NextResponse } from "next/server";

import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

function failureResponse(error: unknown) {
  if (error instanceof OrchestratorError) {
    try {
      return NextResponse.json(JSON.parse(error.message), { status: error.status });
    } catch {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
  }
  return NextResponse.json({ detail: "The orchestrator is unavailable." }, { status: 503 });
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ run_id: string }> },
) {
  const { run_id } = await context.params;
  try {
    const run = await orchestratorFetch(`/workers/runs/${encodeURIComponent(run_id)}`);
    return NextResponse.json(run);
  } catch (error) {
    return failureResponse(error);
  }
}
