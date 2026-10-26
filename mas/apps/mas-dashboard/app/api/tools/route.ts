import { NextResponse } from "next/server";

const TOOL_SERVICE_URL = process.env.TOOL_SERVICE_URL ?? "http://localhost:8002";
const TOOL_SECRET = process.env.TOOL_SECRET ?? "";
const CATALOGUE_UNAVAILABLE = "Tool Service catalogue is temporarily unavailable.";

function unavailable(status = 503) {
  return NextResponse.json(
    { error: CATALOGUE_UNAVAILABLE },
    { status },
  );
}

export async function GET() {
  try {
    const [toolsRes, healthRes] = await Promise.allSettled([
      fetch(`${TOOL_SERVICE_URL}/tools`, {
        headers: { Authorization: `Bearer ${TOOL_SECRET}` },
        cache: "no-store",
      }),
      fetch(`${TOOL_SERVICE_URL}/health`, { cache: "no-store" }),
    ]);

    const denied = [toolsRes, healthRes].find(
      (result) =>
        result.status === "fulfilled" &&
        (result.value.status === 401 || result.value.status === 403),
    );
    if (denied && denied.status === "fulfilled") {
      return NextResponse.json(
        { error: "Tools access denied", status: denied.value.status },
        { status: denied.value.status },
      );
    }

    if (toolsRes.status === "rejected") {
      return unavailable();
    }
    if (!toolsRes.value.ok) {
      const status = toolsRes.value.status >= 500 ? 503 : 502;
      return unavailable(status);
    }

    const rawTools = await toolsRes.value.json();
    const health = healthRes.status === "fulfilled" && healthRes.value.ok
      ? await healthRes.value.json()
      : null;

    const toolList = Array.isArray(rawTools)
      ? rawTools
      : Array.isArray(rawTools?.tools)
        ? rawTools.tools
        : null;
    if (!toolList) {
      return unavailable(502);
    }
    const tools = toolList.map((tool: Record<string, unknown>) => ({
      ...tool,
      name: tool.name ?? tool.tool_name,
      group: tool.group ?? tool.tool_group,
    }));

    return NextResponse.json({ tools, groups: rawTools?.groups, health });
  } catch {
    return unavailable(502);
  }
}
