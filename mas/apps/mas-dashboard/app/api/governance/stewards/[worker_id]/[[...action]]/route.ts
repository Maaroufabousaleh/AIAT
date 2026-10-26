import { NextRequest, NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type RouteContext = {
  params: Promise<{ worker_id: string; action?: string[] }>;
};

function stewardPath(workerId: string, action: string[] = []) {
  const root = `/capabilities/workers/${encodeURIComponent(workerId)}/steward`;
  return action.length > 0
    ? `${root}/${action.map((segment) => encodeURIComponent(segment)).join("/")}`
    : root;
}

function errorResponse(error: unknown) {
  if (error instanceof OrchestratorError) {
    return NextResponse.json({ error: error.message }, { status: error.status });
  }
  return NextResponse.json({ error: "Worker steward request failed" }, { status: 500 });
}

export async function GET(_request: NextRequest, context: RouteContext) {
  const { worker_id, action = [] } = await context.params;
  try {
    if (action.length === 0) {
      const [steward, candidates] = await Promise.all([
        orchestratorFetch(stewardPath(worker_id)),
        orchestratorFetch(stewardPath(worker_id, ["candidates"])),
      ]);
      return NextResponse.json({ steward, candidates });
    }
    if (action.length === 1 && action[0] === "candidates") {
      return NextResponse.json(await orchestratorFetch(stewardPath(worker_id, action)));
    }
    return NextResponse.json({ error: "Worker steward read is not supported" }, { status: 404 });
  } catch (error: unknown) {
    return errorResponse(error);
  }
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { worker_id, action = [] } = await context.params;
  try {
    const body = await request.json();
    const candidateAction = action.length === 3
      && action[0] === "candidates"
      && ["stage", "certify", "approve"].includes(action[2]);
    const rolloutAction = action.length === 3
      && action[0] === "rollouts"
      && ["advance", "rollback"].includes(action[2]);
    const allowed = (action.length === 1 && ["candidates", "rollouts"].includes(action[0]))
      || candidateAction
      || rolloutAction;
    if (!allowed) {
      return NextResponse.json({ error: "Worker steward action is not supported" }, { status: 404 });
    }
    const result = await orchestratorFetch(stewardPath(worker_id, action), {
      method: "POST",
      body: JSON.stringify(body),
    });
    const created = action.length === 1 && ["candidates", "rollouts"].includes(action[0]);
    return NextResponse.json(result, { status: created ? 201 : 200 });
  } catch (error: unknown) {
    return errorResponse(error);
  }
}
