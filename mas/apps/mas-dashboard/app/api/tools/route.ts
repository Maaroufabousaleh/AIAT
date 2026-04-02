import { NextResponse } from "next/server";

const TOOL_SERVICE_URL = process.env.TOOL_SERVICE_URL ?? "http://localhost:8002";
const TOOL_SECRET = process.env.TOOL_SECRET ?? "";

export async function GET() {
  try {
    const [toolsRes, healthRes] = await Promise.allSettled([
      fetch(`${TOOL_SERVICE_URL}/tools`, {
        headers: { Authorization: `Bearer ${TOOL_SECRET}` },
        cache: "no-store",
      }),
      fetch(`${TOOL_SERVICE_URL}/health`, { cache: "no-store" }),
    ]);

    const tools = toolsRes.status === "fulfilled" && toolsRes.value.ok
      ? await toolsRes.value.json()
      : null;
    const health = healthRes.status === "fulfilled" && healthRes.value.ok
      ? await healthRes.value.json()
      : null;

    return NextResponse.json({ tools, health });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
