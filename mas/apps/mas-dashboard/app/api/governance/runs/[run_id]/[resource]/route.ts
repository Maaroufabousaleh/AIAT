import { NextResponse } from "next/server";

import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const READ_RESOURCES = new Set(["events", "transitions", "artifacts", "usage", "checkpoints", "tool-effects"]);
const ACTION_RESOURCES = new Set(["cancel", "pause", "resume", "retry"]);
const MAX_ACTION_BODY_BYTES = 8_192;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type ActionBodyResult =
  | { ok: true; payload: Record<string, string | boolean> }
  | { ok: false; status: number; detail: string };
type ReadActionBodyResult =
  | { ok: true; body: Record<string, unknown> }
  | { ok: false; status: number; detail: string };

async function readBoundedJson(request: Request): Promise<ReadActionBodyResult> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    return { ok: false, status: 415, detail: "Worker Run actions require a JSON request body." };
  }
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_ACTION_BODY_BYTES) {
    return { ok: false, status: 413, detail: "Worker Run action request is too large." };
  }

  if (!request.body) return { ok: false, status: 400, detail: "Worker Run action request body is required." };
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let byteLength = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      byteLength += value.byteLength;
      if (byteLength > MAX_ACTION_BODY_BYTES) {
        await reader.cancel();
        return { ok: false, status: 413, detail: "Worker Run action request is too large." };
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(byteLength);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    const parsed: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, status: 400, detail: "Worker Run action body must be a JSON object." };
    }
    return { ok: true, body: parsed as Record<string, unknown> };
  } catch {
    return { ok: false, status: 400, detail: "Worker Run action request body is not valid JSON." };
  }
}

function validateActionBody(
  body: Record<string, unknown>,
  resource: string,
): ActionBodyResult {
  if (resource === "retry") {
    if (Object.keys(body).some((key) => !["reason", "idempotency_key"].includes(key))) {
      return { ok: false, status: 400, detail: "Worker Run retry body contains an unsupported field." };
    }
    const reason = body.reason;
    const idempotencyKey = body.idempotency_key;
    if (typeof reason !== "string" || reason.trim().length === 0 || reason.trim().length > 4_000) {
      return { ok: false, status: 400, detail: "A non-empty retry reason of at most 4000 characters is required." };
    }
    if (typeof idempotencyKey !== "string" || idempotencyKey.trim().length === 0 || idempotencyKey.trim().length > 128) {
      return { ok: false, status: 400, detail: "A retry idempotency key of at most 128 characters is required." };
    }
    return { ok: true, payload: { reason: reason.trim(), idempotency_key: idempotencyKey.trim() } };
  }
  const allowedFields = resource === "cancel"
    ? new Set(["reason", "force"])
    : resource === "pause"
      ? new Set(["reason"])
      : new Set(["checkpoint_id"]);
  if (Object.keys(body).some((key) => !allowedFields.has(key))) {
    return { ok: false, status: 400, detail: "Worker Run action body contains an unsupported field." };
  }
  if (resource === "resume") {
    const checkpointId = body.checkpoint_id;
    if (checkpointId === undefined) return { ok: true, payload: {} };
    if (typeof checkpointId !== "string" || !UUID_PATTERN.test(checkpointId)) {
      return { ok: false, status: 400, detail: "checkpoint_id must be a valid UUID." };
    }
    return { ok: true, payload: { checkpoint_id: checkpointId } };
  }

  const reason = body.reason;
  if (typeof reason !== "string" || reason.trim().length === 0 || reason.trim().length > 4_000) {
    return { ok: false, status: 400, detail: "A non-empty action reason of at most 4000 characters is required." };
  }
  if (resource === "cancel") {
    const force = body.force === undefined ? false : body.force;
    if (typeof force !== "boolean") {
      return { ok: false, status: 400, detail: "force must be a boolean." };
    }
    return { ok: true, payload: { reason: reason.trim(), force } };
  }
  return { ok: true, payload: { reason: reason.trim() } };
}

function failureResponse(error: unknown) {
  if (error instanceof OrchestratorError) {
    try {
      return NextResponse.json(JSON.parse(error.message), { status: error.status });
    } catch {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
  }
  return NextResponse.json({ detail: "The orchestrator is unavailable." }, { status: 503 });
}

function boundedQuery(request: Request, resource: string): string {
  const incoming = new URL(request.url).searchParams;
  const query = new URLSearchParams();
  const maximumLimit = resource === "tool-effects" ? 200 : resource === "checkpoints" ? 1_000 : 10_000;
  const rawLimit = incoming.get("limit");
  if (rawLimit && /^\d+$/.test(rawLimit)) {
    query.set("limit", String(Math.min(maximumLimit, Math.max(1, Number(rawLimit)))));
  }
  const rawOffset = incoming.get("offset");
  if (resource === "events" && rawOffset && /^\d+$/.test(rawOffset)) {
    query.set("offset", String(Math.min(100_000, Number(rawOffset))));
  }
  return query.size > 0 ? `?${query.toString()}` : "";
}

export async function GET(
  request: Request,
  context: { params: Promise<{ run_id: string; resource: string }> },
) {
  const { run_id, resource } = await context.params;
  if (!READ_RESOURCES.has(resource)) {
    return NextResponse.json({ detail: "Worker Run resource not found." }, { status: 404 });
  }
  try {
    const path = `/workers/runs/${encodeURIComponent(run_id)}/${resource}${boundedQuery(request, resource)}`;
    return NextResponse.json(await orchestratorFetch(path));
  } catch (error) {
    return failureResponse(error);
  }
}

export async function POST(
  request: Request,
  context: { params: Promise<{ run_id: string; resource: string }> },
) {
  const { run_id, resource } = await context.params;
  if (!ACTION_RESOURCES.has(resource)) {
    return NextResponse.json({ detail: "Worker Run action not found." }, { status: 404 });
  }
  const parsed = await readBoundedJson(request);
  if (!parsed.ok) {
    return NextResponse.json({ detail: parsed.detail }, { status: parsed.status });
  }
  const validated = validateActionBody(parsed.body, resource);
  if (!validated.ok) {
    return NextResponse.json({ detail: validated.detail }, { status: validated.status });
  }
  try {
    const result = await orchestratorFetch(
      `/workers/runs/${encodeURIComponent(run_id)}/${resource}`,
      { method: "POST", body: JSON.stringify(validated.payload) },
    );
    return NextResponse.json(result);
  } catch (error) {
    return failureResponse(error);
  }
}
