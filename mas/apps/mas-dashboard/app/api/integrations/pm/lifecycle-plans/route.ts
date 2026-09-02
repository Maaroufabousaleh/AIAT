import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

async function forward(request: NextRequest, path: string, init?: RequestInit) {
  try {
    return NextResponse.json(await orchestratorFetch(path, init));
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "orchestrator unavailable" }, { status: 503 });
  }
}

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams.toString();
  return forward(request, `/integrations/lifecycle-plans${params ? `?${params}` : ""}`);
}

export async function POST(request: NextRequest) {
  const body = await request.text();
  return forward(request, "/integrations/lifecycle-plans", {
    method: "POST",
    body,
  });
}
