import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch } from "@/lib/orchestrator";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const result = await orchestratorFetch("/runtimes/validate", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(result);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
