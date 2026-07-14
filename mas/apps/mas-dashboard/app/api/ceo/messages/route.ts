import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const { message, context_worker_id } = body as {
      message: string;
      context_worker_id?: string;
    };

    if (!message || typeof message !== "string" || message.trim().length === 0) {
      return NextResponse.json({ error: "Message is required" }, { status: 400 });
    }

    // Route through the orchestrator's /ceo/message endpoint.
    // The orchestrator uses its own sender_role (ORCHESTRATOR) which is
    // permitted by CommunicationPolicy to send to exec_ceo.
    const result = await orchestratorFetch<{
      ok: boolean;
      entry_id?: string;
      action?: { worker?: { id?: string } };
    }>(
      "/ceo/message",
      {
        method: "POST",
        body: JSON.stringify({
          message: message.trim(),
          ...(context_worker_id ? { context_worker_id } : {}),
        }),
      }
    );

    return NextResponse.json(result);
  } catch (e) {
    if (e instanceof OrchestratorError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    if (e instanceof Error && e.message.includes("fetch")) {
      return NextResponse.json(
        { error: "Cannot reach the orchestrator. Is it running?" },
        { status: 503 }
      );
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
