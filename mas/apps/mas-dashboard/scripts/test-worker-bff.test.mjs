import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const WORKER_ID = "40000000-0000-4000-8000-000000000401";

class OrchestratorError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const state = { calls: [], nextError: null, nextData: { id: WORKER_ID } };

async function orchestratorFetch(path, init) {
  state.calls.push({ path, init });
  if (state.nextError) throw state.nextError;
  return state.nextData;
}

function loadRoute() {
  const sourcePath = fileURLToPath(
    new URL("../app/api/workers/[worker_id]/route.ts", import.meta.url),
  );
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
    },
  }, { filename: sourcePath });
  return routeModule.exports;
}

const route = loadRoute();

function request(body = {}) {
  return { json: async () => body };
}

function routeProps() {
  return { params: Promise.resolve({ worker_id: WORKER_ID }) };
}

async function responseBody(response) {
  return response.json();
}

beforeEach(() => {
  state.calls = [];
  state.nextError = null;
  state.nextData = { id: WORKER_ID };
});

describe("worker configuration BFF", () => {
  it("forwards an authenticated operator update to the canonical worker endpoint", async () => {
    const body = {
      team_id: "dept_engineering",
      adapter_entrypoint: "CodeWorker",
      adapter_module: "workers.code_worker",
    };
    const response = await route.PUT(request(body), routeProps());

    assert.equal(response.status, 200);
    assert.deepEqual(await responseBody(response), state.nextData);
    assert.equal(state.calls.length, 1);
    assert.equal(state.calls[0].path, `/capabilities/workers/${WORKER_ID}`);
    assert.equal(state.calls[0].init.method, "PUT");
    assert.deepEqual(JSON.parse(state.calls[0].init.body), body);
  });

  it("preserves canonical 409 and 422 statuses with bounded public messages", async () => {
    const cases = [
      [409, "Worker configuration conflicts with the current worker state. Refresh and try again."],
      [422, "Worker configuration was rejected by validation. Review the fields and try again."],
    ];
    for (const [status, expectedMessage] of cases) {
      state.nextError = new OrchestratorError(
        status,
        `{"detail":"invalid worker value","debug":"Traceback /srv/private/app.py secret=never-expose"}`,
      );
      const response = await route.PUT(request({ team_id: "bad" }), routeProps());
      const payload = await responseBody(response);

      assert.equal(response.status, status);
      assert.equal(payload.error, expectedMessage);
      assert.deepEqual(Object.keys(payload), ["error"]);
      assert.equal(payload.error.length <= 120, true);
      assert.equal(JSON.stringify(payload).includes("Traceback"), false);
      assert.equal(JSON.stringify(payload).includes("/srv/private"), false);
      assert.equal(JSON.stringify(payload).includes("never-expose"), false);
      state.nextError = null;
    }
  });

  it("preserves authentication and not-found statuses without exposing upstream text", async () => {
    const cases = [
      [401, "Worker service authentication failed."],
      [403, "You are not authorized to change this worker."],
      [404, "Worker not found."],
    ];
    for (const [status, expectedMessage] of cases) {
      state.nextError = new OrchestratorError(status, "internal principal details: key=secret-value");
      const response = await route.PUT(request(), routeProps());
      const payload = await responseBody(response);

      assert.equal(response.status, status);
      assert.equal(payload.error, expectedMessage);
      assert.deepEqual(Object.keys(payload), ["error"]);
      assert.equal(JSON.stringify(payload).includes("secret-value"), false);
      state.nextError = null;
    }
  });

  it("maps upstream and local failures to a safe gateway error", async () => {
    for (const failure of [
      new OrchestratorError(500, "Traceback: sensitive upstream exception"),
      new Error("local socket error with credential=secret-value"),
    ]) {
      state.nextError = failure;
      const response = await route.PUT(request(), routeProps());
      const payload = await responseBody(response);

      assert.equal(response.status, 502);
      assert.equal(payload.error, "Worker request was rejected.");
      assert.deepEqual(Object.keys(payload), ["error"]);
      assert.equal(JSON.stringify(payload).includes("sensitive"), false);
      assert.equal(JSON.stringify(payload).includes("secret-value"), false);
      state.nextError = null;
    }
  });
});
