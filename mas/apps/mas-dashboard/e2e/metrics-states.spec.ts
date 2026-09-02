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
  const metrics = page.getByRole("main", { name: "Metrics dashboard" });
  await expect(metrics).toBeVisible();
  await expect(
    metrics.getByRole("region", { name: "Metric summaries" }),
  ).toBeVisible();
  const charts = metrics.getByRole("region", { name: "Metric charts" });
  await expect(charts).toBeVisible();
  const rangeGroup = metrics.getByRole("group", { name: "Time range" });
  await expect(rangeGroup).toBeVisible();
  await expect(rangeGroup.getByRole("button")).toHaveCount(4);
  for (const rangeButton of await rangeGroup.getByRole("button").all()) {
    await expect(rangeButton).toHaveCSS("min-height", "44px");
    await expect(rangeButton).toHaveCSS("min-width", "44px");
  }
  await expect(rangeGroup.getByRole("button", { name: "1h" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    metrics.getByRole("button", { name: "Refresh metrics" }),
  ).toHaveCSS("min-height", "44px");
  for (const chartName of [
    "LLM Calls/min by Model",
    "Tool Calls/min (top 10)",
    "Messages/min by Direction",
    "DLQ Depth by Stream",
    "Budget Exhaustions",
    "Open Circuit Breakers",
  ]) {
    await expect(charts.getByRole("region", { name: chartName })).toBeVisible();
  }
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
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCSS(
    "min-height",
    "44px",
  );

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known metrics")).toHaveCount(0);
  await expect(page.getByText("metrics-e2e-model", { exact: true })).toBeVisible();
});

test("metrics access denial on first load hides metric controls", async ({ page }) => {
  await page.route("**/api/metrics**", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "metrics access denied", status: 403 }),
    });
  });

  await authenticate(page, "/metrics");
  const metrics = page.getByRole("main", { name: "Metrics dashboard" });
  await expect(
    metrics.getByRole("region", { name: "Metrics access status" }),
  ).toBeVisible();
  await expect(metrics.getByText("Metrics access denied", { exact: true })).toBeVisible();
  await expect(
    metrics.getByText("No live metric state is available while authorization is unavailable."),
  ).toBeVisible();
  await expect(metrics.getByRole("group", { name: "Time range" })).toHaveCount(0);
  await expect(metrics.getByRole("button", { name: "Refresh metrics" })).toHaveCount(0);
  await expect(metrics.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(metrics.getByText("No live metrics are available", { exact: true })).toBeVisible();
  await expect(metrics.getByRole("region", { name: "Metric summaries" })).toBeVisible();
  await expect(metrics.getByRole("region", { name: "Metric charts" })).toBeVisible();
});

test("metrics access denial after a successful read retains series without controls", async ({ page }) => {
  await page.route("**/api/metrics**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(METRICS_FIXTURE),
    });
  });

  await authenticate(page, "/metrics");
  const metrics = page.getByRole("main", { name: "Metrics dashboard" });
  await expect(page.getByText("metrics-e2e-model", { exact: true })).toBeVisible();

  await page.unroute("**/api/metrics**");
  await page.route("**/api/metrics**", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: "metrics authorization expired", status: 401 }),
    });
  });
  await metrics.getByRole("button", { name: "Refresh metrics" }).click();

  await expect(
    metrics.getByRole("region", { name: "Metrics access status" }),
  ).toBeVisible();
  await expect(page.getByText("metrics-e2e-model", { exact: true })).toBeVisible();
  await expect(metrics.getByText(/Previously loaded metric series remain visible/)).toBeVisible();
  await expect(metrics.getByRole("group", { name: "Time range" })).toHaveCount(0);
  await expect(metrics.getByRole("button", { name: "Refresh metrics" })).toHaveCount(0);
  await expect(metrics.getByRole("button", { name: "Retry" })).toHaveCount(0);
});
