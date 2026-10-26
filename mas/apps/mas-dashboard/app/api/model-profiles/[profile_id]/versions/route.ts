import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(
  request: Request,
  context: { params: Promise<{ profile_id: string }> },
) {
  try {
    const { profile_id: profileId } = await context.params;
    const body = await request.json();
    const result = await orchestratorFetch(`/model-profiles/${encodeURIComponent(profileId)}/versions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(result, { status: 201 });
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof SyntaxError) {
      return NextResponse.json({ error: "The model version request was not valid JSON." }, { status: 400 });
    }
    return NextResponse.json({ error: "The model version could not be saved." }, { status: 500 });
  }
}
