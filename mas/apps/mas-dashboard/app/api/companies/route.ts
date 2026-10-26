import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET() {
  try {
    return NextResponse.json(await orchestratorFetch("/companies"));
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "Company service is unavailable." }, { status: 503 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.text();
    return NextResponse.json(
      await orchestratorFetch("/companies", { method: "POST", body }),
      { status: 201 },
    );
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "Company creation is unavailable." }, { status: 503 });
  }
}
