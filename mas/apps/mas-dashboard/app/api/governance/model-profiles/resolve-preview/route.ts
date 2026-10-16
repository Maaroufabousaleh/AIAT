import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await orchestratorFetch("/model-profiles/resolve-preview", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(result);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Model resolution preview is unavailable." }, { status: 500 });
  }
}
