import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const rawBody = await req.text();
    const data = await orchestratorFetch(`/flows/instances/${id}/retry`, {
      method: "POST",
      ...(rawBody ? { body: rawBody } : {}),
    });
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
