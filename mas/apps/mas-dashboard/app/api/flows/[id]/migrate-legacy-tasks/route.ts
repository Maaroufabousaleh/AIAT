import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

/**
 * Proxy the operator-approved saved-definition migration through the same
 * server-side orchestrator credential boundary as the other flow actions.
 * The request intentionally carries explicit node -> worker UUID mappings;
 * this route never infers a worker from a legacy team alias.
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const body = await req.json();
    const data = await orchestratorFetch(`/flows/${id}/migrate-legacy-tasks`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
