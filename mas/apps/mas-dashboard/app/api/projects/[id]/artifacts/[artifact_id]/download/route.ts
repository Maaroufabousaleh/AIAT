import { NextResponse } from "next/server";

type Params = { params: Promise<{ id: string; artifact_id: string }> };

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";
const OPERATOR_KEY = process.env.AIAT_OPERATOR_API_KEY ?? "";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[a-f0-9]{64}$/i;

export async function GET(_: Request, props: Params) {
  const { id, artifact_id: artifactId } = await props.params;
  if (!UUID_PATTERN.test(id) || !/^[1-9]\d*$/.test(artifactId)) {
    return NextResponse.json(
      { error: "Invalid project or artifact ID" },
      { status: 400 },
    );
  }
  const numericArtifactId = Number(artifactId);
  if (!Number.isSafeInteger(numericArtifactId)) {
    return NextResponse.json(
      { error: "Invalid project or artifact ID" },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      `${ORCHESTRATOR_URL}/projects/${encodeURIComponent(id)}/artifacts/${numericArtifactId}/download`,
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
  } catch {
    return NextResponse.json(
      { error: "Artifact service is temporarily unavailable" },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    // Do not leak storage diagnostics or backend exception details to the UI.
    return NextResponse.json(
      { error: `Artifact download failed with status ${upstream.status}` },
      { status: upstream.status },
    );
  }

  const upstreamDisposition = upstream.headers.get("content-disposition") ?? "";
  const candidate = upstreamDisposition.match(
    /^attachment;\s*filename="([a-zA-Z0-9][a-zA-Z0-9._-]{0,99})"$/i,
  )?.[1];
  const filename = candidate ?? `project-artifact-${numericArtifactId}`;
  const headers = new Headers({
    "Content-Type": "application/octet-stream",
    "Content-Disposition": `attachment; filename="${filename}"`,
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
  });

  const digest = upstream.headers.get("x-aiat-artifact-sha256");
  if (digest && SHA256_PATTERN.test(digest)) {
    headers.set("X-AIAT-Artifact-SHA256", digest.toLowerCase());
  }
  const size = upstream.headers.get("x-aiat-artifact-size");
  if (size && /^\d{1,10}$/.test(size)) {
    headers.set("X-AIAT-Artifact-Size", size);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}
