import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch } from "@/lib/orchestrator";

function normalizeWorker(worker: Record<string, unknown>) {
  const adapterConfig =
    worker.adapter_config && typeof worker.adapter_config === "object"
      ? (worker.adapter_config as Record<string, unknown>)
      : {};
  return {
    ...worker,
    worker_id: worker.worker_id ?? worker.name,
    name: worker.display_name ?? worker.worker_name ?? worker.name,
    transport_mode: worker.transport_mode ?? worker.adapter_type,
    adapter_entrypoint: worker.adapter_entrypoint ?? adapterConfig.entrypoint,
  };
}

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const team_id = searchParams.get("team_id") ?? undefined;
    const status = searchParams.get("status") ?? undefined;

    let path = "/capabilities/workers";
    const params: string[] = [];
    if (team_id) params.push(`team_id=${encodeURIComponent(team_id)}`);
    if (status) params.push(`status=${encodeURIComponent(status)}`);
    if (params.length > 0) path += `?${params.join("&")}`;

    const workers = await orchestratorFetch(path);
    return NextResponse.json(Array.isArray(workers) ? workers.map(normalizeWorker) : workers);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const payload = {
      ...body,
      name: body.worker_id ?? body.name,
      adapter_type: body.adapter_type ?? body.transport_mode,
      adapter_config: body.adapter_config ?? {
        ...(body.adapter_entrypoint ? { entrypoint: body.adapter_entrypoint } : {}),
      },
    };
    const worker = await orchestratorFetch("/capabilities/workers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return NextResponse.json(normalizeWorker(worker as Record<string, unknown>), { status: 201 });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
