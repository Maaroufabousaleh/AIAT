import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const PROJECT_ID = "30000000-0000-4000-8000-000000000401";

class OrchestratorError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const state = { calls: [], nextError: null, nextData: { ok: true } };

async function orchestratorFetch(path, init) {
  state.calls.push({ path, init });
  if (state.nextError) throw state.nextError;
  return state.nextData;
}

function loadRoute(relativePath) {
  const sourcePath = fileURLToPath(new URL(relativePath, import.meta.url));
  const javascript = ts.transpileModule(readFileSync(sourcePath, "utf8"), {
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
            return {
              status: init.status ?? 200,
              body,
              json: async () => body,
            };
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
  }, { filename: sourcePath });
  return routeModule.exports;
}

const route = loadRoute("../app/api/projects/[id]/transition/route.ts");
const props = { params: Promise.resolve({ id: PROJECT_ID }) };

function request(body) {
  return new Request(`http://dashboard.local/api/projects/${PROJECT_ID}/transition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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

describe("project transition BFF", () => {
  it("preserves only the bounded evidence-incomplete error projection", async () => {
    const upstreamDetail = {
      code: "PROJECT_EVIDENCE_INCOMPLETE",
      message: "upstream message is replaced with the canonical UI copy",
      policy_id: "software_delivery",
      policy_version: "1.0",
      evidence_status: "incomplete",
      failed_check_names: ["flow_terminal", "documents_exist"],
      failed_check_count: 2,
      failed_check_names_truncated: false,
      internal_debug: "must not cross the BFF boundary",
    };
    state.nextError = new OrchestratorError(
      409,
      JSON.stringify({ detail: upstreamDetail }),
    );

    const response = await result(await route.POST(
      request({ event: "kpi_saved" }),
      props,
    ));

    assert.equal(response.status, 409);
    assert.deepEqual(JSON.parse(JSON.stringify(response.body)), {
      detail: {
        code: "PROJECT_EVIDENCE_INCOMPLETE",
        message: "Project cannot be completed until its required evidence is complete.",
        policy_id: "software_delivery",
        policy_version: "1.0",
        evidence_status: "incomplete",
        failed_check_names: ["flow_terminal", "documents_exist"],
        failed_check_count: 2,
        failed_check_names_truncated: false,
      },
    });
    assert.equal(state.calls.length, 1);
    assert.equal(state.calls[0].path, `/projects/${PROJECT_ID}/transition`);
    assert.deepEqual(JSON.parse(state.calls[0].init.body), {
      event: "kpi_saved",
      actor_id: "dashboard-operator",
    });
  });

  it("forwards only canonical document lifecycle context and injects the operator actor", async () => {
    const response = await result(await route.POST(
      request({
        event: "pdr_submitted",
        context: {
          document_id: "30000000-0000-4000-8000-000000000402",
          doc_type: "PDR",
        },
      }),
      props,
    ));
    assert.equal(response.status, 200);
    assert.deepEqual(JSON.parse(state.calls[0].init.body), {
      event: "pdr_submitted",
      actor_id: "dashboard-operator",
      context: {
        document_id: "30000000-0000-4000-8000-000000000402",
        doc_type: "PDR",
      },
    });

    const nullContext = await result(await route.POST(
      request({ event: "kpi_saved", context: null }),
      props,
    ));
    assert.equal(nullContext.status, 200);
    assert.deepEqual(JSON.parse(state.calls[1].init.body), {
      event: "kpi_saved",
      actor_id: "dashboard-operator",
    });
  });

  it("rejects caller authority or policy fields and hides arbitrary upstream error bodies", async () => {
    const invalidBodies = [
      { event: "kpi_saved", evidence_policy: { policy_id: "manual" } },
      { event: "kpi_saved", actor_id: "forged-actor" },
      { event: "kpi_saved", context: { evidence_status: "complete" } },
      { event: "kpi_saved", context: { document_id: "not-a-uuid" } },
      { event: "pdr_submitted", context: { document_id: "30000000-0000-4000-8000-000000000402", doc_type: "CDR" } },
    ];
    for (const body of invalidBodies) {
      const invalid = await result(await route.POST(request(body), props));
      assert.equal(invalid.status, 400);
    }
    assert.equal(state.calls.length, 0);

    state.nextError = new OrchestratorError(
      503,
      JSON.stringify({ detail: "private upstream exception: token=do-not-expose" }),
    );
    const failed = await result(await route.POST(request({ event: "kpi_saved" }), props));
    assert.equal(failed.status, 503);
    assert.deepEqual(JSON.parse(JSON.stringify(failed.body)), { error: "Project transition failed." });
    assert.doesNotMatch(JSON.stringify(failed.body), /private upstream|do-not-expose|token=/);
  });
});
