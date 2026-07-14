import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export async function GET() {
  try {
    const data = await orchestratorFetch<unknown>("/dead-letters");
    const rows = Array.isArray(data) ? data : [];
    const dead_letters = rows.map((value) => {
      const row = value as Record<string, unknown>;
      return {
        id: String(row.id),
        stream: String(row.recipient_team ?? "unknown"),
        message_type: String(row.msg_type ?? "unknown"),
        failure_reason: String(row.failure_reason ?? "unknown"),
        retry_count: Number(row.retry_count ?? 0),
        created_at: String(row.dead_at ?? ""),
        envelope: (row.envelope_json ?? {}) as Record<string, unknown>,
      };
    });
    return NextResponse.json({ dead_letters, total: dead_letters.length });
  } catch (e) {
    if (e instanceof OrchestratorError) return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
