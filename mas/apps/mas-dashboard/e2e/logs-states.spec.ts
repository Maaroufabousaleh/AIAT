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

  await page.getByRole("button", { name: "Load", exact: true }).click();
  await expect(page.getByText("retained log line", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Load", exact: true }).click();
  await expect(page.getByText("Showing last known logs")).toBeVisible();
  await expect(page.getByText(/latest log refresh failed/i)).toBeVisible();
  await expect(page.getByText("retained log line", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known logs")).toHaveCount(0);
  await expect(page.getByText("recovered log line", { exact: true })).toBeVisible();
});
