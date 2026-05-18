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

    const rawTools = toolsRes.status === "fulfilled" && toolsRes.value.ok
      ? await toolsRes.value.json()
      : null;
    const health = healthRes.status === "fulfilled" && healthRes.value.ok
      ? await healthRes.value.json()
      : null;

    const toolList = Array.isArray(rawTools) ? rawTools : rawTools?.tools ?? [];
    const tools = toolList.map((tool: Record<string, unknown>) => ({
      ...tool,
      name: tool.name ?? tool.tool_name,
      group: tool.group ?? tool.tool_group,
    }));

    return NextResponse.json({ tools, groups: rawTools?.groups, health });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
