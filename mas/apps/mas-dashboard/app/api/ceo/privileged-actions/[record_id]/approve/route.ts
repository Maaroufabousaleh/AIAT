import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ record_id: string }> };

export async function POST(request: Request, props: Params) {
  const { record_id: recordId } = await props.params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(recordId)) {
    return NextResponse.json({ error: "Invalid approval record id" }, { status: 400 });
  }

  try {
    const body: unknown = await request.json();
    if (!body || typeof body !== "object" || typeof (body as { approved?: unknown }).approved !== "boolean") {
      return NextResponse.json({ error: "A decision is required" }, { status: 400 });
    }
    const reason = typeof (body as { reason?: unknown }).reason === "string"
      ? (body as { reason: string }).reason.trim().slice(0, 1000)
      : "";
    const result = await orchestratorFetch(`/ceo/privileged-action/${encodeURIComponent(recordId)}/approve`, {
      method: "POST",
      // Audit attribution is the dashboard's fixed operator principal; never
      // accept a caller-supplied actor identity from the browser.
      body: JSON.stringify({
        approved: (body as { approved: boolean }).approved,
        decided_by: "dashboard-operator",
        reason,
      }),
    });
    return NextResponse.json(result);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
