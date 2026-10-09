import { expect, test, type Page, type Route } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "10000000-0000-4000-8000-000000000001";
const ISSUE_ID = "30000000-0000-4000-8000-000000000003";
const WORKER_ID = "20000000-0000-4000-8000-000000000002";
const RUN_ID = "40000000-0000-4000-8000-000000000004";
const ACTION_PATH = "/api/executive/actions/cto/worker-runs";

const issue = {
  issue_id: ISSUE_ID,
  project_id: PROJECT_ID,
  title: "Fix the project health check",
  description: "Update the local health check and add a focused regression test.",
  status: "open",
  issue_type: "bug",
  priority: "high",
  revision: 7,
};

const activeWorker = {
  id: WORKER_ID,
  worker_id: "opencode-e2e-worker",
  name: "OpenCode E2E worker",
  status: "ACTIVE",
  active_adapter_id: "50000000-0000-4000-8000-000000000005",
  adapter_type: "opencode",
};

const gatewayWorker = {
  ...activeWorker,
  worker_id: "gateway-e2e-worker",
  name: "Gateway E2E worker",
  adapter_type: "aiat_gateway",
  transport_mode: "aiat_gateway",
};

async function json(route: Route, status: number, payload: unknown, headers: Record<string, string> = {}): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers,
    body: JSON.stringify(payload),
  });
}

async function installProjectMocks(
  page: Page,
  workers: unknown[],
  onDispatch: (route: Route) => Promise<void>,
  options: {
    pendingDecisions?: unknown[];
    onDecision?: (route: Route) => Promise<void>;
    runs?: (url: URL) => unknown[] | Promise<unknown[]>;
    onRunsRequest?: (url: URL) => void;
    getIssue?: () => typeof issue;
    onIssueUpdate?: (body: Record<string, unknown>) => void;
  } = {},
): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === ACTION_PATH && request.method() === "POST") {
      await onDispatch(route);
      return;
    }
    if (path === "/api/workers" && request.method() === "GET") {
      await json(route, 200, workers);
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/decisions`) {
      if (request.method() === "POST") {
        if (options.onDecision) {
          await options.onDecision(route);
        } else {
          await json(route, 200, { status: "decision_recorded" });
        }
      } else {
        await json(route, 200, options.pendingDecisions ?? []);
      }
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}`) {
      await json(route, 200, {
        id: PROJECT_ID,
        name: "Issue dispatch E2E project",
        description: "Project fixture for the mocked governed worker action.",
        state: "ACTIVE",
        created_at: "2026-10-07T17:00:00Z",
        updated_at: "2026-10-07T17:00:00Z",
        company_id: "60000000-0000-4000-8000-000000000006",
      });
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/state-history` ||
        path === `/api/projects/${PROJECT_ID}/transition` ||
        path === `/api/projects/${PROJECT_ID}/context` ||
        path === `/api/projects/${PROJECT_ID}/evidence` ||
        path === `/api/projects/${PROJECT_ID}/issues?limit=100`) {
      await json(route, 200, path.endsWith("/issues") ? [issue] : []);
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/workspace`) {
      await json(route, 200, { project_id: PROJECT_ID, recent_activity: [], pending_approvals: [], artifacts: [] });
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/repository`) {
      await json(route, 200, { workspace: null });
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/flow-instance`) {
      await json(route, 404, { detail: "No flow is attached to this fixture." });
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/evidence/package`) {
      await json(route, 200, {});
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/issues`) {
      await json(route, 200, [options.getIssue?.() ?? issue]);
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}`) {
      if (request.method() === "PATCH") {
        options.onIssueUpdate?.(request.postDataJSON() as Record<string, unknown>);
      }
      await json(route, 200, { issue: options.getIssue?.() ?? issue, comments: [], links: [] });
      return;
    }
    if (path === "/api/governance/runs") {
      options.onRunsRequest?.(url);
      const runs = await (options.runs?.(url) ?? []);
      const limit = Number(url.searchParams.get("limit") ?? "50");
      await json(route, 200, runs, { "X-AIAT-Has-More": String(runs.length === limit) });
      return;
    }
    if (path === "/api/flows") {
      await json(route, 200, []);
      return;
    }

    // Keep this mocked UI test hermetic: unexpected dashboard reads get an
    // empty response, and no request can fall through to a live worker/provider.
    await json(route, 200, []);
  });
}

test("the CTO worker action BFF requires a signed dashboard session", async ({ request }) => {
  const response = await request.post(ACTION_PATH, {
    data: {},
    headers: { "Content-Type": "application/json" },
  });

  expect(response.status()).toBe(401);
  await expect(response.json()).resolves.toMatchObject({ error: "Unauthorized" });
});

test("issue dispatch uses the audited CTO action, keeps retry idempotent, and links the run evidence", async ({ page }) => {
  const submissions: Array<Record<string, unknown>> = [];
  const durableRuns: Array<Record<string, unknown>> = [];
  let currentIssue = { ...issue };
  let releaseFirstDispatch!: () => void;
  let markFirstDispatchSeen!: () => void;
  const firstDispatchSeen = new Promise<void>((resolve) => { markFirstDispatchSeen = resolve; });
  const firstDispatchResponse = new Promise<void>((resolve) => { releaseFirstDispatch = resolve; });

  await installProjectMocks(page, [activeWorker], async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    submissions.push(body);
    if (submissions.length === 1) {
      markFirstDispatchSeen();
      await firstDispatchResponse;
      await json(route, 503, {
        error: JSON.stringify({ detail: { code: "WORKER_NOT_READY", message: "Worker readiness check failed; review the worker's active steward and retry." } }),
      });
      return;
    }
    durableRuns.push({
      id: RUN_ID,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "QUEUED",
      issue_id: ISSUE_ID,
      issue_revision: 7,
      created_at: "2026-10-08T17:01:00Z",
    }, {
      id: "40000000-0000-4000-8000-000000000099",
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "RUNNING",
      issue_id: "30000000-0000-4000-8000-000000000099",
      issue_revision: 7,
      created_at: "2026-10-08T17:02:00Z",
    });
    await json(route, 202, {
      schema_version: "aiat.executive-action.v1",
      role: "cto",
      action: "dispatch_worker_run",
      requested_by: "office_cto",
      authenticated_principal: "operator",
      result: { run_id: RUN_ID, state: "QUEUED", dispatch_mode: "queued" },
      evidence: { kind: "worker_run", project_id: PROJECT_ID, worker_id: WORKER_ID, run_id: RUN_ID },
    });
  }, {
    runs: () => durableRuns,
    getIssue: () => currentIssue,
    onIssueUpdate: (body) => {
      currentIssue = {
        ...currentIssue,
        title: String(body.title ?? currentIssue.title),
        description: typeof body.description === "string" ? body.description : currentIssue.description,
        revision: currentIssue.revision + 1,
      };
    },
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("button", { name: "Manage issue" }).click();
  await expect(page.getByRole("heading", { name: "Manage project issue" })).toBeVisible();
  await page.getByLabel("Active worker with a selected adapter").selectOption(WORKER_ID);
  await page.getByRole("checkbox", { name: /may spend the project budget and send the issue/i }).check();

  const startButton = page.getByRole("button", { name: "Start work" });
  await expect(startButton).toBeEnabled();
  await startButton.click();
  await firstDispatchSeen;
  const pendingButton = page.getByRole("button", { name: /Starting work/ });
  await expect(pendingButton).toBeDisabled();

  // A duplicate event while the first request is pending is stopped by the
  // in-flight idempotency lock. This synthetic event never reaches the API.
  await pendingButton.dispatchEvent("click");
  expect(submissions).toHaveLength(1);
  releaseFirstDispatch();

  await expect(page.getByText("Worker readiness check failed", { exact: false })).toBeVisible();
  await expect(startButton).toBeEnabled();
  await startButton.click();
  await expect.poll(() => submissions.length).toBe(2);

  const expectedKey = `project-issue:${PROJECT_ID}:${ISSUE_ID}:r7`;
  expect(submissions[0]).toMatchObject({
    requested_by: "office_cto",
    dispatch: {
      worker_id: WORKER_ID,
      project_id: PROJECT_ID,
      idempotency_key: expectedKey,
      task_type: "project.issue",
      task_input: {
        issue_id: ISSUE_ID,
        title: issue.title,
        description: issue.description,
      },
    },
  });
  const opencodeDispatch = submissions[0].dispatch as { task_input: Record<string, unknown> };
  expect(opencodeDispatch.task_input.prompt).toEqual(expect.any(String));
  expect(submissions[1]).toEqual(submissions[0]);

  const result = page.getByRole("status", { name: "Worker Run result" });
  await expect(result).toContainText("QUEUED");
  const runLink = result.getByRole("link", { name: `View run and evidence ${RUN_ID}` });
  await expect(runLink).toHaveAttribute("href", `/workers/runs/${RUN_ID}`);
  await expect(page.getByRole("button", { name: "Work already started" })).toBeDisabled();
  expect(submissions).toHaveLength(2);

  // The issue/run association comes from the canonical project run read, so
  // it survives a page reload and does not attach another issue's run.
  await page.reload();
  await page.getByRole("button", { name: "Manage issue" }).click();
  const persistedResult = page.getByRole("status", { name: "Worker Run result" });
  await expect(persistedResult).toContainText("QUEUED");
  await expect(persistedResult.getByRole("link", { name: `View run and evidence ${RUN_ID}` }))
    .toHaveAttribute("href", `/workers/runs/${RUN_ID}`);
  await expect(persistedResult.getByRole("link", { name: /40000000-0000-4000-8000-000000000099/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Work already started" })).toBeDisabled();

  // An old run remains inspectable after issue edits, but is never presented
  // as work for the new issue revision or used to suppress the new revision's
  // dispatch action.
  await page.getByLabel("Title").fill("Update the project health check safely");
  await page.getByRole("button", { name: "Save issue" }).click();
  await expect(page.getByText("revision 8", { exact: false })).toBeVisible();
  const priorRevisionResult = page.getByRole("status", { name: "Worker Run result" });
  await expect(priorRevisionResult).toContainText("prior issue revision 7");
  await expect(priorRevisionResult).toContainText("current issue revision is 8");
  await expect(priorRevisionResult.getByRole("link", { name: `View run and evidence ${RUN_ID}` })).toBeVisible();
  const revisedIssueStartButton = page.getByRole("button", { name: "Start work" });
  await expect(revisedIssueStartButton).toBeDisabled();
  await page.getByLabel("Active worker with a selected adapter").selectOption(WORKER_ID);
  await page.getByRole("checkbox", { name: /may spend the project budget and send the issue/i }).check();
  await expect(revisedIssueStartButton).toBeEnabled();
});

test("an issue retry remains linked to its issue after reload", async ({ page }) => {
  const retryRunId = "40000000-0000-4000-8000-000000000405";
  const durableRuns = [
    {
      id: retryRunId,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "QUEUED",
      retry_of_run_id: RUN_ID,
      issue_id: ISSUE_ID,
      issue_revision: 7,
      created_at: "2026-10-08T17:03:00Z",
    },
    {
      id: RUN_ID,
      project_id: PROJECT_ID,
      worker_id: WORKER_ID,
      task_type: "project.issue",
      state: "FAILED",
      issue_id: ISSUE_ID,
      issue_revision: 7,
      created_at: "2026-10-08T17:01:00Z",
    },
  ];
  await installProjectMocks(page, [activeWorker], async () => {
    throw new Error("This read-only retry association test must not dispatch work.");
  }, { runs: () => durableRuns });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("button", { name: "Manage issue" }).click();
  const result = page.getByRole("status", { name: "Worker Run result" });
  await expect(result).toContainText("QUEUED");
  await expect(result.getByRole("link", { name: `View run and evidence ${retryRunId}` }))
    .toHaveAttribute("href", `/workers/runs/${retryRunId}`);
  await expect(page.getByRole("button", { name: "Work already started" })).toBeDisabled();
  await page.reload();
  await page.getByRole("button", { name: "Manage issue" }).click();
  const persistedResult = page.getByRole("status", { name: "Worker Run result" });
  await expect(persistedResult.getByRole("link", { name: `View run and evidence ${retryRunId}` })).toBeVisible();
});

test("project activity loads a bounded run window and displays only recent runs", async ({ page }) => {
  const runLimitRequests: string[] = [];
  const runs = Array.from({ length: 55 }, (_, index) => ({
    id: `40000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    project_id: PROJECT_ID,
    worker_id: WORKER_ID,
    task_type: "code.edit",
    state: "COMPLETED",
    created_at: new Date(Date.UTC(2026, 9, 8, 17, index)).toISOString(),
  }));
  await installProjectMocks(page, [activeWorker], async () => {
    throw new Error("The run-window UI test must not dispatch work.");
  }, {
    runs: () => runs,
    onRunsRequest: (url) => runLimitRequests.push(url.searchParams.get("limit") ?? ""),
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  const runList = page.getByRole("list", { name: "Project worker runs" });
  await expect(runList.getByRole("listitem")).toHaveCount(50);
  await expect(page.getByText("Showing 1–50 of 55 loaded runs.", { exact: true })).toBeVisible();
  expect(runLimitRequests).toContain("100");
  await page.getByRole("button", { name: "Show older runs" }).click();
  await expect(runList.getByRole("listitem")).toHaveCount(5);
  await expect(page.getByText("Showing 51–55 of 55 loaded runs.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Show newer runs" }).click();
  await expect(runList.getByRole("listitem")).toHaveCount(50);
});

test("a delayed older run page cannot overwrite a refreshed project history", async ({ page }) => {
  let releaseOlderPage!: () => void;
  let markOlderPageSeen!: () => void;
  const olderPageReleased = new Promise<void>((resolve) => { releaseOlderPage = resolve; });
  const olderPageSeen = new Promise<void>((resolve) => { markOlderPageSeen = resolve; });
  let firstPageRequest = 0;
  const createRuns = (start: number, count: number) => Array.from({ length: count }, (_, index) => ({
    id: `40000000-0000-4000-8000-${String(start + index).padStart(12, "0")}`,
    project_id: PROJECT_ID,
    worker_id: WORKER_ID,
    task_type: "code.edit",
    state: "COMPLETED",
    created_at: new Date(Date.UTC(2026, 9, 8, 17, index)).toISOString(),
  }));

  await installProjectMocks(page, [activeWorker], async () => {
    throw new Error("The history refresh test must not dispatch work.");
  }, {
    runs: async (url) => {
      const offset = Number(url.searchParams.get("offset") ?? "0");
      if (offset === 100) {
        markOlderPageSeen();
        await olderPageReleased;
        return createRuns(2000, 100);
      }
      firstPageRequest += 1;
      return firstPageRequest === 1 ? createRuns(1000, 100) : createRuns(3000, 20);
    },
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  const runList = page.getByRole("list", { name: "Project worker runs" });
  await expect(runList.getByRole("listitem")).toHaveCount(50);
  await page.getByRole("button", { name: "Show older runs" }).click();
  await page.getByRole("button", { name: "Load older runs" }).click();
  await olderPageSeen;

  await page.getByRole("button", { name: "Refresh project data" }).click();
  await expect(runList.getByRole("listitem")).toHaveCount(20);
  await expect(page.getByText("Showing 1–20 of 20 loaded runs.", { exact: true })).toBeVisible();

  releaseOlderPage();
  await expect(runList.getByRole("listitem")).toHaveCount(20);
  await expect(page.getByRole("button", { name: "Load older runs" })).toHaveCount(0);
  await expect(runList).not.toContainText("000000002000");
});

test("load older run pages past 1000 and recover cross-page issue retry links after page load", async ({ page }) => {
  const runLimitRequests: string[] = [];
  const intermediateRetryId = "40000000-0000-4000-8000-000000000450";
  const latestRetryId = "40000000-0000-4000-8000-000000000451";
  const originalRunId = "40000000-0000-4000-8000-000000000452";
  await installProjectMocks(page, [activeWorker], async () => {
    throw new Error("The run-history test must not dispatch work.");
  }, {
    onRunsRequest: (url) => runLimitRequests.push(url.searchParams.get("offset") ?? "0"),
    runs: (url) => {
      const offset = Number(url.searchParams.get("offset") ?? "0");
      if (offset === 0) {
        return [{
          id: latestRetryId,
          project_id: PROJECT_ID,
          worker_id: WORKER_ID,
          task_type: "project.issue",
          state: "QUEUED",
          retry_of_run_id: intermediateRetryId,
          retry_issue_id_candidate: ISSUE_ID,
          created_at: "2026-10-08T17:03:00Z",
        }, ...Array.from({ length: 99 }, (_, index) => ({
          id: `40000000-0000-4000-8000-${String(index + 1000).padStart(12, "0")}`,
          project_id: PROJECT_ID,
          worker_id: WORKER_ID,
          task_type: "code.edit",
          state: "COMPLETED",
          created_at: new Date(Date.UTC(2026, 9, 8, 17, index)).toISOString(),
        }))];
      }
      if (offset === 100) {
        return [{
          id: intermediateRetryId,
          project_id: PROJECT_ID,
          worker_id: WORKER_ID,
          task_type: "project.issue",
          state: "FAILED",
          retry_of_run_id: originalRunId,
          retry_issue_id_candidate: ISSUE_ID,
          created_at: "2026-10-08T17:02:00Z",
        }, ...Array.from({ length: 99 }, (_, index) => ({
          id: `40000000-0000-4000-8000-${String(index + 2000).padStart(12, "0")}`,
          project_id: PROJECT_ID,
          worker_id: WORKER_ID,
          task_type: "code.edit",
          state: "COMPLETED",
          created_at: new Date(Date.UTC(2026, 9, 8, 16, index)).toISOString(),
        }))];
      }
      if (offset < 1_000) {
        return Array.from({ length: 100 }, (_, index) => ({
          id: `40000000-0000-4000-8000-${String(offset + index + 3000).padStart(12, "0")}`,
          project_id: PROJECT_ID,
          worker_id: WORKER_ID,
          task_type: "code.edit",
          state: "COMPLETED",
          created_at: new Date(Date.UTC(2026, 9, 7, 17, index)).toISOString(),
        }));
      }
      if (offset === 1_000) {
        return [{
          id: originalRunId,
          project_id: PROJECT_ID,
          worker_id: WORKER_ID,
          task_type: "project.issue",
          state: "FAILED",
          issue_id: ISSUE_ID,
          issue_revision: issue.revision,
          created_at: "2026-10-07T17:00:00Z",
        }];
      }
      return [];
    },
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("button", { name: "Manage issue" }).click();
  await expect(page.getByRole("status", { name: "Worker Run result" })).toHaveCount(0);
  await page.getByRole("button", { name: "Close issue details" }).click();

  for (let pageIndex = 0; pageIndex < 10; pageIndex += 1) {
    await page.getByRole("button", { name: "Show older runs" }).click();
    await page.getByRole("button", { name: "Load older runs" }).click();
  }
  expect(runLimitRequests).toContain("1000");
  const runList = page.getByRole("list", { name: "Project worker runs" });
  await expect(runList.getByRole("listitem")).toHaveCount(1);
  await expect(page.getByText("Showing 1001–1001 of 1001 loaded runs.", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Manage issue" }).click();
  const linkedResult = page.getByRole("status", { name: "Worker Run result" });
  await expect(linkedResult).toContainText("QUEUED");
  await expect(linkedResult.getByRole("link", { name: `View run and evidence ${latestRetryId}` }))
    .toHaveAttribute("href", `/workers/runs/${latestRetryId}`);

  await page.getByRole("button", { name: "Close issue details" }).click();
  await page.reload();
  await page.getByRole("button", { name: "Manage issue" }).click();
  await expect(page.getByRole("status", { name: "Worker Run result" })).toHaveCount(0);
  await page.getByRole("button", { name: "Close issue details" }).click();
  for (let pageIndex = 0; pageIndex < 10; pageIndex += 1) {
    await page.getByRole("button", { name: "Show older runs" }).click();
    await page.getByRole("button", { name: "Load older runs" }).click();
  }
  await page.getByRole("button", { name: "Manage issue" }).click();
  const reloadedLinkedResult = page.getByRole("status", { name: "Worker Run result" });
  await expect(reloadedLinkedResult.getByRole("link", { name: `View run and evidence ${latestRetryId}` }))
    .toHaveAttribute("href", `/workers/runs/${latestRetryId}`);
});

test("Gateway-backed issue dispatch sends a bounded project-scoped prompt", async ({ page }) => {
  const submissions: Record<string, unknown>[] = [];
  await installProjectMocks(page, [gatewayWorker], async (route) => {
    submissions.push(route.request().postDataJSON() as Record<string, unknown>);
    await json(route, 202, {
      schema_version: "aiat.executive-action.v1",
      role: "cto",
      action: "dispatch_worker_run",
      requested_by: "office_cto",
      authenticated_principal: "operator",
      result: { run_id: RUN_ID, state: "QUEUED", dispatch_mode: "queued" },
      evidence: { kind: "worker_run", project_id: PROJECT_ID, worker_id: WORKER_ID, run_id: RUN_ID },
    });
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("button", { name: "Manage issue" }).click();
  await page.getByLabel("Active worker with a selected adapter").selectOption(WORKER_ID);
  await page.getByRole("checkbox", { name: /may spend the project budget and send the issue/i }).check();
  await page.getByRole("button", { name: "Start work" }).click();

  await expect(page.getByRole("status", { name: "Worker Run result" })).toContainText("QUEUED");
  expect(submissions).toHaveLength(1);
  const submitted = submissions[0] as Record<string, unknown>;
  const dispatch = submitted.dispatch as { task_input: Record<string, unknown> };
  expect(submitted).toMatchObject({
    requested_by: "office_cto",
    dispatch: {
      worker_id: WORKER_ID,
      project_id: PROJECT_ID,
      task_type: "project.issue",
      task_input: {
        issue_id: ISSUE_ID,
        title: issue.title,
        description: issue.description,
      },
    },
  });
  expect(dispatch.task_input.prompt).toEqual(expect.any(String));
  const prompt = dispatch.task_input.prompt as string;
  expect(prompt.length).toBeLessThanOrEqual(32_000);
  expect(prompt).toContain(`Project ID: "${PROJECT_ID}"`);
  expect(prompt).toContain(ISSUE_ID);
  expect(prompt).toContain(issue.title);
  expect(prompt).toContain(issue.description);
  expect(prompt).toContain("untrusted task data");
  expect(prompt).toContain("Use only tools and permissions already granted by AIAT");
});

test("issue dispatch stays unavailable when active workers have no selected adapter", async ({ page }) => {
  const submissions: unknown[] = [];
  await installProjectMocks(
    page,
    [
      { ...activeWorker, active_adapter_id: null },
      { ...activeWorker, id: "20000000-0000-4000-8000-000000000007", status: "INACTIVE" },
    ],
    async (route) => {
      submissions.push(route.request().postDataJSON());
      await json(route, 500, { error: "Unexpected dispatch in no-worker test." });
    },
  );

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("button", { name: "Manage issue" }).click();
  await expect(page.getByText("No active workers with a selected adapter are available.")).toBeVisible();
  await page.getByRole("checkbox", { name: /may spend the project budget/i }).check();
  await expect(page.getByRole("button", { name: "Start work" })).toBeDisabled();
  expect(submissions).toHaveLength(0);
});

test("project approval submits the exact selected gate ID", async ({ page }) => {
  const olderGate = "70000000-0000-4000-8000-000000000007";
  const newerGate = "70000000-0000-4000-8000-000000000008";
  const submitted: Array<Record<string, unknown>> = [];

  await installProjectMocks(page, [activeWorker], async (route) => {
    await json(route, 500, { detail: "Worker dispatch is not part of this approval test." });
  }, {
    pendingDecisions: [
      { id: olderGate, project_id: PROJECT_ID, gate_type: "Earlier approval", status: "PENDING", created_at: "2026-10-07T17:00:00Z" },
      { id: newerGate, project_id: PROJECT_ID, gate_type: "Later approval", status: "PENDING", created_at: "2026-10-07T17:01:00Z" },
    ],
    onDecision: async (route) => {
      submitted.push(route.request().postDataJSON() as Record<string, unknown>);
      await json(route, 200, { status: "decision_recorded", gate_id: olderGate });
    },
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("tab", { name: /^Workflow/ }).click();
  await expect(page.getByRole("heading", { name: "Pending Decisions (2)" })).toBeVisible();
  await page.getByTestId(`project-decision-${olderGate}`).getByRole("button", { name: "Approve" }).click();

  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toMatchObject({ gate_id: olderGate, decision: "APPROVED" });
  expect(submitted[0].gate_id).not.toBe(newerGate);
});
