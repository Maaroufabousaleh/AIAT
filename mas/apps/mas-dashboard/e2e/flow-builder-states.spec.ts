import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

async function mockHealthyCatalogues(page: Page) {
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flow-templates", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ templates: [] }),
    });
  });
}

test("new flow hides all editing controls when a catalogue read is denied", async ({
  page,
}) => {
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "worker catalogue access denied" }),
    });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flow-templates", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ templates: [] }),
    });
  });

  await authenticate(page, "/flows/new");

  const access = page.getByRole("region", { name: "Flow creation access status" });
  await expect(access).toBeVisible();
  await expect(access.getByRole("heading", { name: "Flow creation access denied" })).toBeVisible();
  await expect(access).toContainText("governed worker or Model Profile catalogue");
  await expect(page.getByRole("link", { name: "Back to flows list" })).toBeVisible();
  await expect(page.getByTestId("flow-save-button")).toHaveCount(0);
  await expect(page.getByTestId("flow-dry-run-button")).toHaveCount(0);
  await expect(page.getByTestId("add-node-task")).toHaveCount(0);
  await expect(page.getByTestId("flow-name-input")).toHaveAttribute("readonly", "");
});

test("new flow enters the denied state when creation loses authorization", async ({
  page,
}) => {
  await mockHealthyCatalogues(page);
  await page.route("**/api/flows", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "flow creation access denied" }),
    });
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("flow-name-input").fill("Denied flow");
  await page.getByTestId("add-node-start").click();
  await page.getByTestId("add-node-end").click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await page.getByTestId("flow-save-button").click();

  const access = page.getByRole("region", { name: "Flow creation access status" });
  await expect(access).toContainText("flow creation boundary denied access");
  await expect(page.getByTestId("flow-save-button")).toHaveCount(0);
  await expect(page.getByTestId("flow-dry-run-button")).toHaveCount(0);
  await expect(page.getByTestId("add-node-task")).toHaveCount(0);
  await expect(page.getByTestId("flow-name-input")).toHaveAttribute("readonly", "");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
});

test("new flow enters the denied state when readiness validation loses authorization", async ({
  page,
}) => {
  await mockHealthyCatalogues(page);
  await page.route("**/api/flows/dry-run", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: "flow validation authorization expired" }),
    });
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("add-node-start").click();
  await page.getByTestId("flow-dry-run-button").click();

  const access = page.getByRole("region", { name: "Flow creation access status" });
  await expect(access).toContainText("flow readiness validation boundary denied access");
  await expect(page.getByTestId("flow-dry-run-button")).toHaveCount(0);
  await expect(page.getByTestId("flow-save-button")).toHaveCount(0);
  await expect(page.getByTestId("add-node-task")).toHaveCount(0);
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
});

test("Research Brief requires bindings, validates, and attaches to a project", async ({
  page,
}) => {
  const taskCreatePayloads: Array<Record<string, unknown>> = [];
  const projectCreatePayloads: Array<Record<string, unknown>> = [];
  let savedFlow: Record<string, unknown> | null = null;
  let createdProject: Record<string, unknown> | null = null;
  let dryRunRequests = 0;
  let dryRunPayload: Record<string, unknown> | null = null;
  const researchTemplate = {
    template_id: "research",
    name: "Research brief",
    description: "Bind a governed worker and any required approved Model Profile to each task before activation.",
    definition_json: {
      schema_version: "1.0",
      metadata: { template_id: "research", evidence_policy: "research" },
      nodes: [
        { id: "start", type: "start", label: "Start", config: {} },
        { id: "research", type: "task", label: "Research", config: { team_id: "legacy-team", action: "research.collect" } },
        { id: "review", type: "approval", label: "Review findings", config: { approver_role: "cio" } },
        { id: "report", type: "task", label: "Write report", config: { team_id: "legacy-team", action: "document.write" } },
        { id: "end", type: "end", label: "Complete", config: {} },
      ],
      edges: [
        { id: "start-research", source: "start", target: "research" },
        { id: "research-review", source: "research", target: "review" },
        { id: "review-report", source: "review", target: "report" },
        { id: "report-end", source: "report", target: "end" },
      ],
    },
  };

  await page.route("**/api/workers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{
        id: "00000000-0000-4000-a000-000000000701",
        name: "Approved research worker",
        status: "ACTIVE",
        adapter_type: "opencode",
      }]),
    });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ profile_id: "approved-research-profile", status: "approved", purpose: "research" }]),
    });
  });
  await page.route("**/api/flow-templates", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ templates: [researchTemplate] }),
    });
  });
  await page.route("**/api/flows**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/flows/dry-run" && request.method() === "POST") {
      dryRunRequests += 1;
      dryRunPayload = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ valid: true, errors: [], warnings: [] }),
      });
      return;
    }
    if (pathname === "/api/flows" && request.method() === "POST") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      taskCreatePayloads.push(payload);
      savedFlow = {
        id: "research-flow-created",
        name: payload.name,
        version: 1,
        is_active: payload.is_active,
        definition_json: payload.definition_json,
      };
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ id: "research-flow-created", name: payload.name }),
      });
      return;
    }
    if (pathname === "/api/flows" && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(savedFlow ? [savedFlow] : []),
      });
      return;
    }
    if (pathname === "/api/flows/research-flow-created" && request.method() === "GET") {
      await route.fulfill({
        status: savedFlow ? 200 : 404,
        contentType: "application/json",
        body: JSON.stringify(savedFlow ?? { detail: "Flow is not available." }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });
  await page.route("**/api/projects**", async (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname !== "/api/projects") {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    if (request.method() === "POST") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      projectCreatePayloads.push(payload);
      createdProject = {
        id: "00000000-0000-4000-a000-000000000703",
        name: String(payload.name ?? ""),
        description: payload.description ?? null,
        state: "INIT",
        created_at: "2026-10-25T12:00:00Z",
        updated_at: "2026-10-25T12:00:00Z",
      };
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(createdProject),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(createdProject ? [createdProject] : []),
    });
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("flow-name-input").fill("Bound research brief");
  await page.getByTestId("template-research").click();
  await expect(page.getByTestId("research-template-binding-checklist")).toContainText("Research");
  await expect(page.getByTestId("research-template-binding-checklist")).toContainText("Write report");
  await expect(page.locator(".react-flow__node")).toHaveCount(5);
  const canvasBounds = await page.locator(".react-flow").first().boundingBox();
  expect(canvasBounds?.width).toBeGreaterThan(150);
  expect(canvasBounds?.height).toBeGreaterThan(200);
  await page.getByLabel("Activate flow on save").check();

  await page.getByTestId("flow-dry-run-button").click();
  await expect(page.getByText(/Bind a governed worker to each Research Brief task/)).toBeVisible();
  await page.getByTestId("flow-save-button").click();
  expect(taskCreatePayloads).toHaveLength(0);
  expect(dryRunRequests).toBe(0);

  await page.getByRole("button", { name: "Configure Research assignment" }).click();
  await page.getByTestId("task-worker-select").first().selectOption("00000000-0000-4000-a000-000000000701");
  await page.getByTestId("task-model-profile-select").first().selectOption("approved-research-profile");
  await expect(page.getByTestId("research-template-binding-checklist")).toContainText("Write report");
  await expect(page.getByTestId("research-template-binding-checklist")).not.toContainText("Research: governed worker required");

  await page.getByRole("button", { name: "Configure Write report assignment" }).click();
  await page.getByTestId("task-worker-select").first().selectOption("00000000-0000-4000-a000-000000000701");
  await page.getByTestId("task-model-profile-select").first().selectOption("approved-research-profile");
  await expect(page.getByTestId("research-template-bindings-complete")).toBeVisible();
  await page.getByTestId("flow-dry-run-button").click();
  await expect(page.getByTestId("flow-dry-run-result")).toBeVisible();
  expect(dryRunRequests).toBe(1);

  const createResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/flows") && response.request().method() === "POST",
  );
  await page.getByTestId("flow-save-button").click();
  expect((await createResponse).status()).toBe(201);
  expect(taskCreatePayloads).toHaveLength(1);
  expect(taskCreatePayloads[0].is_active).toBe(true);
  const definition = taskCreatePayloads[0].definition_json as {
    nodes: Array<{ type: string; config: Record<string, unknown> }>;
  };
  const tasks = definition.nodes.filter((node) => node.type === "task");
  expect(tasks).toHaveLength(2);
  for (const task of tasks) {
    expect(task.config.worker_id).toBe("00000000-0000-4000-a000-000000000701");
    expect(task.config.model_profile_id).toBe("approved-research-profile");
    expect(task.config.team_id).toBeUndefined();
  }
  expect(dryRunPayload).toEqual(expect.objectContaining({ definition_json: taskCreatePayloads[0].definition_json }));

  await page.goto("/projects");
  await page.getByRole("button", { name: "New Project" }).first().click();
  await page.getByPlaceholder("my-project").fill("Research Brief setup project");
  const startWork = page.getByRole("checkbox", {
    name: "Start CEO feasibility after creation",
  });
  await startWork.uncheck();
  await page.locator("select").last().selectOption("research-flow-created");
  await page.getByRole("button", { name: "Create", exact: true }).click();

  await expect(page.getByRole("link", { name: "Research Brief setup project", exact: true })).toBeVisible();
  expect(projectCreatePayloads).toHaveLength(1);
  expect(projectCreatePayloads[0]).toEqual(expect.objectContaining({
    name: "Research Brief setup project",
    flow_id: "research-flow-created",
    start_immediately: false,
  }));
});
