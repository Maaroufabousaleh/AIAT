import { NextResponse } from "next/server";
import { createHash } from "node:crypto";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

type Params = { params: Promise<{ id: string }> };

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";
const OPERATOR_KEY = process.env.AIAT_OPERATOR_API_KEY ?? "";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[a-f0-9]{64}$/i;
const REPORT_PATH = "reports/research-brief.md";
const MAX_REPORT_BYTES = 10 * 1024 * 1024;

type RecordValue = Record<string, unknown>;

function isRecord(value: unknown): value is RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isResearchReportArtifact(
  value: unknown,
  projectId: string,
): value is RecordValue & {
  id: number;
  path: string;
  sha256: string;
  size_bytes: number;
  metadata: RecordValue & {
    project_id: string;
    worker_run_id: string;
    blob_bucket: string;
    blob_key: string;
    blob_sha256: string;
    blob_size_bytes: number;
  };
} {
  if (!isRecord(value) || !isRecord(value.metadata)) return false;
  const metadata = value.metadata;
  const runId = metadata.worker_run_id;
  const attempt = metadata.worker_run_attempt;
  const canonicalProjectId = projectId.toLowerCase();
  const expectedKey =
    typeof runId === "string" && UUID_PATTERN.test(runId)
      ? `${canonicalProjectId}/worker-runs/${runId}/${
          attempt === undefined ? "" : `attempt-${attempt}/`
        }${REPORT_PATH}`
      : "";

  return (
    Number.isSafeInteger(value.id) &&
    (value.id as number) > 0 &&
    value.path === REPORT_PATH &&
    typeof value.sha256 === "string" &&
    SHA256_PATTERN.test(value.sha256) &&
    Number.isSafeInteger(value.size_bytes) &&
    (value.size_bytes as number) >= 0 &&
    (value.size_bytes as number) <= MAX_REPORT_BYTES &&
    typeof metadata.project_id === "string" &&
    metadata.project_id.toLowerCase() === projectId.toLowerCase() &&
    typeof runId === "string" &&
    UUID_PATTERN.test(runId) &&
    (attempt === undefined || (Number.isSafeInteger(attempt) && (attempt as number) > 0) ) &&
    typeof metadata.blob_bucket === "string" &&
    metadata.blob_bucket.length > 0 &&
    typeof metadata.blob_key === "string" &&
    metadata.blob_key === expectedKey &&
    typeof metadata.blob_sha256 === "string" &&
    metadata.blob_sha256.toLowerCase() === value.sha256.toLowerCase() &&
    metadata.blob_size_bytes === value.size_bytes
  );
}

function safeFailureStatus(error: unknown): number {
  if (error instanceof OrchestratorError && [400, 401, 403, 404, 409, 422].includes(error.status)) {
    return error.status;
  }
  return 502;
}

async function readBoundedBody(response: Response, maxBytes: number): Promise<Uint8Array> {
  const reader = response.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new Error("Report exceeds the verification limit");
    }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

export async function GET(_: Request, props: Params) {
  const params = await props.params;
  try {
    const [documents, feasibility, sprints] = await Promise.allSettled([
      orchestratorFetch(`/projects/${params.id}/documents`),
      orchestratorFetch(`/projects/${params.id}/feasibility`),
      orchestratorFetch(`/projects/${params.id}/sprints`),
    ]);
    return NextResponse.json({
      documents: documents.status === "fulfilled" ? documents.value : null,
      feasibility:
        feasibility.status === "fulfilled" ? feasibility.value : null,
      sprints: sprints.status === "fulfilled" ? sprints.value : null,
    });
  } catch (e) {
    if (e instanceof OrchestratorError)
      return NextResponse.json({ error: e.message }, { status: e.status });
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}

export async function POST(req: Request, props: Params) {
  const { id } = await props.params;
  if (!UUID_PATTERN.test(id)) {
    return NextResponse.json({ error: "Invalid project ID" }, { status: 400 });
  }

  const body: unknown = await req.json().catch(() => null);
  if (
    !isRecord(body) ||
    Object.keys(body).length !== 1 ||
    Object.keys(body)[0] !== "artifact_id" ||
    !Number.isSafeInteger(body.artifact_id) ||
    (body.artifact_id as number) < 1
  ) {
    return NextResponse.json(
      { error: "A project-scoped Research Brief artifact ID is required" },
      { status: 400 },
    );
  }

  const artifactId = body.artifact_id as number;
  try {
    const artifacts = await orchestratorFetch<unknown>(
      `/projects/${encodeURIComponent(id)}/artifacts?limit=1000`,
    );
    const artifact = Array.isArray(artifacts)
      ? artifacts.find((candidate) => isRecord(candidate) && candidate.id === artifactId)
      : undefined;
    if (!isResearchReportArtifact(artifact, id)) {
      return NextResponse.json(
        { error: "The selected artifact is not a verified Research Brief report for this project" },
        { status: 409 },
      );
    }

    const documents = await orchestratorFetch<unknown>(
      `/projects/${encodeURIComponent(id)}/documents`,
    );
    if (!Array.isArray(documents)) {
      return NextResponse.json({ error: "Project document state is unavailable" }, { status: 502 });
    }
    const existing = documents.find(
      (document) =>
        isRecord(document) &&
        document.doc_type === "RESEARCH_BRIEF" &&
        document.blob_bucket === artifact.metadata.blob_bucket &&
        document.blob_key === artifact.metadata.blob_key &&
        typeof document.blob_sha256 === "string" &&
        document.blob_sha256.toLowerCase() === artifact.sha256.toLowerCase(),
    );
    if (existing) return NextResponse.json(existing);
    if (
      documents.some(
        (document) => isRecord(document) && document.doc_type === "RESEARCH_BRIEF",
      )
    ) {
      return NextResponse.json(
        { error: "A different Research Brief document is already registered for this project" },
        { status: 409 },
      );
    }

    const upstream = await fetch(
      `${ORCHESTRATOR_URL}/projects/${encodeURIComponent(id)}/artifacts/${artifactId}/download`,
      {
        headers: {
          "X-API-Key": OPERATOR_KEY,
          "X-AIAT-Actor-ID": "dashboard-operator",
          "X-AIAT-Dashboard-Section": "projects",
          Accept: "*/*",
        },
        cache: "no-store",
        signal: AbortSignal.timeout(30_000),
      },
    );
    if (!upstream.ok) {
      return NextResponse.json(
        { error: "The Research Brief report content could not be verified" },
        { status: upstream.status >= 500 ? 502 : upstream.status },
      );
    }
    const verifiedSha = upstream.headers.get("x-aiat-artifact-sha256");
    const verifiedSize = upstream.headers.get("x-aiat-artifact-size");
    if (
      !verifiedSha ||
      !SHA256_PATTERN.test(verifiedSha) ||
      verifiedSha.toLowerCase() !== artifact.sha256.toLowerCase() ||
      !verifiedSize ||
      !/^\d{1,10}$/.test(verifiedSize) ||
      Number(verifiedSize) !== artifact.size_bytes
    ) {
      return NextResponse.json(
        { error: "The Research Brief report content could not be verified" },
        { status: 409 },
      );
    }
    const reportBytes = await readBoundedBody(upstream, artifact.size_bytes);
    if (
      reportBytes.byteLength !== artifact.size_bytes ||
      createHash("sha256").update(reportBytes).digest("hex") !== artifact.sha256.toLowerCase()
    ) {
      return NextResponse.json(
        { error: "The Research Brief report content could not be verified" },
        { status: 409 },
      );
    }

    const document = await orchestratorFetch(
      `/projects/${encodeURIComponent(id)}/documents`,
      {
        method: "POST",
        body: JSON.stringify({
          doc_type: "RESEARCH_BRIEF",
          created_by: "dashboard-operator",
          blob_bucket: artifact.metadata.blob_bucket,
          blob_key: artifact.metadata.blob_key,
          blob_sha256: artifact.sha256,
        }),
      },
    );
    return NextResponse.json(document, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { error: "The Research Brief document could not be registered" },
      { status: safeFailureStatus(error) },
    );
  }
}
