import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const state = { calls: [], nextData: [], error: null };

class OrchestratorError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function orchestratorFetch(path) {
  state.calls.push(path);
  if (state.error) throw state.error;
  return state.nextData;
}

const sourcePath = fileURLToPath(
  new URL("../app/api/companies/[id]/budgets/route.ts", import.meta.url),
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
            return new Response(JSON.stringify(body), {
              status: init.status ?? 200,
              headers: { "Content-Type": "application/json" },
            });
          },
        },
      };
    }
    if (specifier === "@/lib/orchestrator") return { orchestratorFetch, OrchestratorError };
    throw new Error(`Unexpected route dependency: ${specifier}`);
  },
  Response,
}, { filename: sourcePath });

async function invoke(id) {
  return routeModule.exports.GET(new Request("http://dashboard.test/budgets"), {
    params: Promise.resolve({ id }),
  });
}

beforeEach(() => {
  state.calls = [];
  state.nextData = [{ budget_key: "project", limit: "100.00", used: "12.00", available: "88.00" }];
  state.error = null;
});

describe("company budgets BFF", () => {
  it("encodes the company path segment and forwards the canonical budget balances", async () => {
    const response = await invoke("company/alpha space");
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), state.nextData);
    assert.deepEqual(state.calls, ["/companies/company%2Falpha%20space/budgets"]);
  });

  it("preserves canonical upstream error status and message", async () => {
    state.error = new OrchestratorError(404, "company not found");
    const response = await invoke("missing-company");
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { detail: "company not found" });
    assert.deepEqual(state.calls, ["/companies/missing-company/budgets"]);
  });

  it("returns a safe unavailable response for unexpected upstream failures", async () => {
    state.error = new Error("private upstream detail");
    const response = await invoke("company");
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { detail: "Company budget balances are unavailable." });
  });
});
