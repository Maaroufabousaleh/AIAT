import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

test("system status retains the last known state when a refresh fails", async ({
  page,
}) => {
  let requestCount = 0;
  await page.route("**/api/system/status", async (route) => {
    requestCount += 1;
    if (requestCount === 3) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "system status fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "running",
        uptime_seconds: 123,
        active_projects: 2,
        scheduled_shutdown: null,
        scheduled_resume: null,
        paused_reason: null,
      }),
    });
  });

  await authenticate(page, "/system");
  await expect(page.getByRole("heading", { name: "System Control" })).toBeVisible();
  await expect(page.getByText("running", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Refresh system status" }).click();
  await expect(page.getByText("Showing last known system status")).toBeVisible();
  await expect(page.getByText(/latest system status refresh failed/i)).toBeVisible();
  await expect(page.getByText("running", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known system status")).toHaveCount(0);
  await expect(page.getByText("running", { exact: true })).toBeVisible();
});
