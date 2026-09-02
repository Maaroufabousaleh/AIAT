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
        scheduled_shutdown: "0 22 * * *",
        scheduled_resume: "0 8 * * *",
        paused_reason: null,
      }),
    });
  });

  await authenticate(page, "/system");
  await expect(page.getByRole("heading", { name: "System Control" })).toBeVisible();
  const system = page.getByRole("main", { name: "System Control" });
  await expect(system).toBeVisible();
  await expect(system.getByRole("status", { name: "System runtime status" })).toBeVisible();
  for (const region of [
    system.getByRole("region", { name: "Scheduled system events" }),
    system.getByRole("region", { name: "System runtime controls" }),
    system.getByRole("region", { name: "Shutdown system" }),
    system.getByRole("region", { name: "Resume system" }),
    system.getByRole("region", { name: "System schedule" }),
  ]) {
    await expect(region).toBeVisible();
  }
  for (const control of [
    system.getByRole("button", { name: "Refresh system status" }),
    system.getByRole("button", { name: "Shutdown the MAS runtime" }),
    system.getByRole("button", { name: "Resume the MAS runtime" }),
    system.getByLabel("Shutdown cron"),
    system.getByLabel("Resume cron"),
    system.getByRole("button", { name: "Save cron schedule" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
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

test("system control exposes a first-load access-denied state without retry", async ({
  page,
}) => {
  await page.route("**/api/system/status", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "system control access denied" }),
    });
  });

  await authenticate(page, "/system");
  const system = page.getByRole("main", { name: "System Control" });
  const status = system.getByRole("region", {
    name: "System Control access status",
  });
  await expect(status).toBeVisible();
  await expect(
    status.getByRole("heading", { name: "System Control access denied" }),
  ).toBeVisible();
  await expect(status.getByText(/not authorized to read or control the runtime/i)).toBeVisible();
  await expect(
    status.getByRole("link", { name: "Return to dashboard" }),
  ).toHaveCSS("min-height", "44px");
  await expect(system.getByRole("button", { name: "Refresh system status" })).toHaveCount(0);
  await expect(system.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(system.getByRole("region", { name: "System runtime controls" })).toHaveCount(0);
  await expect(system.getByRole("region", { name: "System schedule" })).toHaveCount(0);
});

test("system control hides mutations when access is lost after a successful read", async ({
  page,
}) => {
  let requestCount = 0;
  await page.route("**/api/system/status", async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "running", uptime_seconds: 42, active_projects: 1 }),
      });
      return;
    }
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "system control access revoked" }),
    });
  });

  await authenticate(page, "/system");
  const system = page.getByRole("main", { name: "System Control" });
  await expect(system.getByText("running", { exact: true })).toBeVisible();
  await expect(system.getByRole("region", { name: "System runtime controls" })).toBeVisible();

  await system.getByRole("button", { name: "Refresh system status" }).click();
  const status = system.getByRole("region", { name: "System Control access status" });
  await expect(status).toBeVisible();
  await expect(status.getByText(/last known status remains visible/i)).toBeVisible();
  await expect(system.getByText("running", { exact: true })).toBeVisible();
  await expect(system.getByRole("button", { name: "Refresh system status" })).toHaveCount(0);
  await expect(system.getByRole("button", { name: "Shutdown the MAS runtime" })).toHaveCount(0);
  await expect(system.getByRole("button", { name: "Resume the MAS runtime" })).toHaveCount(0);
  await expect(system.getByRole("region", { name: "System schedule" })).toHaveCount(0);
});
