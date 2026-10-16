import { NextResponse } from "next/server";
import type { CanonicalIssueCreateRequest } from "@/lib/generated/orchestrator-api";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const CREATE_FIELDS = new Set([
  "title", "description", "issue_type", "priority", "sprint_id",
  "assigned_team", "assigned_agent", "estimated_hours", "story_points",
]);

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validOptionalText(value: unknown, max: number, min = 0): boolean {
  return value === null || (typeof value === "string" && value.length <= max && value.trim().length >= min);
}

function validText(value: unknown, max: number, min = 1): value is string {
  return typeof value === "string" && value.length <= max && value.trim().length >= min;
}

function validateCreateBody(value: unknown): value is CanonicalIssueCreateRequest {
  if (!isObject(value) || Object.keys(value).some((key) => !CREATE_FIELDS.has(key))) return false;
  if ("title" in value && !validText(value.title, 500)) return false;
  if ("description" in value && !validOptionalText(value.description, 100_000)) return false;
  if ("issue_type" in value && !validText(value.issue_type, 64)) return false;
  if ("priority" in value && !validText(value.priority, 64)) return false;
  if ("sprint_id" in value && value.sprint_id !== null && (typeof value.sprint_id !== "string" || !UUID_PATTERN.test(value.sprint_id))) return false;
  if ("assigned_team" in value && !validOptionalText(value.assigned_team, 200)) return false;
  if ("assigned_agent" in value && !validOptionalText(value.assigned_agent, 200)) return false;
  if ("estimated_hours" in value && value.estimated_hours !== null && (typeof value.estimated_hours !== "number" || !Number.isFinite(value.estimated_hours) || value.estimated_hours < 0)) return false;
  if ("story_points" in value && value.story_points !== null && (!Number.isSafeInteger(value.story_points) || (value.story_points as number) < 0)) return false;
  return true;
}

export async function GET(request: Request, props: Params) {
  const { id } = await props.params;
  if (!UUID_PATTERN.test(id)) {
    return NextResponse.json({ error: "project id must be a UUID" }, { status: 400 });
  }

  const rawLimit = new URL(request.url).searchParams.get("limit") ?? "100";
  const limit = Number(rawLimit);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    return NextResponse.json({ error: "limit must be an integer from 1 to 100" }, { status: 400 });
  }

  try {
    const data = await orchestratorFetch(`/projects/${id}/issues?limit=${limit}`);
    return NextResponse.json(data);
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function POST(request: Request, props: Params) {
  const { id } = await props.params;
  if (!UUID_PATTERN.test(id)) {
    return NextResponse.json({ error: "project id must be a UUID" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "request body must be valid JSON" }, { status: 400 });
  }
  if (!validateCreateBody(body)) {
    return NextResponse.json({ error: "issue fields do not match the supported create request" }, { status: 400 });
  }

  try {
    const data = await orchestratorFetch(`/projects/${id}/issues`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(data, { status: 201 });
  } catch (error: unknown) {
    if (error instanceof OrchestratorError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
