import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const RUN_ID = "50000000-0000-4000-8000-000000000001";
const state = { calls: [], nextData: { run_id: RUN_ID, state: "QUEUED" } };

class OrchestratorError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function orchestratorFetch(path, init) {
  state.calls.push({ path, init });
  return state.nextData;
}

const sourcePath = fileURLToPath(new URL("../app/api/governance/runs/[run_id]/[resource]/route.ts", import.meta.url));
const javascript = ts.transpileModule(readFileSync(sourcePath, "utf8"), {
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
      return { NextResponse: { json(body, init = {}) { return { status: init.status ?? 200, body, json: async () => body }; } } };
    }
    if (specifier === "@/lib/orchestrator") return { orchestratorFetch, OrchestratorError };
    throw new Error(`Unexpected route dependency: ${specifier}`);
  },
  TextDecoder,
  URL,
  URLSearchParams,
}, { filename: sourcePath });

function props(resource = "retry") {
  return { params: Promise.resolve({ run_id: RUN_ID, resource }) };
}

function post(body, { resource = "retry", contentType = "application/json" } = {}) {
  return new Request(`http://dashboard.local/api/governance/runs/${RUN_ID}/${resource}`, {
    method: "POST",
    headers: contentType ? { "content-type": contentType } : {},
    body: JSON.stringify(body),
  });
}

function get(resource, query = "") {
  return new Request(`http://dashboard.local/api/governance/runs/${RUN_ID}/${resource}${query}`);
}

beforeEach(() => {
  state.calls = [];
  state.nextData = { run_id: RUN_ID, state: "QUEUED" };
});

describe("Worker Run retry action BFF", () => {
  it("trims and forwards only the bounded retry fields", async () => {
    const response = await routeModule.exports.POST(
      post({ reason: "  operator approved retry  ", idempotency_key: "  stable-key-1  " }),
      props(),
    );

    assert.equal(response.status, 200, JSON.stringify(response.body));
    assert.deepEqual(await response.json(), state.nextData);
    assert.equal(state.calls.length, 1);
    assert.equal(state.calls[0].path, `/workers/runs/${RUN_ID}/retry`);
    assert.equal(state.calls[0].init.method, "POST");
    assert.deepEqual(JSON.parse(state.calls[0].init.body), {
      reason: "operator approved retry",
      idempotency_key: "stable-key-1",
    });
  });

  it("rejects missing, blank, oversized, or unsupported retry input before dispatch", async () => {
    const invalidBodies = [
      { reason: "retry" },
      { reason: "   ", idempotency_key: "key" },
      { reason: "retry", idempotency_key: "   " },
      { reason: "retry", idempotency_key: "key", force: true },
      { reason: "x".repeat(4_001), idempotency_key: "key" },
      { reason: "retry", idempotency_key: "x".repeat(129) },
    ];

    for (const body of invalidBodies) {
      const response = await routeModule.exports.POST(post(body), props());
      assert.equal(response.status, 400);
      assert.equal(state.calls.length, 0);
    }
  });

  it("requires JSON and bounds the action body", async () => {
    const wrongContentType = await routeModule.exports.POST(
      post({ reason: "retry", idempotency_key: "key" }, { contentType: "text/plain" }),
      props(),
    );
    assert.equal(wrongContentType.status, 415);

    const oversized = await routeModule.exports.POST(
      post({ reason: "x".repeat(9_000), idempotency_key: "key" }),
      props(),
    );
    assert.equal(oversized.status, 413);
    assert.equal(state.calls.length, 0);
  });
});

describe("Worker Run evidence BFF", () => {
  it("forwards the bounded tool-effect query through the allowlisted read path", async () => {
    const response = await routeModule.exports.GET(
      get("tool-effects", "?limit=9999&offset=50&payload=ignored"),
      props("tool-effects"),
    );

    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), state.nextData);
    assert.equal(state.calls.length, 1);
    assert.equal(state.calls[0].path, `/workers/runs/${RUN_ID}/tool-effects?limit=200`);
  });

  it("rejects unlisted read resources before dispatch", async () => {
    const response = await routeModule.exports.GET(get("request-payload"), props("request-payload"));

    assert.equal(response.status, 404);
    assert.equal(state.calls.length, 0);
  });
});
