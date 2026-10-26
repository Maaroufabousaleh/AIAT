import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(
  request: Request,
  context: { params: Promise<{ profile_id: string; version_id: string }> },
) {
  try {
    const { profile_id: profileId, version_id: versionId } = await context.params;
    const body = await request.json();
    const result = await orchestratorFetch(
      `/model-profiles/${encodeURIComponent(profileId)}/versions/${encodeURIComponent(versionId)}/activate`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    return NextResponse.json(result);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof SyntaxError) {
      return NextResponse.json({ error: "The model profile approval request was not valid JSON." }, { status: 400 });
    }
    return NextResponse.json({ error: "The model profile version could not be approved." }, { status: 500 });
  }
}
