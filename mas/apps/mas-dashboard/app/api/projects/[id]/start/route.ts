import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

export async function POST(_: Request, props: Params) {
  const { id } = await props.params;
  try {
    const data = await orchestratorFetch(`/projects/${id}/start`, {
      method: "POST",
    });
    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
