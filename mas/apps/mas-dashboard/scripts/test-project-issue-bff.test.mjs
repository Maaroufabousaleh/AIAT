import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const PROJECT_ID = "30000000-0000-4000-8000-000000000301";
const ISSUE_ID = "30000000-0000-4000-8000-000000000302";

class OrchestratorError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const state = {
  calls: [],
  nextError: null,
  nextData: { ok: true },
};

async function orchestratorFetch(path, init) {
  state.calls.push({ path, init });
  if (state.nextError) throw state.nextError;
  return state.nextData;
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
  const localRequire = (specifier) => {
    if (specifier === "next/server") {
      return {
        NextResponse: {
          json(body, init = {}) {
            return { status: init.status ?? 200, body, json: async () => body };
          },
        },
      };
    }
    if (specifier === "@/lib/orchestrator") {
      return { orchestratorFetch, OrchestratorError };
    }
    throw new Error(`Unexpected route dependency: ${specifier}`);
  };

  runInNewContext(javascript, {
    exports: routeModule.exports,
    module: routeModule,
    require: localRequire,
    URL,
  }, { filename: sourcePath });
  return routeModule.exports;
}

const collectionRoute = loadRoute("../app/api/projects/[id]/issues/route.ts");
const detailRoute = loadRoute("../app/api/projects/[id]/issues/[issue_id]/route.ts");
const commentsRoute = loadRoute("../app/api/projects/[id]/issues/[issue_id]/comments/route.ts");

function request(path, method = "GET", body) {
  return new Request(`http://dashboard.local${path}`, {
    method,
    ...(body === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  });
}

async function result(response) {
  return { status: response.status, body: await response.json() };
}

beforeEach(() => {
  state.calls = [];
  state.nextError = null;
  state.nextData = { ok: true };
});

describe("project issue collection BFF", () => {
  it("validates project UUIDs, list bounds, and create fields before forwarding", async () => {
    assert.equal((await result(await collectionRoute.GET(
      request("/api/projects/not-a-uuid/issues"), { params: Promise.resolve({ id: "not-a-uuid" }) },
    ))).status, 400);

    assert.equal((await result(await collectionRoute.GET(
      request("/api/projects/issues?limit=101"), { params: Promise.resolve({ id: PROJECT_ID }) },
    ))).status, 400);

    const invalidBodies = [
      { title: "valid title", actor_id: "spoofed" },
      { title: "   " },
      { title: "x".repeat(501) },
      { title: "valid title", estimated_hours: -1 },
    ];
    for (const body of invalidBodies) {
      assert.equal((await result(await collectionRoute.POST(
        request(`/api/projects/${PROJECT_ID}/issues`, "POST", body),
        { params: Promise.resolve({ id: PROJECT_ID }) },
      ))).status, 400);
    }
    assert.equal(state.calls.length, 0);
  });

  it("forwards a valid bounded list request and a typed issue create", async () => {
    const list = await result(await collectionRoute.GET(
      request(`/api/projects/${PROJECT_ID}/issues?limit=25`),
      { params: Promise.resolve({ id: PROJECT_ID }) },
    ));
    assert.equal(list.status, 200);
    assert.equal(state.calls[0].path, `/projects/${PROJECT_ID}/issues?limit=25`);

    const body = { title: "Implement issue BFF tests", issue_type: "test", priority: "high" };
    const created = await result(await collectionRoute.POST(
      request(`/api/projects/${PROJECT_ID}/issues`, "POST", body),
      { params: Promise.resolve({ id: PROJECT_ID }) },
    ));
    assert.equal(created.status, 201);
    assert.equal(state.calls[1].path, `/projects/${PROJECT_ID}/issues`);
    assert.deepEqual(JSON.parse(state.calls[1].init.body), body);
  });
});

describe("project issue detail and update BFF", () => {
  it("validates both route UUIDs and delegates scoped ownership to the canonical API", async () => {
    const invalid = await result(await detailRoute.GET(
      request(`/api/projects/not-a-uuid/issues/${ISSUE_ID}`),
      { params: Promise.resolve({ id: "not-a-uuid", issue_id: ISSUE_ID }) },
    ));
    assert.equal(invalid.status, 400);
    assert.equal(state.calls.length, 0);

    state.nextError = new OrchestratorError(404, "issue not found for project");
    const mismatch = await result(await detailRoute.GET(
      request(`/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}`),
      { params: Promise.resolve({ id: PROJECT_ID, issue_id: ISSUE_ID }) },
    ));
    assert.equal(mismatch.status, 404);
    assert.equal(mismatch.body.error, "issue not found for project");
    assert.equal(state.calls[0].path, `/projects/${PROJECT_ID}/issues/${ISSUE_ID}`);
  });

  it("requires a positive expected_revision and forwards it unchanged", async () => {
    for (const body of [
      { title: "Updated" },
      { title: "Updated", expected_revision: null },
      { title: "Updated", expected_revision: 0 },
      { title: "Updated", expected_revision: 1.5 },
      { title: "Updated", expected_revision: 1, actor_id: "spoofed" },
    ]) {
      const rejected = await result(await detailRoute.PATCH(
        request(`/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}`, "PATCH", body),
        { params: Promise.resolve({ id: PROJECT_ID, issue_id: ISSUE_ID }) },
      ));
      assert.equal(rejected.status, 400);
    }
    assert.equal(state.calls.length, 0);

    const body = { status: "in_review", expected_revision: 7 };
    const updated = await result(await detailRoute.PATCH(
      request(`/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}`, "PATCH", body),
      { params: Promise.resolve({ id: PROJECT_ID, issue_id: ISSUE_ID }) },
    ));
    assert.equal(updated.status, 200);
    assert.equal(state.calls[0].path, `/projects/${PROJECT_ID}/issues/${ISSUE_ID}`);
    assert.equal(state.calls[0].init.method, "PATCH");
    assert.deepEqual(JSON.parse(state.calls[0].init.body), body);
  });

  it("preserves canonical 404 ownership checks on updates", async () => {
    state.nextError = new OrchestratorError(404, "issue not found for project");
    const mismatch = await result(await detailRoute.PATCH(
      request(`/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}`, "PATCH", { expected_revision: 2 }),
      { params: Promise.resolve({ id: PROJECT_ID, issue_id: ISSUE_ID }) },
    ));
    assert.equal(mismatch.status, 404);
    assert.equal(state.calls[0].path, `/projects/${PROJECT_ID}/issues/${ISSUE_ID}`);
  });
});

describe("project issue comment BFF", () => {
  it("rejects browser actor attribution and forwards a fixed operator actor", async () => {
    for (const body of [
      { body: "hello", actor_id: "browser-supplied" },
      { body: "hello", run_id: ISSUE_ID },
      { body: "   " },
      { body: "x".repeat(100_001) },
    ]) {
      const rejected = await result(await commentsRoute.POST(
        request(`/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}/comments`, "POST", body),
        { params: Promise.resolve({ id: PROJECT_ID, issue_id: ISSUE_ID }) },
      ));
      assert.equal(rejected.status, 400);
    }
    assert.equal(state.calls.length, 0);

    const created = await result(await commentsRoute.POST(
      request(`/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}/comments`, "POST", { body: "  reviewed  " }),
      { params: Promise.resolve({ id: PROJECT_ID, issue_id: ISSUE_ID }) },
    ));
    assert.equal(created.status, 201);
    assert.equal(state.calls[0].path, `/projects/${PROJECT_ID}/issues/${ISSUE_ID}/comments`);
    assert.equal(state.calls[0].init.method, "POST");
    assert.deepEqual(JSON.parse(state.calls[0].init.body), {
      body: "reviewed",
      actor_id: "dashboard-operator",
    });
  });

  it("preserves canonical 404 ownership checks on comments", async () => {
    state.nextError = new OrchestratorError(404, "issue not found for project");
    const mismatch = await result(await commentsRoute.POST(
      request(`/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}/comments`, "POST", { body: "comment" }),
      { params: Promise.resolve({ id: PROJECT_ID, issue_id: ISSUE_ID }) },
    ));
    assert.equal(mismatch.status, 404);
    assert.equal(state.calls[0].path, `/projects/${PROJECT_ID}/issues/${ISSUE_ID}/comments`);
  });
});
