import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch } from "@/lib/orchestrator";

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
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
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
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
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
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
