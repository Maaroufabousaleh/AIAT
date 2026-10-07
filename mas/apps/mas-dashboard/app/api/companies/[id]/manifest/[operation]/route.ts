import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type RouteContext = { params: Promise<{ id: string; operation: string }> };

async function forward(
  request: NextRequest,
  context: RouteContext,
  method: "GET" | "POST",
) {
  const { id, operation } = await context.params;
  const allowed = method === "GET" ? ["history"] : ["validate", "apply", "rollback"];
  if (!allowed.includes(operation)) {
    return NextResponse.json({ detail: "Unknown company manifest operation." }, { status: 404 });
  }
  const suffix = operation === "history" ? "?limit=100" : "";
  const options: RequestInit = method === "GET"
    ? { method }
    : { method, body: await request.text() };
  try {
    return NextResponse.json(await orchestratorFetch(
      `/companies/${encodeURIComponent(id)}/manifest/${operation}${suffix}`,
      options,
    ));
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "Company manifest service is unavailable." }, { status: 503 });
  }
}

export async function GET(request: NextRequest, context: RouteContext) {
  return forward(request, context, "GET");
}

export async function POST(request: NextRequest, context: RouteContext) {
  return forward(request, context, "POST");
}
