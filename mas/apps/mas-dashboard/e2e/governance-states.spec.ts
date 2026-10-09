import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

async function setupGuidedPrivilegedActionPage(
  page: Page,
  onSubmit: (body: Record<string, unknown>) => void,
) {
  const unexpectedApiRequests: string[] = [];
  const externalOrigins: string[] = [];
  const appOrigin = new URL(process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4000").origin;
  page.on("request", (request) => {
    const origin = new URL(request.url()).origin;
    if (origin !== appOrigin) externalOrigins.push(origin);
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/projects" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/workers" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/governance/model-profiles/catalogue" && request.method() === "GET") {
      await route.fulfill({ json: { schema_version: "guided-actions-e2e-v1", entries: [] } });
      return;
    }
    if (path.startsWith("/api/governance/") && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/executive/actions/ceo/privileged-actions" && request.method() === "POST") {
      onSubmit(request.postDataJSON() as Record<string, unknown>);
      const body = request.postDataJSON() as { action?: string };
      const recordId = "70000000-0000-4000-8000-000000000003";
      await route.fulfill({
        json: {
          schema_version: "aiat.executive-action.v1",
          result: { decision: "pending_approval", record_id: recordId, risk: body.action === "system.restart" ? "critical" : "high" },
          evidence: { schema_version: "aiat.executive-action.v1", action: body.action, record_id: recordId },
        },
      });
      return;
    }
    unexpectedApiRequests.push(`${request.method()} ${path}`);
    await route.fulfill({ status: 501, json: { error: "Unexpected API request in guided action fixture." } });
  });
  await authenticate(page, "/governance");
  return { unexpectedApiRequests, externalOrigins };
}

test("governance retains the last known state when a refresh fails", async ({
  page,
}) => {
  let catalogueRequestCount = 0;

  await page.route((url) => url.pathname === "/api/projects", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "10000000-0000-4000-8000-000000000001", name: "E2E Project", state: "ACTIVE" },
      ]),
    });
  });

  await page.route((url) => url.pathname === "/api/workers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "20000000-0000-4000-8000-000000000002", worker_id: "governance-e2e-worker", name: "Governance E2E worker", status: "ACTIVE" },
      ]),
    });
  });

  await page.route(
    (url) => url.pathname === "/api/governance/model-profiles",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            profile_id: "governance-e2e-profile",
            purpose: "E2E governance profile",
            status: "APPROVED",
            versions: [
              {
                version: "v1",
                provider_id: "e2e-provider",
                exact_model_id: "e2e-model",
                status: "APPROVED",
              },
            ],
          },
        ]),
      });
    },
  );

  await page.route(
    (url) => url.pathname === "/api/governance/model-profiles/catalogue",
    async (route) => {
      catalogueRequestCount += 1;
      if (catalogueRequestCount === 2) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "catalogue fixture unavailable" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "e2e-governance-v1",
          registry_model_count: 1,
          profile_count: 1,
          profile_version_count: 1,
          covered_profile_version_count: 1,
          profile_pending_model_count: 0,
          entries: [
            {
              model_id: "e2e-model",
              provider_id: "e2e-provider",
              profile_state: "approved_profile_present",
            },
          ],
        }),
      });
    },
  );

  await page.route(
    (url) => url.pathname === "/api/governance/runs",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "governance-e2e-run",
            worker_id: "governance-e2e-worker",
            task_type: "governance.check",
            state: "SUCCEEDED",
            model_resolution_snapshot_id: "snapshot-e2e",
          },
        ]),
      });
    },
  );

  await page.route(
    (url) => url.pathname === "/api/governance/stewards",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "governance-e2e-steward",
            worker_id: "governance-e2e-worker",
            status: "READY",
            monitoring_cadence: "daily",
            candidate_count: 0,
          },
        ]),
      });
    },
  );

  await authenticate(page, "/governance");
  await expect(page.getByRole("heading", { name: "Governance" })).toBeVisible();
  const governance = page.getByRole("main", { name: "Governance" });
  await expect(governance).toBeVisible();
  for (const region of [
    governance.getByRole("region", { name: "Governance read surfaces" }),
    governance.getByRole("region", { name: "Executive actions" }),
    governance.getByRole("region", { name: "Model Profiles" }),
    governance.getByRole("region", { name: "Recent WorkerRuns" }),
    governance.getByRole("region", { name: "External Worker Stewards" }),
    governance.getByRole("region", { name: "Runtime Model Catalogue" }),
  ]) {
    await expect(region).toBeVisible();
  }
  for (const control of [
    governance.getByRole("button", { name: "Refresh governance" }),
    governance.getByRole("combobox", { name: "Project", exact: true }),
    governance.getByRole("combobox", { name: "Model Profile", exact: true }),
    governance.getByLabel("Reason"),
    governance.getByRole("combobox", { name: "Worker", exact: true }),
    governance.getByLabel("Task type"),
    governance.getByLabel("Idempotency key"),
    governance.getByRole("combobox", { name: "Project (optional)", exact: true }),
    governance.getByLabel("Maximum run cost (USD, optional)"),
    governance.getByLabel("Execution timeout (seconds, optional)"),
    governance.getByRole("checkbox"),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  const executiveActions = governance.getByRole("region", { name: "Executive actions" });
  await executiveActions.getByText("Advanced task input (JSON object)").click();
  await executiveActions.getByText("Advanced action and payload").click();
  for (const control of [
    executiveActions.getByLabel("Task input (JSON object)"),
    executiveActions.getByLabel("Action", { exact: true }),
    executiveActions.getByLabel("Payload (JSON object)"),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  for (const action of [
    executiveActions.getByRole("button", { name: "Request override" }),
    executiveActions.getByRole("button", { name: "Dispatch governed run" }),
    executiveActions.getByRole("button", { name: "Request privileged action" }),
  ]) {
    await expect(action).toHaveCSS("min-height", "44px");
  }
  const workerRunsTable = governance.getByRole("table", { name: "Recent governed worker runs" });
  await expect(workerRunsTable).toBeVisible();
  for (const header of await workerRunsTable.getByRole("columnheader").all()) {
    await expect(header).toHaveAttribute("scope", "col");
  }
  const modelProfiles = governance.getByRole("region", { name: "Model Profiles" });
  const workerStewards = governance.getByRole("region", { name: "External Worker Stewards" });
  await expect(modelProfiles.getByText("governance-e2e-profile")).toBeVisible();
  await expect(page.getByText("e2e-governance-v1")).toBeVisible();
  await expect(workerStewards.getByText("governance-e2e-worker", { exact: true })).toBeVisible();

  await governance.getByRole("button", { name: "Refresh governance" }).click();
  await expect(page.getByText("Showing last known governance state")).toBeVisible();
  await expect(page.getByText(/latest refresh failed/i)).toBeVisible();
  await expect(modelProfiles.getByText("governance-e2e-profile")).toBeVisible();
  await expect(page.getByText("e2e-governance-v1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known governance state")).toHaveCount(0);
  await expect(modelProfiles.getByText("governance-e2e-profile")).toBeVisible();
});

test("executive action forms validate structured requests before sending", async ({ page }) => {
  const submitted: Array<{ path: string; body: Record<string, unknown> }> = [];
  await page.route("**/api/governance/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/catalogue")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "governance-actions-e2e-v1",
          registry_model_count: 0,
          profile_count: 0,
          profile_version_count: 0,
          covered_profile_version_count: 0,
          profile_pending_model_count: 0,
          entries: [],
        }),
      });
      return;
    }
    if (path.endsWith("/model-profiles")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { profile_id: "governance-action-profile", purpose: "Action test profile", status: "APPROVED" },
        ]),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route((url) => url.pathname === "/api/projects", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "10000000-0000-4000-8000-000000000001", name: "E2E Project", state: "ACTIVE" },
      ]),
    });
  });
  await page.route((url) => url.pathname === "/api/workers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "20000000-0000-4000-8000-000000000002", worker_id: "governance-e2e-worker", name: "Governance E2E worker", status: "ACTIVE" },
        { id: "not-a-uuid", worker_id: "governance-e2e-invalid-worker", name: "Malformed worker fixture", status: "ACTIVE" },
      ]),
    });
  });
  await page.route("**/api/executive/actions/**", async (route) => {
    submitted.push({
      path: new URL(route.request().url()).pathname,
      body: route.request().postDataJSON() as Record<string, unknown>,
    });
    await route.fulfill({
      status: route.request().url().includes("worker-runs") ? 202 : 200,
      contentType: "application/json",
      body: JSON.stringify({ schema: "aiat.executive-action.v1", result: { decision: "pending_approval" } }),
    });
  });

  await authenticate(page, "/governance");
  const actions = page.getByRole("region", { name: "Executive actions" });

  await actions.getByRole("combobox", { name: "Worker", exact: true }).selectOption("20000000-0000-4000-8000-000000000002");
  await actions.getByLabel("Task type").fill("code.edit");
  const idempotencyInput = actions.getByLabel("Idempotency key");
  await expect(idempotencyInput).not.toHaveValue("");
  const generatedKey = await idempotencyInput.inputValue();
  expect(generatedKey).toBeTruthy();
  await actions.getByRole("combobox", { name: "Project (optional)", exact: true }).selectOption("10000000-0000-4000-8000-000000000001");
  const runBudget = actions.getByLabel("Maximum run cost (USD, optional)");
  await runBudget.fill("0");
  expect(await runBudget.evaluate((element) => (element as HTMLInputElement).checkValidity())).toBe(false);
  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect.poll(() => submitted.length).toBe(0);
  await runBudget.fill("");
  const runTimeout = actions.getByLabel("Execution timeout (seconds, optional)");
  await runTimeout.fill("1.5");
  expect(await runTimeout.evaluate((element) => (element as HTMLInputElement).checkValidity())).toBe(false);
  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect.poll(() => submitted.length).toBe(0);
  await runTimeout.fill("");
  await actions.getByText("Advanced task input (JSON object)").click();
  await actions.getByLabel("Task input (JSON object)").fill("[\"not an object\"]");
  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect(page.getByRole("status", { name: "Executive action result" })).toContainText("Task input must be a JSON object.");
  await expect.poll(() => submitted.length).toBe(0);

  await actions.getByRole("combobox", { name: "Worker", exact: true }).selectOption("not-a-uuid");
  await actions.getByLabel("Task input (JSON object)").fill("{}");
  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect(page.getByRole("status", { name: "Executive action result" })).toContainText("selected worker ID is invalid");
  await expect.poll(() => submitted.length).toBe(0);

  await actions.getByRole("combobox", { name: "Worker", exact: true }).selectOption("20000000-0000-4000-8000-000000000002");
  await actions.getByLabel("Task input (JSON object)").fill('{"issue":"Fix the failing test"}');
  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toMatchObject({
    path: "/api/executive/actions/cto/worker-runs",
    body: {
      requested_by: "office_cto",
      dispatch: {
        worker_id: "20000000-0000-4000-8000-000000000002",
        idempotency_key: generatedKey,
        task_type: "code.edit",
        task_input: { issue: "Fix the failing test" },
        project_id: "10000000-0000-4000-8000-000000000001",
      },
    },
  });

  await actions.getByText("Advanced action and payload").click();
  await actions.getByLabel("Action", { exact: true }).fill("security.override_cso");
  await actions.getByLabel("Payload (JSON object)").fill("[]");
  const privilegedAction = actions.getByRole("button", { name: "Request privileged action" });
  await expect(privilegedAction).toBeDisabled();
  await actions.getByRole("checkbox").check();
  await privilegedAction.click();
  await expect(page.getByRole("status", { name: "Executive action result" })).toContainText("Payload must be a JSON object.");
  await expect.poll(() => submitted.length).toBe(1);

  await actions.getByLabel("Payload (JSON object)").fill('{"ticket":"SEC-12"}');
  await privilegedAction.click();
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1]).toMatchObject({
    path: "/api/executive/actions/ceo/privileged-actions",
    body: {
      requested_by: "exec_ceo",
      action: "security.override_cso",
      payload: { ticket: "SEC-12" },
    },
  });
});

test("executive action forms submit canonical choices without copied IDs", async ({ page }) => {
  const submitted: Array<{ path: string; body: Record<string, unknown> }> = [];
  await page.route("**/api/governance/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/catalogue")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ schema_version: "e2e", registry_model_count: 0, profile_count: 0, profile_version_count: 0, covered_profile_version_count: 0, profile_pending_model_count: 0, entries: [] }),
      });
      return;
    }
    if (path.endsWith("/model-profiles")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { profile_id: "governance-select-profile", purpose: "Selectable E2E profile", status: "APPROVED" },
        ]),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route((url) => url.pathname === "/api/projects", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "10000000-0000-4000-8000-000000000001", name: "E2E delivery project", state: "ACTIVE" },
      ]),
    });
  });
  await page.route((url) => url.pathname === "/api/workers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "20000000-0000-4000-8000-000000000002", worker_id: "governance-e2e-worker", name: "Governance E2E worker", status: "ACTIVE" },
      ]),
    });
  });
  await page.route("**/api/executive/actions/**", async (route) => {
    submitted.push({
      path: new URL(route.request().url()).pathname,
      body: route.request().postDataJSON() as Record<string, unknown>,
    });
    await route.fulfill({
      status: route.request().url().includes("worker-runs") ? 202 : 201,
      contentType: "application/json",
      body: JSON.stringify({ schema: "aiat.executive-action.v1", result: { decision: "pending_approval" } }),
    });
  });

  await authenticate(page, "/governance");
  const actions = page.getByRole("region", { name: "Executive actions" });
  const projectSelect = actions.getByRole("combobox", { name: "Project", exact: true });
  const profileSelect = actions.getByRole("combobox", { name: "Model Profile", exact: true });
  const workerSelect = actions.getByRole("combobox", { name: "Worker", exact: true });
  await expect(projectSelect).toHaveJSProperty("tagName", "SELECT");
  await expect(profileSelect).toHaveJSProperty("tagName", "SELECT");
  await expect(workerSelect).toHaveJSProperty("tagName", "SELECT");
  await projectSelect.selectOption("10000000-0000-4000-8000-000000000001");
  await profileSelect.selectOption("governance-select-profile");
  await actions.getByLabel("Reason").fill("Use the approved profile for this project.");
  await actions.getByRole("button", { name: "Request override" }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toMatchObject({
    path: "/api/executive/actions/cfo/model-overrides",
    body: {
      project_id: "10000000-0000-4000-8000-000000000001",
      requested_profile_id: "governance-select-profile",
      requested_by: "office_cfo",
      reason: "Use the approved profile for this project.",
    },
  });

  await workerSelect.selectOption("20000000-0000-4000-8000-000000000002");
  await actions.getByLabel("Task type").fill("code.edit");
  await actions.getByLabel("Maximum run cost (USD, optional)").fill("2.5");
  await actions.getByLabel("Execution timeout (seconds, optional)").fill("90");
  const requestKey = await actions.getByLabel("Idempotency key").inputValue();
  expect(requestKey).toBeTruthy();
  await actions.getByRole("combobox", { name: "Project (optional)", exact: true }).selectOption("10000000-0000-4000-8000-000000000001");
  await actions.getByText("Advanced task input (JSON object)").click();
  await actions.getByLabel("Task input (JSON object)").fill('{"issue":"Fix the failing test"}');
  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1]).toMatchObject({
    path: "/api/executive/actions/cto/worker-runs",
    body: {
      requested_by: "office_cto",
      dispatch: {
        worker_id: "20000000-0000-4000-8000-000000000002",
        idempotency_key: requestKey,
        task_type: "code.edit",
        task_input: { issue: "Fix the failing test" },
        project_id: "10000000-0000-4000-8000-000000000001",
        budget_usd: 2.5,
        timeout_seconds: 90,
      },
    },
  });
});

test("executive action forms reject malformed and stale option IDs before submission", async ({ page }) => {
  const submitted: string[] = [];
  let useRefreshedOptions = false;
  const currentProject = "10000000-0000-4000-8000-000000000001";
  const refreshedProject = "10000000-0000-4000-8000-000000000003";
  const currentWorker = "20000000-0000-4000-8000-000000000002";
  const refreshedWorker = "20000000-0000-4000-8000-000000000004";

  await page.route("**/api/governance/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/catalogue")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: "e2e", registry_model_count: 0, profile_count: 0, profile_version_count: 0, covered_profile_version_count: 0, profile_pending_model_count: 0, entries: [] }) });
      return;
    }
    if (path.endsWith("/model-profiles")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ profile_id: "governance-stale-test-profile", purpose: "Stale ID test", status: "APPROVED" }]) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route((url) => url.pathname === "/api/projects", async (route) => {
    const projects = useRefreshedOptions
      ? [{ id: refreshedProject, name: "Current project", state: "ACTIVE" }]
      : [
        { id: currentProject, name: "Selected project", state: "ACTIVE" },
        { id: "not-a-uuid", name: "Malformed project", state: "ACTIVE" },
        { id: refreshedProject, name: "Current project", state: "ACTIVE" },
      ];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(projects) });
  });
  await page.route((url) => url.pathname === "/api/workers", async (route) => {
    const workers = useRefreshedOptions
      ? [{ id: refreshedWorker, worker_id: "current-worker", name: "Current worker", status: "ACTIVE" }]
      : [
        { id: currentWorker, worker_id: "selected-worker", name: "Selected worker", status: "ACTIVE" },
        { id: "not-a-uuid", worker_id: "malformed-worker", name: "Malformed worker", status: "ACTIVE" },
        { id: refreshedWorker, worker_id: "current-worker", name: "Current worker", status: "ACTIVE" },
      ];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(workers) });
  });
  await page.route("**/api/executive/actions/**", async (route) => {
    submitted.push(new URL(route.request().url()).pathname);
    await route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
  });

  await authenticate(page, "/governance");
  const actions = page.getByRole("region", { name: "Executive actions" });
  const projectSelect = actions.getByRole("combobox", { name: "Project", exact: true });
  const profileSelect = actions.getByRole("combobox", { name: "Model Profile", exact: true });
  const workerSelect = actions.getByRole("combobox", { name: "Worker", exact: true });
  await profileSelect.selectOption("governance-stale-test-profile");
  await actions.getByLabel("Reason").fill("Verify option IDs before submitting.");
  await projectSelect.selectOption("not-a-uuid");
  await actions.getByRole("button", { name: "Request override" }).click();
  await expect(page.getByRole("status", { name: "Executive action result" })).toContainText("selected project ID is invalid");
  await expect.poll(() => submitted.length).toBe(0);

  await workerSelect.selectOption("not-a-uuid");
  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect(page.getByRole("status", { name: "Executive action result" })).toContainText("selected worker ID is invalid");
  await expect.poll(() => submitted.length).toBe(0);

  await projectSelect.selectOption(currentProject);
  await workerSelect.selectOption(currentWorker);
  useRefreshedOptions = true;
  await actions.getByRole("button", { name: "Refresh action choices" }).click();
  await expect(actions.getByRole("option", { name: /Current project/ })).toHaveCount(2);
  await expect(actions.getByRole("option", { name: /Current worker/ })).toHaveCount(1);
  await actions.getByRole("button", { name: "Request override" }).click();
  await expect(page.getByRole("status", { name: "Executive action result" })).toContainText("selected project is no longer in the current project list");
  await expect.poll(() => submitted.length).toBe(0);

  await actions.getByRole("button", { name: "Dispatch governed run" }).click();
  await expect(page.getByRole("status", { name: "Executive action result" })).toContainText("selected worker is no longer in the current worker list");
  await expect.poll(() => submitted.length).toBe(0);
});

test("governance exposes a first-load access-denied state without retry or mutations", async ({
  page,
}) => {
  await page.route("**/api/governance/**", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "governance access denied" }),
    });
  });

  await authenticate(page, "/governance");
  const governance = page.getByRole("main", { name: "Governance" });
  const access = governance.getByRole("region", { name: "Governance access status" });
  await expect(access).toBeVisible();
  await expect(access.getByRole("heading", { name: "Governance access denied" })).toBeVisible();
  await expect(access.getByText(/not authorized to read or change governance/i)).toBeVisible();
  await expect(access.getByRole("link", { name: "Return to dashboard" })).toHaveCSS("min-height", "44px");
  await expect(governance.getByRole("button", { name: "Refresh governance" })).toHaveCount(0);
  await expect(governance.getByRole("button", { name: "Retry" })).toHaveCount(0);
  for (const action of ["Request override", "Dispatch governed run", "Request privileged action"]) {
    await expect(governance.getByRole("button", { name: action })).toHaveCount(0);
  }
  await expect(governance.getByText(/executive action forms are hidden/i)).toBeVisible();
});

test("governance hides mutations when access is lost after a successful read", async ({
  page,
}) => {
  let catalogueRequestCount = 0;
  await page.route(
    (url) => url.pathname.startsWith("/api/governance/"),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/catalogue")) {
        catalogueRequestCount += 1;
        if (catalogueRequestCount > 1) {
          await route.fulfill({
            status: 403,
            contentType: "application/json",
            body: JSON.stringify({ error: "governance access revoked" }),
          });
          return;
        }
      }
      if (path.endsWith("/model-profiles")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              profile_id: "governance-denial-profile",
              purpose: "Denial fixture profile",
              status: "APPROVED",
            },
          ]),
        });
        return;
      }
      if (path.endsWith("/catalogue")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            schema_version: "governance-denial-v1",
            registry_model_count: 1,
            profile_count: 1,
            profile_version_count: 1,
            covered_profile_version_count: 1,
            profile_pending_model_count: 0,
          }),
        });
        return;
      }
      if (path.endsWith("/runs")) {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
    },
  );

  await authenticate(page, "/governance");
  const governance = page.getByRole("main", { name: "Governance" });
  const modelProfiles = governance.getByRole("region", { name: "Model Profiles" });
  await expect(modelProfiles.getByText("governance-denial-profile", { exact: true })).toBeVisible();
  await governance.getByRole("button", { name: "Refresh governance" }).click();

  const access = governance.getByRole("region", { name: "Governance access status" });
  await expect(access).toBeVisible();
  await expect(access.getByText(/last-known read context remains visible/i)).toBeVisible();
  await expect(modelProfiles.getByText("governance-denial-profile", { exact: true })).toBeVisible();
  await expect(governance.getByRole("button", { name: "Refresh governance" })).toHaveCount(0);
  await expect(governance.getByRole("button", { name: "Retry" })).toHaveCount(0);
  for (const action of ["Request override", "Dispatch governed run", "Request privileged action"]) {
    await expect(governance.getByRole("button", { name: action })).toHaveCount(0);
  }
});

test("guided CEO privileged request submits a bounded approved action and reason", async ({ page }) => {
  const submitted: Array<Record<string, unknown>> = [];
  const { unexpectedApiRequests, externalOrigins } = await setupGuidedPrivilegedActionPage(page, (body) => submitted.push(body));
  const actions = page.getByRole("region", { name: "Executive actions" });
  const guidedAction = actions.getByLabel("Approval-gated action");

  await expect(guidedAction.locator('option[value="system.restart"]')).toHaveText("Restart the AIAT system · critical risk");
  await expect(guidedAction.locator('option[value="team.shutdown"]')).toHaveCount(1);
  await expect(guidedAction.locator('option[value="container.stop"]')).toHaveCount(1);
  await expect(guidedAction.locator('option[value="worker.force_stop"]')).toHaveCount(1);
  await expect(guidedAction.locator('option[value="system.wipe"]')).toHaveCount(0);
  await expect(guidedAction.locator('option[value="credentials.export"]')).toHaveCount(0);

  await guidedAction.selectOption("system.restart");
  await actions.getByLabel("Justification and target context").fill("Restart after the approved maintenance window.");
  await actions.getByRole("checkbox").check();
  await actions.getByRole("button", { name: "Request privileged action" }).click();

  await expect(page.getByRole("region", { name: "Privileged action pending approval" })).toBeVisible();
  await expect.poll(() => submitted).toEqual([{
    action: "system.restart",
    requested_by: "exec_ceo",
    payload: { reason: "Restart after the approved maintenance window." },
  }]);
  expect(unexpectedApiRequests).toEqual([]);
  expect(externalOrigins).toEqual([]);
});

test("guided CEO privileged request requires an action and reason before submission", async ({ page }) => {
  const submitted: Array<Record<string, unknown>> = [];
  const { unexpectedApiRequests, externalOrigins } = await setupGuidedPrivilegedActionPage(page, (body) => submitted.push(body));
  const actions = page.getByRole("region", { name: "Executive actions" });
  const request = actions.getByRole("button", { name: "Request privileged action" });
  const guidedAction = actions.getByLabel("Approval-gated action");
  const reason = actions.getByLabel("Justification and target context");

  await reason.fill("Needs a reviewed target and justification.");
  await actions.getByRole("checkbox").check();
  await expect(request).toBeDisabled();
  await guidedAction.selectOption("container.stop");
  await expect(request).toBeEnabled();
  await reason.fill("   ");
  await expect(request).toBeDisabled();
  await expect.poll(() => submitted).toHaveLength(0);

  expect(unexpectedApiRequests).toEqual([]);
  expect(externalOrigins).toEqual([]);
});
