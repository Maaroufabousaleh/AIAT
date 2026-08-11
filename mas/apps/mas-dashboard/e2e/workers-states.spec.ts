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
  const row = page.getByRole("row", { name: /worker-states/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("Active", { exact: true })).toBeVisible();

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
