import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const SOURCE_FLOW = {
  id: "flow-legacy-migration",
  name: "Legacy QA Flow",
  description: "A saved flow with a legacy task assignment.",
  version: 1,
  created_by: "e2e",
  is_active: true,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  definition_json: {
    schema_version: "aiat.flow-node-schemas.v1.0",
    nodes: [
      { id: "start", type: "start", label: "Start", config: {}, position: { x: 80, y: 80 } },
      {
        id: "legacy-task",
        type: "task",
        label: "Run QA tests",
        config: { team_id: "dept_qa", action: "test.run" },
        position: { x: 240, y: 80 },
      },
      { id: "end", type: "end", label: "End", config: {}, position: { x: 400, y: 80 } },
    ],
    edges: [
      { id: "start-task", source: "start", target: "legacy-task" },
      { id: "task-end", source: "legacy-task", target: "end" },
    ],
  },
};

const WORKER_ID = "11111111-1111-4111-8111-111111111111";
const MIGRATED_FLOW = {
  ...SOURCE_FLOW,
  id: "flow-legacy-migration-v2",
  name: "Legacy QA Flow",
  version: 2,
  is_active: false,
  definition_json: {
    ...SOURCE_FLOW.definition_json,
    nodes: SOURCE_FLOW.definition_json.nodes.map((node) => node.id === "legacy-task"
      ? { ...node, config: { worker_id: WORKER_ID, task_type: "test.run", model_mode: "none" } }
      : node),
  },
};

test("operator previews and applies legacy flow migration as a new inactive version", async ({ page }) => {
  const migrationRequests: Array<{ dry_run?: boolean; worker_bindings?: Record<string, string>; is_active?: boolean }> = [];
  let sourceReadCount = 0;
  const sourceMutationRequests: string[] = [];
  const executionRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/flows/flow-legacy-migration" && request.method() !== "GET") {
      sourceMutationRequests.push(`${request.method()} ${url.pathname}`);
    }
    if (url.pathname.includes("/api/flows/instances") || url.pathname.includes("/api/flows/execute")) {
      executionRequests.push(`${request.method()} ${url.pathname}`);
    }
  });

  await page.route("**/api/workers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: WORKER_ID, name: "OpenCode QA", status: "ACTIVE", adapter_type: "opencode" }]),
    });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flows/flow-legacy-migration/migrate-legacy-tasks", async (route) => {
    const request = route.request().postDataJSON() as { dry_run?: boolean; worker_bindings?: Record<string, string>; is_active?: boolean };
    migrationRequests.push(request);
    if (request.dry_run) {
      const hasBinding = request.worker_bindings?.["legacy-task"] === WORKER_ID;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "dry_run",
          source_flow_id: SOURCE_FLOW.id,
          source_flow_version: SOURCE_FLOW.version,
          valid: hasBinding,
          changed: true,
          definition_json: SOURCE_FLOW.definition_json,
          migrated_node_ids: hasBinding ? ["legacy-task"] : [],
          missing_worker_bindings: hasBinding ? [] : ["legacy-task"],
          unknown_worker_bindings: [],
          unknown_model_profile_bindings: [],
          errors: hasBinding ? [] : [{ code: "WORKER_BINDING_REQUIRED", node_id: "legacy-task", message: "every task without worker_id requires an explicit worker binding" }],
          findings_before: [],
          findings_after: [],
        }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "migrated",
        source_flow_id: SOURCE_FLOW.id,
        migration: {
          schema: "aiat.flow-legacy-task-migration.v1",
          source_flow_version: SOURCE_FLOW.version,
          migrated_node_ids: ["legacy-task"],
        },
        flow: MIGRATED_FLOW,
      }),
    });
  });
  await page.route("**/api/flows/flow-legacy-migration", async (route) => {
    sourceReadCount += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SOURCE_FLOW) });
  });

  await authenticate(page, "/flows/flow-legacy-migration");
  const migration = page.getByTestId("legacy-flow-migration");
  await expect(migration).toBeVisible();
  await expect(page.getByTestId("flow-name-input")).toHaveValue(SOURCE_FLOW.name);

  await page.getByTestId("legacy-migration-preview-button").click();
  await expect(page.getByTestId("legacy-migration-preview")).toContainText("Missing worker bindings: legacy-task");
  await expect(page.getByTestId("legacy-migration-apply-button")).toBeDisabled();

  await page.getByTestId("legacy-worker-binding-legacy-task").selectOption(WORKER_ID);
  await expect(migration).toContainText("Bindings changed; run the dry-run again before applying.");
  await page.getByTestId("legacy-migration-preview-button").click();
  await expect(page.getByTestId("legacy-migration-preview")).toContainText("Dry-run passed");
  await expect(page.getByTestId("legacy-migration-apply-button")).toBeEnabled();
  await page.getByTestId("legacy-migration-apply-button").click();

  const result = page.getByTestId("legacy-migration-result");
  await expect(result).toContainText("Source flow v1 remains unchanged");
  await expect(result).toContainText("New inactive version v2");
  await expect(page.getByTestId("legacy-migration-open-version")).toHaveAttribute("href", "/flows/flow-legacy-migration-v2");
  await expect(page.getByText("v1 · Updated")).toBeVisible();

  expect(sourceReadCount).toBe(1);
  expect(SOURCE_FLOW.definition_json.nodes[1].config).toEqual({ team_id: "dept_qa", action: "test.run" });
  expect(migrationRequests).toHaveLength(3);
  expect(migrationRequests[0]).toMatchObject({ dry_run: true, worker_bindings: {}, is_active: false });
  expect(migrationRequests[1]).toMatchObject({ dry_run: true, worker_bindings: { "legacy-task": WORKER_ID }, is_active: false });
  expect(migrationRequests[2]).toMatchObject({ dry_run: false, worker_bindings: { "legacy-task": WORKER_ID }, is_active: false });
  expect(sourceMutationRequests).toEqual([]);
  expect(executionRequests).toEqual([]);
});
