import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const METRICS_FIXTURE = {
  results: [
    {
      metric: {
        model: "metrics-e2e-model",
        tool_name: "metrics-e2e-tool",
        direction: "inbound",
        stream: "metrics-e2e-stream",
        agent_id: "metrics-e2e-agent",
      },
      values: [[1722470400, "1"]],
      value: [1722470400, "1"],
    },
  ],
};

test("metrics retains the last known series when a refresh partially fails", async ({ page }) => {
  await page.route("**/api/metrics**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(METRICS_FIXTURE),
    });
  });

  await authenticate(page, "/metrics");
  await expect(page.getByRole("heading", { name: "Metrics" })).toBeVisible();
  await expect(page.getByText("metrics-e2e-model", { exact: true })).toBeVisible();

  await page.unroute("**/api/metrics**");
  let failNextRead = true;
  await page.route("**/api/metrics**", async (route) => {
    if (failNextRead) {
      failNextRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "metrics fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(METRICS_FIXTURE),
    });
  });

  await page.getByRole("button", { name: "Refresh metrics" }).click();
  await expect(page.getByText("Showing last known metrics")).toBeVisible();
  await expect(page.getByText(/metrics refresh failed/i)).toBeVisible();
  await expect(page.getByText("metrics-e2e-model", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known metrics")).toHaveCount(0);
  await expect(page.getByText("metrics-e2e-model", { exact: true })).toBeVisible();
});
