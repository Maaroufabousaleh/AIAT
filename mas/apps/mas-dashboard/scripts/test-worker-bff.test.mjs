import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { beforeEach, describe, it } from "node:test";
import ts from "typescript";

const WORKER_ID = "40000000-0000-4000-8000-000000000401";
const PROJECT_ID = "30000000-0000-4000-8000-000000000301";
const ISSUE_ID = "30000000-0000-4000-8000-000000000302";
const RUN_ID = "40000000-0000-4000-8000-000000000402";

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

function loadRoute(relativePath = "../app/api/workers/[worker_id]/route.ts") {
  const sourcePath = fileURLToPath(
    new URL(relativePath, import.meta.url),
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
              return {
                status: init.status ?? 200,
                body,
                headers: {
                  get(name) {
                    const match = Object.entries(init.headers ?? {}).find(([key]) => key.toLowerCase() === name.toLowerCase());
                    return match?.[1] ?? null;
                  },
                },
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
    },
    URLSearchParams,
  }, { filename: sourcePath });
  return routeModule.exports;
}

const route = loadRoute();
const runListRoute = loadRoute("../app/api/governance/runs/route.ts");

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

describe("Worker Run list BFF", () => {
  it("projects durable issue correlation without returning the submitted task payload", async () => {
    const taskInput = {
      issue_id: ISSUE_ID,
      title: "private task title",
      description: "private task description",
      prompt: "private worker prompt",
    };
    state.nextData = [{
      id: RUN_ID,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "QUEUED",
      model_resolution_snapshot_id: null,
      created_at: "2026-10-08T17:01:00Z",
      request_json: {
        project_id: PROJECT_ID,
        idempotency_key: `project-issue:${PROJECT_ID}:${ISSUE_ID}:r7`,
        task_input: taskInput,
      },
      result_json: { internal: "result payload" },
    }];

    const response = await runListRoute.GET({
      nextUrl: new URL(`http://dashboard.local/api/governance/runs?project_id=${PROJECT_ID}&limit=50`),
    });
    const payload = await responseBody(response);

    assert.equal(response.status, 200);
    assert.equal(state.calls[0].path, `/workers/runs?limit=50&offset=0&project_id=${PROJECT_ID}`);
    assert.deepEqual(JSON.parse(JSON.stringify(payload)), [{
      id: RUN_ID,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "QUEUED",
      model_resolution_snapshot_id: null,
      created_at: "2026-10-08T17:01:00Z",
      issue_id: ISSUE_ID,
      issue_revision: 7,
    }]);
    assert.equal(JSON.stringify(payload).includes("request_json"), false);
    assert.equal(JSON.stringify(payload).includes("private task"), false);
    assert.equal(JSON.stringify(payload).includes("private worker prompt"), false);
    assert.equal(JSON.stringify(payload).includes("result payload"), false);
    assert.equal(response.headers.get("x-aiat-has-more"), "false");
  });

  it("resolves multi-level retry lineage independently of newest-first response order", async () => {
    const retryRunId = "40000000-0000-4000-8000-000000000405";
    const secondRetryRunId = "40000000-0000-4000-8000-000000000406";
    state.nextData = [{
      id: secondRetryRunId,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "QUEUED",
      retry_of_run_id: retryRunId,
      request_json: {
        project_id: PROJECT_ID,
        idempotency_key: `operator-retry:${retryRunId}:manual-retry-2`,
        task_input: { issue_id: ISSUE_ID, prompt: "private newest prompt" },
      },
      result_json: { internal: "private result" },
    }, {
      id: retryRunId,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "FAILED",
      retry_of_run_id: RUN_ID,
      request_json: {
        project_id: PROJECT_ID,
        idempotency_key: `operator-retry:${RUN_ID}:manual-retry-1`,
        task_input: { issue_id: ISSUE_ID, prompt: "private intermediate prompt" },
      },
    }, {
      id: RUN_ID,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "FAILED",
      request_json: {
        project_id: PROJECT_ID,
        idempotency_key: `project-issue:${PROJECT_ID}:${ISSUE_ID}:r7`,
        task_input: { issue_id: ISSUE_ID, prompt: "private original prompt" },
      },
    }];

    const response = await runListRoute.GET({
      nextUrl: new URL(`http://dashboard.local/api/governance/runs?project_id=${PROJECT_ID}&limit=1000`),
    });
    const payload = await responseBody(response);

    assert.equal(response.status, 200);
    assert.equal(payload.length, 3);
    assert.deepEqual(JSON.parse(JSON.stringify(payload[0])), {
      id: secondRetryRunId,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "QUEUED",
      retry_of_run_id: retryRunId,
      issue_id: ISSUE_ID,
      issue_revision: 7,
    });
    assert.equal(payload[1].issue_id, ISSUE_ID);
    assert.equal(JSON.stringify(payload).includes("private newest prompt"), false);
    assert.equal(JSON.stringify(payload).includes("private intermediate prompt"), false);
    assert.equal(JSON.stringify(payload).includes("private original prompt"), false);
    assert.equal(JSON.stringify(payload).includes("private result"), false);

    state.nextData = [{
      id: secondRetryRunId,
      project_id: PROJECT_ID,
      task_type: "project.issue",
      retry_of_run_id: retryRunId,
      request_json: {
        project_id: PROJECT_ID,
        idempotency_key: `operator-retry:${retryRunId}:manual-retry-2`,
        task_input: { issue_id: ISSUE_ID, prompt: "private paged retry prompt" },
      },
    }];
    const retryOnlyPage = await runListRoute.GET({
      nextUrl: new URL(`http://dashboard.local/api/governance/runs?project_id=${PROJECT_ID}&limit=1&offset=0`),
    });
    const retryOnlyPayload = await responseBody(retryOnlyPage);
    assert.equal(Object.hasOwn(retryOnlyPayload[0], "issue_id"), false);
    assert.equal(retryOnlyPayload[0].retry_issue_id_candidate, ISSUE_ID);
    assert.equal(JSON.stringify(retryOnlyPayload).includes("private paged retry prompt"), false);

    // A lineage edge cannot transfer an issue association across projects.
    state.nextData = [{
      id: secondRetryRunId,
      project_id: "30000000-0000-4000-8000-000000000398",
      task_type: "project.issue",
      retry_of_run_id: RUN_ID,
      request_json: { task_input: { issue_id: ISSUE_ID } },
    }, {
      id: RUN_ID,
      project_id: PROJECT_ID,
      task_type: "project.issue",
      request_json: {
        project_id: PROJECT_ID,
        idempotency_key: `project-issue:${PROJECT_ID}:${ISSUE_ID}:r7`,
        task_input: { issue_id: ISSUE_ID },
      },
    }];
    const crossProject = await runListRoute.GET({
      nextUrl: new URL(`http://dashboard.local/api/governance/runs?project_id=${PROJECT_ID}`),
    });
    const crossProjectPayload = await responseBody(crossProject);
    assert.equal(crossProjectPayload.length, 1);
    assert.equal(crossProjectPayload[0].id, RUN_ID);

    state.nextData = [{
      id: secondRetryRunId,
      project_id: PROJECT_ID,
      task_type: "project.issue",
      retry_of_run_id: RUN_ID,
      request_json: {
        project_id: "30000000-0000-4000-8000-000000000398",
        task_input: { issue_id: ISSUE_ID },
      },
    }, {
      id: RUN_ID,
      project_id: PROJECT_ID,
      task_type: "project.issue",
      request_json: {
        project_id: PROJECT_ID,
        idempotency_key: `project-issue:${PROJECT_ID}:${ISSUE_ID}:r7`,
        task_input: { issue_id: ISSUE_ID },
      },
    }];
    const inconsistentRetry = await runListRoute.GET({
      nextUrl: new URL(`http://dashboard.local/api/governance/runs?project_id=${PROJECT_ID}`),
    });
    const inconsistentRetryPayload = await responseBody(inconsistentRetry);
    assert.equal(Object.hasOwn(inconsistentRetryPayload[0], "issue_id"), false);
  });

  it("does not correlate unrelated, malformed, or cross-project runs", async () => {
    const unrelatedProjectId = "30000000-0000-4000-8000-000000000399";
    state.nextData = [
      {
        id: RUN_ID,
        project_id: PROJECT_ID,
        task_type: "code.edit",
        state: "RUNNING",
        request_json: {
          project_id: PROJECT_ID,
          idempotency_key: `project-issue:${PROJECT_ID}:${ISSUE_ID}:r7`,
          task_input: { issue_id: ISSUE_ID },
        },
      },
      {
        id: "40000000-0000-4000-8000-000000000403",
        project_id: PROJECT_ID,
        task_type: "project.issue",
        state: "RUNNING",
        request_json: {
          project_id: unrelatedProjectId,
          idempotency_key: `project-issue:${PROJECT_ID}:${ISSUE_ID}:r7`,
          task_input: { issue_id: ISSUE_ID },
        },
      },
      {
        id: "40000000-0000-4000-8000-000000000404",
        project_id: PROJECT_ID,
        task_type: "project.issue",
        state: "RUNNING",
        request_json: {
          project_id: PROJECT_ID,
          idempotency_key: `project-issue:${PROJECT_ID}:not-an-issue:r7`,
          task_input: { issue_id: "not-an-issue" },
        },
      },
    ];

    const response = await runListRoute.GET({
      nextUrl: new URL(`http://dashboard.local/api/governance/runs?project_id=${PROJECT_ID}`),
    });
    const payload = await responseBody(response);

    assert.equal(response.status, 200);
    assert.equal(payload.length, 3);
    assert.equal(payload.some((run) => Object.hasOwn(run, "issue_id")), false);
    assert.equal(payload.some((run) => Object.hasOwn(run, "request_json")), false);
  });

  it("rejects malformed upstream data and invalid project filters", async () => {
    state.nextData = { rows: [] };
    const malformed = await runListRoute.GET({
      nextUrl: new URL("http://dashboard.local/api/governance/runs"),
    });
    assert.equal(malformed.status, 502);
    assert.deepEqual(JSON.parse(JSON.stringify(await responseBody(malformed))), { error: "Worker Run list returned an invalid response." });

    const invalidProject = await runListRoute.GET({
      nextUrl: new URL("http://dashboard.local/api/governance/runs?project_id=not-a-uuid"),
    });
    assert.equal(invalidProject.status, 400);

    const tooLarge = await runListRoute.GET({
      nextUrl: new URL(`http://dashboard.local/api/governance/runs?limit=1001`),
    });
    assert.equal(tooLarge.status, 400);
    for (const invalidOffset of ["-1", "1.5", "100001", "9007199254740992"]) {
      const invalid = await runListRoute.GET({
        nextUrl: new URL(`http://dashboard.local/api/governance/runs?offset=${invalidOffset}`),
      });
      assert.equal(invalid.status, 400);
    }
    state.nextData = [];
    const maximum = await runListRoute.GET({
      nextUrl: new URL("http://dashboard.local/api/governance/runs?limit=1000&offset=100000"),
    });
    assert.equal(maximum.status, 200);
    assert.equal(state.calls.at(-1).path, "/workers/runs?limit=1000&offset=100000");

    state.nextData = [{ id: RUN_ID, project_id: PROJECT_ID, state: "FAILED" }, {
      id: "40000000-0000-4000-8000-000000000407",
      project_id: PROJECT_ID,
      state: "SUCCEEDED",
    }];
    const nextPage = await runListRoute.GET({
      nextUrl: new URL("http://dashboard.local/api/governance/runs?limit=2&offset=1200"),
    });
    assert.equal(nextPage.status, 200);
    assert.equal(state.calls.at(-1).path, "/workers/runs?limit=2&offset=1200");
    assert.equal(nextPage.headers.get("x-aiat-has-more"), "true");
  });
});
