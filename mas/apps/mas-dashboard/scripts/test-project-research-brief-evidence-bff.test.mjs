import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const PROJECT_ID = "ab000000-0000-4000-8000-000000000301";
const RUN_ID = "30000000-0000-4000-8000-000000000302";
const CONTENT = "# Research Brief\n\nVerified synthetic report.\n";
const SHA256 = createHash("sha256").update(CONTENT).digest("hex");
const ATTEMPT = 2;
const BLOB_BUCKET = "synthetic-research";
const BLOB_KEY = `${PROJECT_ID}/worker-runs/${RUN_ID}/attempt-${ATTEMPT}/reports/research-brief.md`;
const state = { calls: [], artifacts: [], documents: [], download: null, fetchError: null };

function artifact(overrides = {}) {
  return {
    id: 7,
    path: "reports/research-brief.md",
    sha256: SHA256,
    size_bytes: Buffer.byteLength(CONTENT),
    metadata: {
      project_id: PROJECT_ID,
      worker_run_id: RUN_ID,
      worker_run_attempt: ATTEMPT,
      blob_bucket: BLOB_BUCKET,
      blob_key: BLOB_KEY,
      blob_sha256: SHA256,
      blob_size_bytes: Buffer.byteLength(CONTENT),
    },
    ...overrides,
  };
}

async function mockedFetch(url, init = {}) {
  const target = String(url);
  const normalizedTarget = target.toLowerCase();
  const method = String(init.method ?? "GET").toUpperCase();
  state.calls.push({ url: target, method, init });
  if (state.fetchError) throw state.fetchError;
  if (normalizedTarget.endsWith(`/projects/${PROJECT_ID}/artifacts?limit=1000`)) {
    return Response.json(state.artifacts);
  }
  if (normalizedTarget.endsWith(`/projects/${PROJECT_ID}/documents`) && method === "GET") {
    return Response.json(state.documents);
  }
  if (normalizedTarget.endsWith(`/projects/${PROJECT_ID}/documents`) && method === "POST") {
    const body = JSON.parse(String(init.body));
    const document = { id: "40000000-0000-4000-8000-000000000401", ...body };
    state.documents.push(document);
    return Response.json(document, { status: 201 });
  }
  if (normalizedTarget === `http://orchestrator.test/projects/${PROJECT_ID}/artifacts/7/download`) {
    return state.download;
  }
  throw new Error("Unexpected test fetch destination");
}

function compile(relativePath, injectedRequire) {
  const sourcePath = fileURLToPath(new URL(relativePath, import.meta.url));
  const javascript = ts.transpileModule(readFileSync(sourcePath, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: sourcePath,
  }).outputText;
  const transpiledModule = { exports: {} };
  runInNewContext(javascript, {
    exports: transpiledModule.exports,
    module: transpiledModule,
    require: injectedRequire,
    process: {
      env: {
        ORCHESTRATOR_URL: "http://orchestrator.test",
        AIAT_OPERATOR_API_KEY: "test-operator-key",
      },
    },
    fetch: mockedFetch,
    AbortSignal,
    Headers,
    Response,
    URL,
    URLSearchParams,
  }, { filename: sourcePath });
  return transpiledModule.exports;
}

const orchestrator = compile("../lib/orchestrator.ts", (specifier) => {
  throw new Error(`Unexpected orchestrator dependency: ${specifier}`);
});
const route = compile("../app/api/projects/[id]/documents/route.ts", (specifier) => {
  if (specifier === "next/server") {
    return {
      NextResponse: {
        json(body, init = {}) {
          return Response.json(body, { status: init.status ?? 200 });
        },
      },
    };
  }
  if (specifier === "node:crypto") return { createHash };
  if (specifier === "@/lib/orchestrator") return orchestrator;
  throw new Error(`Unexpected route dependency: ${specifier}`);
});

async function invoke(id = PROJECT_ID, body = { artifact_id: 7 }) {
  const request = new Request("http://dashboard.test/api/project/documents", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return route.POST(request, { params: Promise.resolve({ id }) });
}

function verifiedDownload(content = CONTENT, digest = SHA256, size = Buffer.byteLength(CONTENT)) {
  return new Response(content, {
    status: 200,
    headers: {
      "X-AIAT-Artifact-SHA256": digest,
      "X-AIAT-Artifact-Size": String(size),
    },
  });
}

beforeEach(() => {
  state.calls = [];
  state.artifacts = [artifact()];
  state.documents = [];
  state.download = verifiedDownload();
  state.fetchError = null;
});

describe("Research Brief artifact evidence BFF", () => {
  it("accepts only a project-scoped artifact ID", async () => {
    assert.equal((await invoke("not-a-uuid")).status, 400);
    assert.equal((await invoke(PROJECT_ID, { artifact_id: 7, created_by: "forged" })).status, 400);
    assert.equal(state.calls.length, 0);
  });

  it("registers only canonical report metadata after verified content and fixes the actor and type", async () => {
    const response = await invoke();
    assert.equal(response.status, 201);
    assert.equal(state.calls.length, 4);
    assert.deepEqual(state.calls.map(({ method }) => method), ["GET", "GET", "GET", "POST"]);
    assert.equal(state.calls[2].url, `http://orchestrator.test/projects/${PROJECT_ID}/artifacts/7/download`);
    assert.equal(state.calls[2].init.headers["X-API-Key"], "test-operator-key");
    assert.equal(state.calls[2].init.headers["X-AIAT-Actor-ID"], "dashboard-operator");
    assert.equal(state.calls[2].init.headers["X-AIAT-Dashboard-Section"], "projects");
    assert.equal(state.calls[2].init.cache, "no-store");
    assert.deepEqual(JSON.parse(state.calls[3].init.body), {
      doc_type: "RESEARCH_BRIEF",
      created_by: "dashboard-operator",
      blob_bucket: BLOB_BUCKET,
      blob_key: BLOB_KEY,
      blob_sha256: SHA256,
    });
    assert.equal((await response.json()).doc_type, "RESEARCH_BRIEF");
  });

  it("normalizes UUID casing when matching canonical project artifact ownership", async () => {
    const response = await invoke(PROJECT_ID.toUpperCase());

    assert.equal(response.status, 201);
    assert.equal(state.calls.some(({ method }) => method === "POST"), true);
  });

  it("rejects wrong path, project/run ownership, and malformed attempt bindings before download", async () => {
    const candidates = [
      artifact({ path: "reports/other.md" }),
      artifact({ metadata: { ...artifact().metadata, project_id: "40000000-0000-4000-8000-000000000401" } }),
      artifact({ metadata: { ...artifact().metadata, worker_run_attempt: 0 } }),
      artifact({ metadata: { ...artifact().metadata, blob_key: `${PROJECT_ID}/worker-runs/${RUN_ID}/reports/research-brief.md` } }),
    ];
    for (const candidate of candidates) {
      state.calls = [];
      state.artifacts = [candidate];
      const response = await invoke();
      assert.equal(response.status, 409);
      assert.equal(state.calls.some(({ url }) => url.endsWith("/download")), false);
      assert.equal(state.calls.some(({ method }) => method === "POST"), false);
    }
  });

  it("does not register when downloaded metadata disagrees or the report body cannot be verified", async () => {
    state.download = verifiedDownload(CONTENT, "b".repeat(64));
    let response = await invoke();
    assert.equal(response.status, 409);
    assert.equal(state.calls.some(({ method }) => method === "POST"), false);

    state.calls = [];
    state.download = verifiedDownload("tampered report bytes", SHA256, Buffer.byteLength(CONTENT));
    response = await invoke();
    assert.equal(response.status, 409);
    assert.equal(state.calls.some(({ method }) => method === "POST"), false);
  });

  it("returns the exact existing document idempotently and conflicts with a different report", async () => {
    const existing = {
      id: "40000000-0000-4000-8000-000000000402",
      doc_type: "RESEARCH_BRIEF",
      blob_bucket: BLOB_BUCKET,
      blob_key: BLOB_KEY,
      blob_sha256: SHA256,
    };
    state.documents = [existing];
    let response = await invoke();
    assert.equal(response.status, 200);
    assert.equal((await response.json()).id, existing.id);
    assert.equal(state.calls.some(({ url }) => url.endsWith("/download")), false);

    state.calls = [];
    state.documents = [{ ...existing, blob_key: `${PROJECT_ID}/different/report.md` }];
    response = await invoke();
    assert.equal(response.status, 409);
    assert.equal(state.calls.some(({ url }) => url.endsWith("/download")), false);
  });

  it("sanitizes upstream failures", async () => {
    state.fetchError = new Error("private backend connection detail");
    const response = await invoke();
    const body = await response.json();
    assert.equal(response.status, 502);
    assert.equal(body.error, "The Research Brief document could not be registered");
    assert.equal(JSON.stringify(body).includes("private backend"), false);
  });
});
