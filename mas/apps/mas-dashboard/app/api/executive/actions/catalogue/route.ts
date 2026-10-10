import { NextResponse } from "next/server";

import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

/** Load canonical executive request fields from the control-plane schemas and policy. */
export async function GET() {
  try {
    return NextResponse.json(await orchestratorFetch("/executive/actions/catalogue"));
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json(
      { error: error instanceof Error ? error.message : String(error) },
      { status: 500 },
    );
  }
}
