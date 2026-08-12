import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const WORKER_FIXTURE = [
  {
    id: "worker-states-001",
    worker_id: "worker-states",
    name: "States Worker",
    status: "ACTIVE",
    evaluation_status: "approved",
    team_id: "dept_qa",
    version: "1.0.0",
  },
];

test("hiring board retains workers when refresh fails", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/workers", async (route) => {
    requestCount += 1;
    if (requestCount === 2) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "worker fixture unavailable" }),
      });
      return;
    }

    const workers = requestCount >= 3
      ? [{ ...WORKER_FIXTURE[0], status: "INACTIVE" }]
      : WORKER_FIXTURE;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(workers),
    });
  });

  await authenticate(page, "/workers");
  await expect(page.getByRole("heading", { name: "Hiring Board" })).toBeVisible();
  const hiringBoard = page.getByRole("main", { name: "Hiring Board" });
  await expect(hiringBoard).toBeVisible();
  for (const region of [
    hiringBoard.getByRole("region", { name: "Hiring board adapter policy" }),
    hiringBoard.getByRole("region", { name: "Hiring board integration readiness" }),
    hiringBoard.getByRole("region", { name: "Advanced Runtimes" }),
    hiringBoard.getByRole("region", { name: "Hiring board summary" }),
    hiringBoard.getByRole("region", { name: "Hiring board filters" }),
    hiringBoard.getByRole("region", { name: "Registered workers" }),
  ]) {
    await expect(region).toBeVisible();
  }
  for (const control of [
    hiringBoard.getByRole("button", { name: "Refresh workers" }),
    hiringBoard.getByRole("button", { name: "Register Worker" }),
    hiringBoard.getByRole("searchbox", { name: "Search workers" }),
    hiringBoard.getByRole("checkbox", { name: "Select all workers" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  const statusFilters = hiringBoard
    .getByRole("group", { name: "Filter by status" })
    .getByRole("button");
  await expect(statusFilters).toHaveCount(7);
  for (const filter of await statusFilters.all()) {
    await expect(filter).toHaveCSS("min-height", "44px");
  }
  const row = page.getByRole("row", { name: /worker-states/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("Active", { exact: true })).toBeVisible();
  for (const control of [
    row.getByRole("button", { name: "Deactivate worker-states" }),
    row.getByRole("button", { name: "Drain worker-states" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  await row.focus();
  await row.press("Enter");
  await expect(page.getByText("Integration", { exact: true })).toBeVisible();

  await hiringBoard.getByRole("button", { name: "Register Worker" }).click();
  const dialog = page.getByRole("dialog", { name: "Register Worker" });
  await expect(dialog).toBeVisible();
  for (const field of [
    dialog.getByLabel("Worker ID *"),
    dialog.getByLabel("Name *"),
    dialog.getByLabel("Description"),
    dialog.getByLabel("Team ID"),
    dialog.getByLabel("Transport Mode"),
    dialog.getByLabel("GitHub Repository URL"),
    dialog.getByLabel("Immutable Version Pin"),
    dialog.getByLabel("Adapter Entrypoint"),
    dialog.getByLabel("Sandbox Profile"),
  ]) {
    await expect(field).toHaveCSS("min-height", "44px");
  }
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("button", { name: "Refresh workers" }).click();
  await expect(page.getByText("Showing last known workers")).toBeVisible();
  await expect(page.getByText(/worker fixture unavailable/i)).toBeVisible();
  await expect(row).toBeVisible();
  await expect(row.getByText("Active", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known workers")).toHaveCount(0);
  await expect(row.getByText("Inactive", { exact: true })).toBeVisible();
});
