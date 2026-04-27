import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch } from "@/lib/orchestrator";

export async function PATCH(
  req: NextRequest,
  { params }: { params: { worker_id: string } }
) {
  try {
    const body = await req.json();
    const result = await orchestratorFetch(
      `/capabilities/workers/${params.worker_id}/status`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      }
    );
    return NextResponse.json(result);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
