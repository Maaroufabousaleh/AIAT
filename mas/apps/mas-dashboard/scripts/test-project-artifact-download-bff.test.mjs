import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const PROJECT_ID = "30000000-0000-4000-8000-000000000301";
const ARTIFACT_ID = "73";
const SHA256 = "a".repeat(64);
const state = { calls: [], upstream: null, fetchError: null };

async function mockedFetch(url, init) {
  state.calls.push({ url: String(url), init });
  if (state.fetchError) throw state.fetchError;
  return state.upstream;
}

const sourcePath = fileURLToPath(
  new URL("../app/api/projects/[id]/artifacts/[artifact_id]/download/route.ts", import.meta.url),
);
const source = readFileSync(sourcePath, "utf8");
const javascript = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
  fileName: sourcePath,
}).outputText;
const routeModule = { exports: {} };
runInNewContext(javascript, {
  exports: routeModule.exports,
  module: routeModule,
  require(specifier) {
    if (specifier === "next/server") {
      return {
        NextResponse: {
          json(body, init = {}) {
            return new Response(JSON.stringify(body), {
              status: init.status ?? 200,
              headers: { "Content-Type": "application/json" },
            });
          },
        },
      };
    }
    throw new Error(`Unexpected route dependency: ${specifier}`);
  },
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
}, { filename: sourcePath });

async function invoke(id = PROJECT_ID, artifact_id = ARTIFACT_ID) {
  return routeModule.exports.GET(new Request("http://dashboard.test/download"), {
    params: Promise.resolve({ id, artifact_id }),
  });
}

beforeEach(() => {
  state.calls = [];
  state.upstream = null;
  state.fetchError = null;
});

describe("project artifact download BFF", () => {
  it("validates the project and artifact IDs before forwarding", async () => {
    assert.equal((await invoke("not-a-uuid", ARTIFACT_ID)).status, 400);
    assert.equal((await invoke(PROJECT_ID, "0")).status, 400);
    assert.equal((await invoke(PROJECT_ID, "9007199254740992")).status, 400);
    assert.equal(state.calls.length, 0);
  });

  it("forwards an authenticated project request and safe attachment headers", async () => {
    state.upstream = new Response("artifact bytes", {
      status: 200,
      headers: {
        "Content-Type": "text/plain",
        "Content-Disposition": 'attachment; filename="totals.py"',
        "X-AIAT-Artifact-SHA256": SHA256,
        "X-AIAT-Artifact-Size": "14",
        "Set-Cookie": "backend=session-secret",
      },
    });

    const response = await invoke();
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "artifact bytes");
    assert.equal(state.calls.length, 1);
    assert.equal(
      state.calls[0].url,
      `http://orchestrator.test/projects/${PROJECT_ID}/artifacts/${ARTIFACT_ID}/download`,
    );
    assert.equal(state.calls[0].init.headers["X-API-Key"], "test-operator-key");
    assert.equal(state.calls[0].init.headers["X-AIAT-Actor-ID"], "dashboard-operator");
    assert.equal(state.calls[0].init.headers["X-AIAT-Dashboard-Section"], "projects");
    assert.equal(state.calls[0].init.headers.Accept, "*/*");
    assert.equal(state.calls[0].init.cache, "no-store");
    assert.ok(state.calls[0].init.signal instanceof AbortSignal);
    assert.equal(response.headers.get("Content-Type"), "application/octet-stream");
    assert.equal(response.headers.get("Content-Disposition"), 'attachment; filename="totals.py"');
    assert.equal(response.headers.get("X-AIAT-Artifact-SHA256"), SHA256);
    assert.equal(response.headers.get("X-AIAT-Artifact-Size"), "14");
    assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff");
    assert.equal(response.headers.get("Cache-Control"), "private, no-store");
    assert.equal(response.headers.get("Set-Cookie"), null);
  });

  it("sanitizes unsafe filenames and invalid metadata headers", async () => {
    state.upstream = new Response("bytes", {
      status: 200,
      headers: {
        "Content-Disposition": 'attachment; filename="../../private"',
        "X-AIAT-Artifact-SHA256": "invalid",
        "X-AIAT-Artifact-Size": "-1",
      },
    });

    const response = await invoke();
    assert.equal(response.headers.get("Content-Disposition"), 'attachment; filename="project-artifact-73"');
    assert.equal(response.headers.get("X-AIAT-Artifact-SHA256"), null);
    assert.equal(response.headers.get("X-AIAT-Artifact-Size"), null);
  });

  it("returns bounded errors without backend details", async () => {
    state.upstream = new Response(JSON.stringify({ detail: "private storage endpoint" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
    const missing = await invoke();
    const payload = await missing.json();
    assert.equal(missing.status, 404);
    assert.equal(payload.error, "Artifact download failed with status 404");
    assert.equal(JSON.stringify(payload).includes("private storage"), false);

    state.fetchError = new Error("private upstream configuration");
    const unavailable = await invoke();
    assert.equal(unavailable.status, 502);
    assert.equal((await unavailable.json()).error, "Artifact service is temporarily unavailable");
  });
});
