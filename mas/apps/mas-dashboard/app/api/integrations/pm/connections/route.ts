import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(request: NextRequest) {
  try {
    const body = await request.text();
    const connection = await orchestratorFetch("/integrations/connections", {
      method: "POST",
      body,
    });
    return NextResponse.json(connection, { status: 201 });
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}
