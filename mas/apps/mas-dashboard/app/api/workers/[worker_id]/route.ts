import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const PUBLIC_ERROR_MESSAGES: Record<number, string> = {
  400: "Worker request is invalid.",
  401: "Worker service authentication failed.",
  403: "You are not authorized to change this worker.",
  404: "Worker not found.",
  409: "Worker configuration conflicts with the current worker state. Refresh and try again.",
  422: "Worker configuration was rejected by validation. Review the fields and try again.",
  429: "Worker update was rate limited. Try again later.",
};

function errorResponse(error: unknown) {
  const upstreamStatus =
    error instanceof OrchestratorError ? error.status : undefined;
  const status =
    Number.isInteger(upstreamStatus) && upstreamStatus! >= 400 && upstreamStatus! < 500
      ? upstreamStatus!
      : 502;
  const message = PUBLIC_ERROR_MESSAGES[status] ?? "Worker request was rejected.";
  return NextResponse.json({ error: message }, { status });
}

export async function GET(
  _req: NextRequest,
  props: { params: Promise<{ worker_id: string }> },
) {
  const params = await props.params;
  try {
    const worker = await orchestratorFetch(
      `/capabilities/workers/${params.worker_id}`,
    );
    return NextResponse.json(worker);
  } catch (e: unknown) {
    return errorResponse(e);
  }
}

export async function PUT(
  req: NextRequest,
  props: { params: Promise<{ worker_id: string }> },
) {
  const params = await props.params;
  try {
    const body = await req.json();
    const worker = await orchestratorFetch(
      `/capabilities/workers/${params.worker_id}`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    );
    return NextResponse.json(worker);
  } catch (e: unknown) {
    return errorResponse(e);
  }
}

export async function DELETE(
  req: NextRequest,
  props: { params: Promise<{ worker_id: string }> },
) {
  const params = await props.params;
  try {
    const permanent = req.nextUrl.searchParams.get("permanent");
    const suffix = permanent === "true" ? "?permanent=true" : "";
    await orchestratorFetch(`/capabilities/workers/${params.worker_id}${suffix}`, {
      method: "DELETE",
    });
    return NextResponse.json({ success: true });
  } catch (e: unknown) {
    return errorResponse(e);
  }
}
