import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  try {
    return NextResponse.json(
      await orchestratorFetch(`/companies/${encodeURIComponent(id)}/budgets`),
    );
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "Company budget balances are unavailable." }, { status: 503 });
  }
}
