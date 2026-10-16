import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const PROJECT_ID = "30000000-0000-4000-8000-000000000301";
const DOCUMENT_ID = "30000000-0000-4000-8000-000000000303";
const SHA256 = "a".repeat(64);

const state = { calls: [], upstream: null, fetchError: null };

async function mockedFetch(url, init) {
  state.calls.push({ url: String(url), init });
  if (state.fetchError) throw state.fetchError;
  return state.upstream;
}

function loadRoute(relativePath) {
  const sourcePath = fileURLToPath(new URL(relativePath, import.meta.url));
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
  return routeModule.exports;
}

const route = loadRoute("../app/api/projects/[id]/documents/[documentId]/[action]/route.ts");

async function invoke(id, documentId, action) {
  return route.GET(new Request("http://dashboard.test/api/document"), {
    params: Promise.resolve({ id, documentId, action }),
  });
}

beforeEach(() => {
  state.calls = [];
  state.upstream = null;
  state.fetchError = null;
});

describe("project document preview/download BFF", () => {
  it("validates project and document UUIDs and limits supported actions", async () => {
    assert.equal((await invoke("not-a-uuid", DOCUMENT_ID, "preview")).status, 400);
    assert.equal((await invoke(PROJECT_ID, "not-a-uuid", "download")).status, 400);
    assert.equal((await invoke(PROJECT_ID, DOCUMENT_ID, "delete")).status, 404);
    assert.equal(state.calls.length, 0);
  });

  it("forwards the authenticated project-scoped request and only safe preview headers", async () => {
    state.upstream = new Response("# Generated document", {
      status: 200,
      headers: {
        "Content-Type": "text/markdown; charset=utf-8",
        "X-AIAT-Document-SHA256": SHA256,
        "Set-Cookie": "backend=session-secret",
      },
    });
    const response = await invoke(PROJECT_ID, DOCUMENT_ID, "preview");

    assert.equal(response.status, 200);
    assert.equal(await response.text(), "# Generated document");
    assert.equal(state.calls.length, 1);
    assert.equal(
      state.calls[0].url,
      `http://orchestrator.test/projects/${PROJECT_ID}/documents/${DOCUMENT_ID}/preview`,
    );
    assert.equal(state.calls[0].init.headers["X-API-Key"], "test-operator-key");
    assert.equal(state.calls[0].init.headers["X-AIAT-Actor-ID"], "dashboard-operator");
    assert.equal(state.calls[0].init.headers["X-AIAT-Dashboard-Section"], "projects");
    assert.equal(state.calls[0].init.headers.Accept, "*/*");
    assert.equal(state.calls[0].init.cache, "no-store");
    assert.ok(state.calls[0].init.signal instanceof AbortSignal);
    assert.equal(response.headers.get("X-AIAT-Document-SHA256"), SHA256);
    assert.equal(response.headers.get("Content-Disposition"), "inline");
    assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff");
    assert.equal(response.headers.get("Content-Security-Policy"), "sandbox; default-src 'none'");
    assert.equal(response.headers.get("Set-Cookie"), null);
  });

  it("keeps canonical project ownership failures status-safe", async () => {
    state.upstream = new Response(JSON.stringify({ detail: "private storage diagnostic" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
    const response = await invoke(PROJECT_ID, DOCUMENT_ID, "preview");
    const payload = await response.json();
    assert.equal(response.status, 404);
    assert.equal(payload.error, "Document preview failed with status 404");
    assert.equal(JSON.stringify(payload).includes("private storage diagnostic"), false);
    assert.equal(
      state.calls[0].url,
      `http://orchestrator.test/projects/${PROJECT_ID}/documents/${DOCUMENT_ID}/preview`,
    );
  });

  it("sanitizes attachment names and validates the content digest", async () => {
    state.upstream = new Response("safe bytes", {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": 'attachment; filename="pdr-v2.pdf"',
        "X-AIAT-Document-SHA256": "not-a-digest",
      },
    });
    const validName = await invoke(PROJECT_ID, DOCUMENT_ID, "download");
    assert.equal(validName.headers.get("Content-Disposition"), 'attachment; filename="pdr-v2.pdf"');
    assert.equal(validName.headers.get("X-AIAT-Document-SHA256"), null);

    state.upstream = new Response("safe bytes", {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": 'attachment; filename="../../private"',
      },
    });
    const unsafeName = await invoke(PROJECT_ID, DOCUMENT_ID, "download");
    assert.equal(unsafeName.headers.get("Content-Disposition"), 'attachment; filename="project-document"');
  });

  it("does not expose internal fetch errors", async () => {
    state.fetchError = new Error("orchestrator secret endpoint failure");
    const response = await invoke(PROJECT_ID, DOCUMENT_ID, "download");
    const payload = await response.json();
    assert.equal(response.status, 502);
    assert.equal(payload.error, "Document service is temporarily unavailable");
    assert.equal(JSON.stringify(payload).includes("secret endpoint"), false);
  });
});
