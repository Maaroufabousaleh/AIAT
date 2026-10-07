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

async function json(route: Route, status: number, payload: unknown): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

async function installProjectMocks(
  page: Page,
  workers: unknown[],
  onDispatch: (route: Route) => Promise<void>,
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
        path === `/api/projects/${PROJECT_ID}/decisions` ||
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
      await json(route, 200, [issue]);
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/issues/${ISSUE_ID}` && request.method() === "GET") {
      await json(route, 200, { issue, comments: [], links: [] });
      return;
    }
    if (path === "/api/governance/runs") {
      await json(route, 200, []);
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
  expect(submissions[0]).toEqual({
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
  expect(submissions[1]).toEqual(submissions[0]);

  const result = page.getByRole("status", { name: "Worker Run result" });
  await expect(result).toContainText("QUEUED");
  const runLink = result.getByRole("link", { name: `View run and evidence ${RUN_ID}` });
  await expect(runLink).toHaveAttribute("href", `/workers/runs/${RUN_ID}`);
  await expect(page.getByRole("button", { name: "Work already started" })).toBeDisabled();
  expect(submissions).toHaveLength(2);
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
