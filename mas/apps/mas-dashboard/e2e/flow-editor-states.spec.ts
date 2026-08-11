import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const FLOW_FIXTURE = {
  id: "flow-editor-recovery",
  name: "Recovery Flow",
  description: "A flow used to verify editor recovery states.",
  version: 1,
  created_by: "e2e",
  is_active: true,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
  definition_json: {
    schema_version: "aiat.flow-node-schemas.v1.0",
    nodes: [
      { id: "start", type: "start", label: "Start", config: {}, position: { x: 80, y: 80 } },
      { id: "end", type: "end", label: "End", config: {}, position: { x: 280, y: 80 } },
    ],
    edges: [{ id: "edge-start-end", source: "start", target: "end" }],
  },
};

test("flow editor exposes first-load and stale refresh recovery", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flows/flow-editor-recovery", async (route) => {
    requestCount += 1;
    if (requestCount === 1 || requestCount === 3) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "flow fixture unavailable" }),
      });
      return;
    }
    const payload = requestCount >= 4
      ? { ...FLOW_FIXTURE, name: "Recovered Flow", version: 2 }
      : FLOW_FIXTURE;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });

  await authenticate(page, "/flows/flow-editor-recovery");
  await expect(page.getByRole("heading", { name: "Flow unavailable" })).toBeVisible();
  await expect(page.getByText("flow fixture unavailable")).toBeVisible();

  await page.getByTestId("flow-load-retry").click();
  await expect(page.getByTestId("flow-name-input")).toHaveValue("Recovery Flow");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);

  await page.getByRole("button", { name: "Refresh flow" }).click();
  await expect(page.getByTestId("flow-editor-stale")).toBeVisible();
  await expect(page.getByTestId("flow-name-input")).toHaveValue("Recovery Flow");

  await page.getByTestId("flow-editor-stale").getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("flow-editor-stale")).toHaveCount(0);
  await expect(page.getByTestId("flow-name-input")).toHaveValue("Recovered Flow");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  expect(requestCount).toBe(4);
});
