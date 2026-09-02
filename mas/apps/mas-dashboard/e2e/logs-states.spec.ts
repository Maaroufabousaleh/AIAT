import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const NORMAL_LOG = "2026-08-01T00:00:00Z retained log line";
const RECOVERED_LOG = "2026-08-01T00:00:01Z recovered log line";

test("container logs retain the last buffer when a reload fails", async ({
  page,
}) => {
  let requestCount = 0;

  await page.route("**/api/logs/**", async (route) => {
    requestCount += 1;
    const body = requestCount === 2
      ? `data: ${JSON.stringify({ error: "logs fixture unavailable" })}\n\n`
      : `data: ${requestCount === 1 ? NORMAL_LOG : RECOVERED_LOG}\n\n`;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body,
    });
  });

  await authenticate(page, "/logs");
  await expect(page.getByRole("heading", { name: "Container Logs" })).toBeVisible();
  const logs = page.getByRole("main", { name: "Container logs" });
  await expect(logs).toBeVisible();
  await expect(logs.getByRole("region", { name: "Log filters" })).toBeVisible();
  await expect(
    logs.getByRole("region", { name: "Log level color legend" }),
  ).toBeVisible();
  await expect(logs.getByRole("log", { name: /Log output for/ })).toBeVisible();
  await expect(logs.getByRole("region", { name: "Log status" })).toBeVisible();
  for (const control of [
    logs.getByRole("combobox", { name: "Container" }),
    logs.getByRole("combobox", { name: "Tail lines" }),
    logs.getByRole("checkbox", { name: "Follow live output" }),
    logs.getByRole("button", { name: "Load", exact: true }),
    logs.getByRole("button", { name: "Clear log buffer" }),
    logs.getByRole("button", { name: "Copy visible log lines to clipboard" }),
    logs.getByRole("button", { name: "Download visible log lines" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  const levelFilters = logs
    .getByRole("group", { name: "Filter by log level" })
    .getByRole("button");
  await expect(levelFilters).toHaveCount(5);
  for (const filter of await levelFilters.all()) {
    await expect(filter).toHaveCSS("min-height", "44px");
  }
  await expect(
    logs.getByRole("searchbox", { name: "Filter log text" }),
  ).toHaveCSS("min-height", "44px");

  await page.getByRole("button", { name: "Load", exact: true }).click();
  await expect(page.getByText("retained log line", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Load", exact: true }).click();
  await expect(page.getByText("Showing last known logs")).toBeVisible();
  await expect(page.getByText(/latest log refresh failed/i)).toBeVisible();
  await expect(page.getByText("retained log line", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCSS(
    "min-height",
    "44px",
  );

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known logs")).toHaveCount(0);
  await expect(page.getByText("recovered log line", { exact: true })).toBeVisible();
});

test("container logs access denial on first load hides log controls", async ({ page }) => {
  await page.route("**/api/logs/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `data: ${JSON.stringify({ error: "HTTP 403", status: 403 })}\n\n`,
    });
  });

  await authenticate(page, "/logs");
  const logs = page.getByRole("main", { name: "Container logs" });
  await logs.getByRole("button", { name: "Load", exact: true }).click();

  await expect(logs.getByRole("region", { name: "Log access status" })).toBeVisible();
  await expect(logs.getByText("Log access denied", { exact: true })).toBeVisible();
  await expect(
    logs.getByText("No live log data is available while authorization is unavailable."),
  ).toBeVisible();
  await expect(logs.getByRole("combobox", { name: "Container" })).toHaveCount(0);
  await expect(logs.getByRole("combobox", { name: "Tail lines" })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Load", exact: true })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(logs.getByRole("searchbox", { name: "Filter log text" })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Copy visible log lines to clipboard" })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Download visible log lines" })).toHaveCount(0);
  await expect(logs.getByRole("log", { name: /Last known log output for/ })).toBeVisible();
});

test("container logs access denial after a successful read retains lines without controls", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/logs/**", async (route) => {
    requestCount += 1;
    const body = requestCount === 1
      ? `data: ${NORMAL_LOG}\n\n`
      : `data: ${JSON.stringify({ error: "HTTP 401", status: 401 })}\n\n`;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body,
    });
  });

  await authenticate(page, "/logs");
  const logs = page.getByRole("main", { name: "Container logs" });
  await logs.getByRole("button", { name: "Load", exact: true }).click();
  await expect(page.getByText("retained log line", { exact: true })).toBeVisible();

  await logs.getByRole("button", { name: "Load", exact: true }).click();
  await expect(logs.getByRole("region", { name: "Log access status" })).toBeVisible();
  await expect(page.getByText("retained log line", { exact: true })).toBeVisible();
  await expect(page.getByText(/Previously loaded log lines remain visible/)).toBeVisible();
  await expect(logs.getByRole("combobox", { name: "Container" })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Load", exact: true })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Clear log buffer" })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Copy visible log lines to clipboard" })).toHaveCount(0);
  await expect(logs.getByRole("button", { name: "Download visible log lines" })).toHaveCount(0);
  await expect(logs.getByRole("searchbox", { name: "Filter log text" })).toHaveCount(0);
});
