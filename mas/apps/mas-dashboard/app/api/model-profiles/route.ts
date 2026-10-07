import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await orchestratorFetch("/model-profiles", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(result, { status: 201 });
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof SyntaxError) {
      return NextResponse.json({ error: "The model profile request was not valid JSON." }, { status: 400 });
    }
    return NextResponse.json({ error: "The model profile could not be saved." }, { status: 500 });
  }
}
