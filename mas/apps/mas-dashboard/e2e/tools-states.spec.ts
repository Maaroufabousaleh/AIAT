import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const TOOLS_FIXTURE = {
  tools: [
    {
      name: "tools-e2e-read",
      description: "Retained after a failed catalogue refresh",
      group: "General",
      circuit_breaker: { state: "CLOSED", failure_count: 0 },
    },
  ],
  health: { tools_registered: 1 },
};

test("tools catalogue retains the last known state when a refresh fails", async ({
  page,
}) => {
  await page.route("**/api/tools", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TOOLS_FIXTURE),
    });
  });

  await authenticate(page, "/tools");
  await expect(page.getByRole("heading", { name: "Tools" })).toBeVisible();
  await expect(page.getByText("tools-e2e-read", { exact: true })).toBeVisible();

  await page.unroute("**/api/tools");
  let failNextRead = true;
  await page.route("**/api/tools", async (route) => {
    if (failNextRead) {
      failNextRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "tools fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TOOLS_FIXTURE),
    });
  });

  await page.getByRole("button", { name: "Refresh tools" }).click();
  await expect(page.getByText("Showing last known tool catalogue")).toBeVisible();
  await expect(page.getByText(/latest tools refresh failed/i)).toBeVisible();
  await expect(page.getByText("tools-e2e-read", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known tool catalogue")).toHaveCount(0);
  await expect(page.getByText("tools-e2e-read", { exact: true })).toBeVisible();
});
