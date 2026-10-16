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

test("Research Brief requires governed task bindings before readiness or activation", async ({
  page,
}) => {
  const taskCreatePayloads: Array<Record<string, unknown>> = [];
  let dryRunRequests = 0;
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
  await page.route("**/api/flows/dry-run", async (route) => {
    dryRunRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ valid: true, errors: [], warnings: [] }),
    });
  });
  await page.route("**/api/flows", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
      return;
    }
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    taskCreatePayloads.push(payload);
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ id: "research-flow-created", name: payload.name }),
    });
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("flow-name-input").fill("Bound research brief");
  await page.getByTestId("template-research").click();
  await expect(page.getByTestId("research-template-binding-checklist")).toContainText("Research");
  await expect(page.getByTestId("research-template-binding-checklist")).toContainText("Write report");
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
});
